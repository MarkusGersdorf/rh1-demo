"""Replay matching and model-output parsing."""

import json

import pytest

from app.backends import (
    BackendError,
    LlamaCppBackend,
    ReplayBackend,
    _parse_model_json,
    build_backend,
    diff_fingerprint,
)
from app.schema import ModelResponse


# --------------------------------------------------------------------------
# replay matching
# --------------------------------------------------------------------------

def write_entry(directory, name, *, fingerprint="deadbeef", priority=0,
                contains=None, findings=None, summary="", provenance="captured"):
    entry = {
        "provenance": provenance,
        "label": name,
        "fingerprint": fingerprint,
        "match": {"priority": priority},
        "latency_ms": 0,
        "response": {"summary": summary, "findings": findings or []},
    }
    if contains:
        entry["match"]["contains_all"] = contains
    (directory / f"{name}.json").write_text(json.dumps(entry))
    return entry


def critical():
    return [{
        "title": "t", "severity": "critical", "confidence": "high",
        "cwe": "CWE-639", "owasp": "A01:2021", "file": "X.java",
        "line": 1, "rationale": "r",
    }]


@pytest.fixture
def replay_dir(tmp_path, monkeypatch):
    """REPLAY_DIR is read when the backend is constructed, so setenv suffices."""
    monkeypatch.setenv("REPLAY_DIR", str(tmp_path))
    monkeypatch.setenv("REPLAY_MAX_DELAY_SECONDS", "0")
    return tmp_path


def test_exact_fingerprint_match(replay_dir, make_request):
    diff = "diff --git a/x b/x\n+int x = 1;\n"
    write_entry(replay_dir, "e1", fingerprint=diff_fingerprint(diff), summary="hit")
    parsed, _usage, replayed = ReplayBackend("m").review(make_request(diff))
    assert replayed is True
    assert parsed.summary == "hit"


def test_contains_all_match_when_fingerprint_differs(replay_dir, make_request):
    """The stage safety net: a typo changes the hash but not the marker."""
    write_entry(replay_dir, "e1", fingerprint="nope",
                contains=["ownership mismatch"], summary="matched by marker")
    req = make_request("diff --git a/x b/x\n+// ownership mismatch here\n")
    parsed, _usage, replayed = ReplayBackend("m").review(req)
    assert replayed is True
    assert parsed.summary == "matched by marker"


def test_contains_all_requires_every_substring(replay_dir, make_request):
    write_entry(replay_dir, "e1", fingerprint="nope",
                contains=["alpha", "beta"], summary="should not match")
    with pytest.raises(BackendError):
        ReplayBackend("m").review(make_request("+alpha only\n"))


def test_contains_all_searches_file_contents_too(replay_dir, make_request):
    write_entry(replay_dir, "e1", fingerprint="nope",
                contains=["FORBIDDEN"], summary="found in file body")
    req = make_request("+trivial\n", files=[("A.java", "return FORBIDDEN;")])
    parsed, _usage, _ = ReplayBackend("m").review(req)
    assert parsed.summary == "found in file body"


def test_higher_priority_entry_wins(replay_dir, make_request):
    write_entry(replay_dir, "low", fingerprint="x", priority=1,
                contains=["marker"], summary="low priority")
    write_entry(replay_dir, "high", fingerprint="y", priority=99,
                contains=["marker"], summary="high priority")
    parsed, _usage, _ = ReplayBackend("m").review(make_request("+marker\n"))
    assert parsed.summary == "high priority"


def test_fingerprint_beats_contains_all(replay_dir, make_request):
    diff = "+marker\n"
    write_entry(replay_dir, "by-contains", fingerprint="x", priority=99,
                contains=["marker"], summary="contains")
    write_entry(replay_dir, "by-fingerprint", fingerprint=diff_fingerprint(diff),
                priority=0, summary="fingerprint")
    parsed, _usage, _ = ReplayBackend("m").review(make_request(diff))
    assert parsed.summary == "fingerprint"


def test_miss_raises_rather_than_passing(replay_dir, make_request):
    """A cache miss must never look like a clean review."""
    write_entry(replay_dir, "e1", fingerprint="nope")
    with pytest.raises(BackendError) as exc:
        ReplayBackend("m").review(make_request("+something unmatched\n"))
    assert "no replay entry matches" in str(exc.value)


def test_miss_delegates_to_live_backend(replay_dir, make_request):
    class StubLive:
        name = "stub"
        model = "stub"

        def __init__(self):
            self.called = False

        def review(self, req):
            self.called = True
            return ModelResponse(summary="from live"), {"total_tokens": 1}, False

    live = StubLive()
    parsed, _usage, replayed = ReplayBackend("m", live=live).review(
        make_request("+unmatched\n"))
    assert live.called
    assert replayed is False, "a live answer must not be labelled as replayed"
    assert parsed.summary == "from live"


def test_malformed_entry_is_skipped_not_fatal(replay_dir, make_request):
    (replay_dir / "broken.json").write_text("{ this is not json")
    diff = "+ok\n"
    write_entry(replay_dir, "good", fingerprint=diff_fingerprint(diff), summary="ok")
    parsed, _usage, _ = ReplayBackend("m").review(make_request(diff))
    assert parsed.summary == "ok"


def test_empty_cache_raises(replay_dir, make_request):
    with pytest.raises(BackendError):
        ReplayBackend("m").review(make_request("+anything\n"))


# --------------------------------------------------------------------------
# model output parsing
# --------------------------------------------------------------------------

VALID = '{"summary": "s", "findings": []}'


def test_plain_json():
    assert _parse_model_json(VALID).summary == "s"


def test_fenced_json():
    assert _parse_model_json(f"```json\n{VALID}\n```").summary == "s"


def test_unlabelled_fence():
    assert _parse_model_json(f"```\n{VALID}\n```").summary == "s"


def test_reasoning_block_is_stripped():
    """Reasoning models emit <think> before the answer. Strip, do not fail."""
    text = f"<think>\nLet me consider the ownership check...\n</think>\n{VALID}"
    assert _parse_model_json(text).summary == "s"


def test_multiline_reasoning_with_fence():
    text = f"<think>reasoning\nacross lines</think>\n```json\n{VALID}\n```"
    assert _parse_model_json(text).summary == "s"


def test_prose_around_json_is_recovered():
    text = f"Here is my analysis:\n{VALID}\nLet me know if you need more."
    assert _parse_model_json(text).summary == "s"


def test_findings_are_parsed():
    payload = json.dumps({"summary": "", "findings": critical()})
    parsed = _parse_model_json(payload)
    assert len(parsed.findings) == 1
    assert parsed.findings[0].cwe == "CWE-639"


def test_no_json_at_all_raises():
    with pytest.raises(BackendError) as exc:
        _parse_model_json("I am unable to review this code.")
    assert "no JSON object" in str(exc.value)


def test_schema_violation_raises():
    """Severity outside the enum must fail the step, not be coerced."""
    bad = json.dumps({"summary": "", "findings": [
        {**critical()[0], "severity": "catastrophic"}]})
    with pytest.raises(BackendError):
        _parse_model_json(bad)


def test_empty_output_raises():
    with pytest.raises(BackendError):
        _parse_model_json("")


# --------------------------------------------------------------------------
# backend selection
# --------------------------------------------------------------------------

def test_default_backend_is_replay(monkeypatch, tmp_path):
    monkeypatch.setenv("REPLAY_DIR", str(tmp_path))
    assert build_backend().name == "replay"


def test_explicit_backends(monkeypatch, tmp_path):
    monkeypatch.setenv("REPLAY_DIR", str(tmp_path))
    for name, cls_name in (("llamacpp", "llamacpp"), ("vllm", "vllm")):
        monkeypatch.setenv("SCANNER_BACKEND", name)
        assert build_backend().name == cls_name


def test_unknown_backend_raises(monkeypatch):
    monkeypatch.setenv("SCANNER_BACKEND", "chatgpt")
    with pytest.raises(ValueError) as exc:
        build_backend()
    assert "not one of" in str(exc.value)


def test_replay_fallback_wires_a_live_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("REPLAY_DIR", str(tmp_path))
    monkeypatch.setenv("SCANNER_BACKEND", "replay")
    monkeypatch.setenv("REPLAY_FALLBACK", "live")
    backend = build_backend()
    assert isinstance(backend.live, LlamaCppBackend)


def test_no_fallback_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("REPLAY_DIR", str(tmp_path))
    assert build_backend().live is None


def test_decoding_is_deterministic(monkeypatch):
    """Temperature 0 and a fixed seed, or the gate is not reproducible."""
    payload = LlamaCppBackend("http://x", "m")._payload([])
    assert payload["temperature"] == 0.0
    assert payload["seed"] == 42
    assert payload["top_p"] == 1.0
    assert payload["response_format"]["type"] == "json_schema"
