"""Model backends.

Three implementations behind one interface, selected by SCANNER_BACKEND:

  replay    - previously captured real model output, keyed by diff. Deterministic
              and instant. The stage default on a CPU-only cluster.
  llamacpp  - live inference against the llama.cpp KServe InferenceService.
  vllm      - live inference against a vLLM OpenAI endpoint (GPU).

All three speak the same OpenAI-ish chat completion shape, so adding a fourth
(a hosted model, a different security model) is a new class and nothing else.
"""

from __future__ import annotations

import glob
import hashlib
import json
import logging
import os
import re
import time

import httpx

from .prompts import build_messages
from .schema import ModelResponse

LOG = logging.getLogger("backends")

DEFAULT_REPLAY_DIR = "/app/replay"


def _replay_dir() -> str:
    return os.getenv("REPLAY_DIR", DEFAULT_REPLAY_DIR)


def _request_timeout() -> float:
    return float(os.getenv("MODEL_TIMEOUT_SECONDS", "600"))


def diff_fingerprint(diff: str) -> str:
    """Stable hash of a diff, insensitive to the noise that changes between runs.

    Commit SHAs, index lines and trailing whitespace all vary run to run while
    the substantive change is identical, so they are stripped before hashing.
    Without this the replay cache would miss every time.
    """
    lines = []
    for line in diff.splitlines():
        if line.startswith(("index ", "@@", "diff --git", "--- ", "+++ ")):
            continue
        stripped = line.rstrip()
        if stripped:
            lines.append(stripped)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


# The JSON Schema the model is constrained to. Passed to the inference server
# for grammar-constrained decoding, so malformed output is impossible rather
# than merely unlikely.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "severity": {"enum": ["critical", "high", "medium", "low", "info"]},
                    "confidence": {"enum": ["high", "medium", "low"]},
                    "cwe": {"type": "string"},
                    "owasp": {"type": "string"},
                    "cvss_vector": {"type": "string"},
                    "cvss_score": {"type": "number"},
                    "file": {"type": "string"},
                    "line": {"type": "integer"},
                    "rationale": {"type": "string"},
                    "exploit": {"type": "string"},
                    "fix": {"type": "string"},
                },
                "required": [
                    "title", "severity", "confidence", "cwe", "owasp",
                    "file", "line", "rationale",
                ],
            },
        },
    },
    "required": ["findings", "summary"],
}


class BackendError(RuntimeError):
    """Raised when the model cannot be consulted or its answer is unusable.

    Always propagates to verdict=error, which fails the pipeline. A security
    gate that fails open is decoration.
    """


class Backend:
    name = "abstract"
    model = "unknown"

    def review(self, req) -> tuple[ModelResponse, dict, bool]:
        """Return (parsed response, usage dict, replayed flag)."""
        raise NotImplementedError


class OpenAICompatBackend(Backend):
    """Shared implementation for llama.cpp and vLLM - both speak OpenAI chat."""

    def __init__(self, base_url: str, model: str, api_key: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key

    def _payload(self, messages: list[dict]) -> dict:
        return {
            "model": self.model,
            "messages": messages,
            # Zero temperature. A gate that answers differently on re-run is
            # not a gate.
            "temperature": 0.0,
            "top_p": 1.0,
            "seed": 42,
            "max_tokens": int(os.getenv("MODEL_MAX_TOKENS", "2048")),
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "code_review", "schema": RESPONSE_SCHEMA},
            },
        }

    def review(self, req):
        messages = build_messages(
            req.repository, req.commit, req.base_commit, req.diff, req.files,
            localizer=getattr(req, "localizer", None),
        )
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        url = f"{self.base_url}/v1/chat/completions"
        try:
            with httpx.Client(timeout=_request_timeout()) as client:
                resp = client.post(url, json=self._payload(messages), headers=headers)
                if resp.status_code == 400:
                    # Older servers reject json_schema. Retry with the weaker
                    # json_object mode; the prompt still asks for the shape.
                    LOG.warning("json_schema rejected, retrying with json_object")
                    payload = self._payload(messages)
                    payload["response_format"] = {"type": "json_object"}
                    resp = client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                body = resp.json()
        except httpx.HTTPError as exc:
            raise BackendError(f"model endpoint unreachable: {exc}") from exc

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise BackendError(f"unexpected response shape: {str(body)[:300]}") from exc

        parsed = _parse_model_json(content)
        usage = body.get("usage", {})
        return parsed, usage, False


class LlamaCppBackend(OpenAICompatBackend):
    name = "llamacpp"


class VllmBackend(OpenAICompatBackend):
    name = "vllm"


class ReplayBackend(Backend):
    """Serves captured real model output.

    Entries are JSON files in REPLAY_DIR produced by scripts/capture-replay.sh
    against the live model. This is a recording, never a fabrication - keep that
    distinction if someone in the room asks whether the demo is real.

    Matching is layered because a live typo on stage must not break the demo:
      1. exact diff fingerprint
      2. a `match.contains_all` list of substrings, highest priority first
      3. miss -> delegate to `live` backend if configured, else hard error
    """

    name = "replay"

    def __init__(self, model: str, live: Backend | None = None,
                 replay_dir: str | None = None):
        self.model = model
        self.live = live
        # Resolved here rather than at import time so the directory can be
        # changed by configuration (and by tests) without reloading the module.
        self.replay_dir = replay_dir or _replay_dir()
        self.entries = self._load()
        LOG.info("replay cache: %d entries from %s",
                 len(self.entries), self.replay_dir)

    def _load(self) -> list[dict]:
        entries = []
        for path in sorted(glob.glob(os.path.join(self.replay_dir, "*.json"))):
            try:
                with open(path) as handle:
                    entry = json.load(handle)
                entry.setdefault("_path", path)
                if entry.get("provenance") != "captured":
                    LOG.warning(
                        "replay entry %s is a %s, not a model capture - do not "
                        "present its output as live inference",
                        os.path.basename(path),
                        entry.get("provenance", "unlabelled entry"),
                    )
                entries.append(entry)
            except (OSError, json.JSONDecodeError) as exc:
                LOG.error("skipping bad replay entry %s: %s", path, exc)
        entries.sort(key=lambda e: -int(e.get("match", {}).get("priority", 0)))
        return entries

    def _find(self, req) -> dict | None:
        fingerprint = diff_fingerprint(req.diff)
        for entry in self.entries:
            if entry.get("fingerprint") == fingerprint:
                LOG.info("replay hit by fingerprint: %s", entry["_path"])
                return entry
        haystack = req.diff + "".join(f.content for f in req.files)
        for entry in self.entries:
            needles = entry.get("match", {}).get("contains_all")
            if needles and all(n in haystack for n in needles):
                LOG.info("replay hit by contains_all: %s", entry["_path"])
                return entry
        return None

    def review(self, req):
        entry = self._find(req)
        if entry is None:
            if self.live is not None:
                LOG.warning("replay miss (fp=%s) - falling through to live model",
                            diff_fingerprint(req.diff)[:12])
                return self.live.review(req)
            raise BackendError(
                "no replay entry matches this diff "
                f"(fingerprint {diff_fingerprint(req.diff)[:12]}) and no live "
                "fallback is configured. Capture it with scripts/capture-replay.sh "
                "or set REPLAY_FALLBACK=live."
            )

        # Replay the recorded latency so pacing on stage matches reality,
        # capped so nobody waits three minutes for a recording.
        delay = min(float(entry.get("latency_ms", 0)) / 1000.0,
                    float(os.getenv("REPLAY_MAX_DELAY_SECONDS", "3")))
        if delay > 0:
            time.sleep(delay)

        parsed = ModelResponse.model_validate(entry["response"])
        return parsed, entry.get("usage", {}), True


def _parse_model_json(content: str) -> ModelResponse:
    """Parse the model's JSON, tolerating fences and reasoning preamble.

    Constrained decoding should make this unnecessary. It is here because
    "should" is doing a lot of work in that sentence, and because reasoning
    models like to emit a <think> block first.
    """
    text = content.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        return ModelResponse.model_validate_json(text)
    except Exception:
        pass

    # Last resort: the outermost JSON object in the text.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return ModelResponse.model_validate_json(text[start:end + 1])
        except Exception as exc:
            raise BackendError(f"model output is not valid against schema: {exc}") from exc
    raise BackendError(f"no JSON object in model output: {text[:300]}")


def build_backend() -> Backend:
    """Construct the backend named by SCANNER_BACKEND."""
    choice = os.getenv("SCANNER_BACKEND", "replay").lower()
    model = os.getenv("MODEL_NAME", "fdtn-ai/Foundation-Sec-8B-Reasoning")
    endpoint = os.getenv("MODEL_ENDPOINT", "http://foundation-sec-reasoning-predictor.lightwell-inward.svc.cluster.local")
    api_key = os.getenv("MODEL_API_KEY") or None

    if choice == "llamacpp":
        return LlamaCppBackend(endpoint, model, api_key)
    if choice == "vllm":
        return VllmBackend(endpoint, model, api_key)
    if choice == "replay":
        live = None
        if os.getenv("REPLAY_FALLBACK", "").lower() == "live":
            live = LlamaCppBackend(endpoint, model, api_key)
        return ReplayBackend(model, live)
    raise ValueError(
        f"SCANNER_BACKEND={choice!r} is not one of: replay, llamacpp, vllm"
    )
