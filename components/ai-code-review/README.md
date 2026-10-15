# ai-code-review

The gate. POST a diff, get findings, a verdict, and an in-toto statement.

## API

```
GET  /healthz       liveness
GET  /readyz        readiness - for live backends, probes the model endpoint
POST /review        the gate
GET  /api/reviews   last 25 reviews (JSON)
GET  /              operator view, auto-refreshing. For the projector.
```

`POST /review`:

```json
{
  "repository": "http://gitea.../demo/accounts-api",
  "commit": "4c1b9e7",
  "base_commit": "0ad3f11",
  "diff": "diff --git a/... ",
  "files": [{"path": "src/.../AccountResource.java", "content": "..."}],
  "image": "image-registry.../accounts-api",
  "image_digest": "sha256:..."
}
```

Returns `verdict` (`pass` | `fail` | `error`), `findings[]`, and `predicate`
(the in-toto statement for `cosign attest`).

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SCANNER_BACKEND` | `replay` | `replay`, `llamacpp` or `vllm` |
| `MODEL_ENDPOINT` | KServe predictor svc | OpenAI-compatible base URL |
| `MODEL_NAME` | `fdtn-ai/Foundation-Sec-8B-Reasoning` | Reported in the attestation |
| `MODEL_DIGEST` | read from `model.sha256` | Pins the model in the attestation |
| `MODEL_MAX_TOKENS` | `2048` | Reasoning needs headroom; don't cut this to 512 |
| `MODEL_TIMEOUT_SECONDS` | `600` | CPU inference is slow. Be generous. |
| `FAIL_ON` | `critical,high` | Severities that fail the build |
| `MIN_CONFIDENCE` | `medium` | Confidence floor before a finding can block |
| `REPLAY_FALLBACK` | unset | `live` sends replay misses to the model |
| `REPLAY_MAX_DELAY_SECONDS` | `3` | Caps the simulated latency of a replay |

## Fail-closed behaviour

Model unreachable, timeout, malformed output, or an unmatched replay with no
fallback all produce `verdict: error`, and the pipeline task exits non-zero.
There is no configuration that makes this fail open. That is deliberate.

## Replay entries

```json
{
  "provenance": "captured",            // or "hand-authored-seed"
  "label": "human-readable",
  "fingerprint": "<sha256 of normalised diff>",
  "match": { "priority": 10, "contains_all": ["distinctive substring"] },
  "latency_ms": 64210,
  "usage": {...},
  "response": { "summary": "...", "findings": [...] }
}
```

Two entries ship as **hand-authored seeds** so the pipeline is testable before
the model is serving. They are worked examples of the expected answer, not model
output, and the service logs a warning for each at startup. Replace them with
`scripts/capture-replay.sh`, which records the real model and sets
`provenance: captured`.

Matching order: exact fingerprint, then `contains_all` by descending priority,
then fallback-or-error. The `contains_all` layer is what keeps the demo alive
when a live typo on stage changes the diff hash.

## Local development

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
SCANNER_BACKEND=replay REPLAY_DIR=replay \
  uvicorn app.main:app --reload --port 8080
```

Then open http://localhost:8080 and POST one of the diffs in
`../vuln-demo-app/.demo/`.

## Tests

```sh
pip install -r requirements-dev.txt
pytest                                   # 133 tests
pytest --cov=app --cov-report=term-missing
```

Coverage floor is 90% (`pyproject.toml`); it currently sits at 98%.

| File | Covers |
|---|---|
| `test_fingerprint.py` | Diff normalisation — blob hashes, hunk offsets and whitespace must not move the fingerprint, or the replay cache misses on stage |
| `test_gate.py` | The pass/fail decision, `FAIL_ON`, the `MIN_CONFIDENCE` floor, and the sharp edge that `FAIL_ON=""` blocks nothing |
| `test_backends.py` | Replay matching (fingerprint, `contains_all`, priority), and parsing output from a reasoning model that emits `<think>` blocks and code fences |
| `test_live_backend.py` | The live HTTP path via `httpx.MockTransport`: the `json_schema` → `json_object` downgrade, 500s, timeouts, unexpected response shapes |
| `test_attest.py` | The in-toto statement — subject binding, scope counting, and that the reviewer block is fully pinned |
| `test_api.py` | The endpoints as Tekton drives them, plus the fail-closed guarantee |
| `test_prompts.py` | That the prompt version is pinned and the prompt still scopes out dependencies |
| `test_demo_integrity.py` | **The demo's premise.** See below. |

### test_demo_integrity.py

The most valuable file here, and the least obvious. It asserts the chain the
walkthrough depends on:

```
AccountResource.java  ->  .demo/*.diff  ->  replay/*.json  ->  the finding
```

Specifically it verifies that the ownership branch still has no `return` (the
vulnerability is actually planted), that `get()` above it still returns 403
(the contrast that carries the demo), that the committed diffs regenerate from
the committed sources, that each replay entry's fingerprint matches its diff,
that the `contains_all` markers really occur, and that the cited line numbers
land on the ownership check rather than somewhere in the file.

It also asserts the app's own test suite **does not** test cross-tenant
access. That is deliberate: Act 2 is "the tests are green and the code is
still wrong." A cross-tenant assertion in `src/test` would make the vulnerable
commit fail its unit tests and the narrative collapses. Those assertions live
here instead.

If you edit the Java, run `pytest tests/test_demo_integrity.py` before you
trust the demo again.

### Fail-closed

`test_api.py::test_no_configuration_makes_a_backend_error_pass` walks the
configuration knobs that exist and asserts none of them turns an unreachable
model into a `pass`. If you add a new setting, extend that test.
