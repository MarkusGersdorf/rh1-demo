"""The HTTP surface, driven the way the Tekton task drives it."""

import pytest


@pytest.fixture
def client(scanner_app):
    c, _main = scanner_app()
    return c


def review(client, diff, **kw):
    payload = {"repository": "r", "commit": "c" * 40, "base_commit": "b" * 40,
               "diff": diff}
    payload.update(kw)
    return client.post("/review", json=payload)


# --------------------------------------------------------------------------
# health
# --------------------------------------------------------------------------

def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok", "backend": "replay"}


def test_readyz_reports_replay_entry_count(client):
    body = client.get("/readyz").json()
    assert body["status"] == "ok"
    assert body["entries"] >= 2


def test_readyz_unready_when_replay_cache_is_empty(scanner_app, tmp_path):
    """An empty cache means the gate cannot answer. That is not ready."""
    c, _ = scanner_app(REPLAY_DIR=str(tmp_path))
    resp = c.get("/readyz")
    assert resp.status_code == 503


def test_readyz_unready_when_model_unreachable(scanner_app):
    c, _ = scanner_app(SCANNER_BACKEND="llamacpp",
                       MODEL_ENDPOINT="http://127.0.0.1:1/nope")
    resp = c.get("/readyz")
    assert resp.status_code == 503
    assert "unreachable" in resp.json()["status"] or "unhealthy" in resp.json()["status"]


# --------------------------------------------------------------------------
# the two demo paths, end to end
# --------------------------------------------------------------------------

def test_vulnerable_diff_fails_the_gate(client, vulnerable_diff, vulnerable_source):
    resp = review(client, vulnerable_diff,
                  files=[{"path": "AccountResource.java", "content": vulnerable_source}])
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "fail"
    assert any(f["cwe"] == "CWE-639" for f in body["findings"]), \
        "the broken access control finding is the whole demo"


def test_fixed_diff_passes_the_gate(client, fixed_diff, fixed_source):
    body = review(client, fixed_diff,
                  files=[{"path": "AccountResource.java", "content": fixed_source}]).json()
    assert body["verdict"] == "pass"
    assert body["findings"] == []


def test_findings_are_ordered_most_severe_first(client, vulnerable_diff):
    body = review(client, vulnerable_diff).json()
    order = [f["severity"] for f in body["findings"]]
    rank = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
    assert order == sorted(order, key=lambda s: -rank[s])


def test_response_carries_a_predicate(client, vulnerable_diff):
    body = review(client, vulnerable_diff).json()
    assert body["predicate"]["predicateType"].endswith("/code-review/v1")


def test_replayed_responses_are_labelled(client, vulnerable_diff):
    body = review(client, vulnerable_diff).json()
    assert body["replayed"] is True
    explain = [r for r in body["predicate"]["predicate"]["reviewers"]
               if r["stage"] == "explain"][0]
    assert explain["replayed"] is True


def test_image_digest_binds_the_subject(client, vulnerable_diff):
    body = review(client, vulnerable_diff,
                  image="reg/accounts-api",
                  image_digest="sha256:abcdef123456").json()
    assert body["predicate"]["subject"][0]["digest"]["sha256"] == "abcdef123456"


# --------------------------------------------------------------------------
# fail-closed: the security property
# --------------------------------------------------------------------------

def test_replay_miss_returns_error_not_pass(client):
    """A cache miss must not look like a clean review."""
    body = review(client, "diff --git a/x b/x\n+int unmatched = 1;\n").json()
    assert body["verdict"] == "error"
    assert body["findings"] == []
    assert "no replay entry matches" in body["error"]


def test_unreachable_model_returns_error_not_pass(scanner_app):
    c, _ = scanner_app(SCANNER_BACKEND="llamacpp",
                       MODEL_ENDPOINT="http://127.0.0.1:1/nope",
                       MODEL_TIMEOUT_SECONDS="2")
    body = review(c, "diff --git a/x b/x\n+int x = 1;\n").json()
    assert body["verdict"] == "error"
    assert "unreachable" in body["error"]


def test_error_responses_carry_no_predicate(scanner_app):
    """Nothing signable may come out of a failed review."""
    c, _ = scanner_app(SCANNER_BACKEND="llamacpp",
                       MODEL_ENDPOINT="http://127.0.0.1:1/nope",
                       MODEL_TIMEOUT_SECONDS="2")
    body = review(c, "diff --git a/x b/x\n+int x = 1;\n").json()
    assert body["predicate"] is None


def test_no_configuration_makes_a_backend_error_pass(scanner_app):
    """The fail-closed guarantee, asserted against the knobs that exist.

    FAIL_ON and MIN_CONFIDENCE tune which *findings* block. Neither may turn a
    backend failure into a pass, because that is the difference between a
    security control and decoration.
    """
    for env in (
        {"FAIL_ON": ""},
        {"FAIL_ON": "critical", "MIN_CONFIDENCE": "high"},
        {"MIN_CONFIDENCE": "low"},
    ):
        c, _ = scanner_app(SCANNER_BACKEND="llamacpp",
                           MODEL_ENDPOINT="http://127.0.0.1:1/nope",
                           MODEL_TIMEOUT_SECONDS="2", **env)
        body = review(c, "diff --git a/x b/x\n+int x = 1;\n").json()
        assert body["verdict"] == "error", f"{env} produced {body['verdict']}"


def test_empty_diff_is_rejected(client):
    assert review(client, "   \n  ").status_code == 400


def test_missing_diff_is_a_validation_error(client):
    assert client.post("/review", json={"repository": "r", "commit": "c"}).status_code == 422


# --------------------------------------------------------------------------
# history and UI
# --------------------------------------------------------------------------

def test_reviews_history_is_newest_first(client, vulnerable_diff, fixed_diff):
    review(client, vulnerable_diff)
    review(client, fixed_diff)
    history = client.get("/api/reviews").json()
    assert len(history) == 2
    assert history[0]["verdict"] == "pass"
    assert history[1]["verdict"] == "fail"


def test_ui_is_empty_before_any_review(client):
    assert "No reviews yet" in client.get("/").text


def test_ui_renders_findings_and_the_replay_badge(client, vulnerable_diff):
    review(client, vulnerable_diff)
    html = client.get("/").text
    assert "CWE-639" in html
    assert "REPLAY" in html, "a recorded answer must be visibly labelled"
    assert "CRITICAL" in html


def test_ui_escapes_model_output(scanner_app, tmp_path, monkeypatch):
    """Findings are model output rendered into HTML. Escape them."""
    import json
    (tmp_path / "xss.json").write_text(json.dumps({
        "provenance": "captured", "fingerprint": "x",
        "match": {"contains_all": ["trigger"]}, "latency_ms": 0,
        "response": {"summary": "<script>alert(1)</script>", "findings": []},
    }))
    c, _ = scanner_app(REPLAY_DIR=str(tmp_path))
    review(c, "+trigger\n")
    html = c.get("/").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# --------------------------------------------------------------------------
# two-stage behaviour
# --------------------------------------------------------------------------

def test_stage_b_reviews_the_diff_even_when_stage_a_is_absent(
        client, vulnerable_diff):
    """Stage A is advisory. Its absence must not disable the review.

    Antares is a Python-leaning localizer being asked about Java here, so
    gating the explanation on a localizer hit would let one model's miss
    silently switch off the other.
    """
    body = review(client, vulnerable_diff).json()
    assert body["verdict"] == "fail"
    assert body["localizer"] is None
    assert any(f["cwe"] == "CWE-639" for f in body["findings"])


def test_localizer_is_echoed_into_the_response(client, vulnerable_diff,
                                               localizer_result):
    body = review(client, vulnerable_diff,
                  localizer=localizer_result.model_dump(mode="json")).json()
    assert body["localizer"]["model"] == "antares-1b"
    assert body["localizer"]["cwes_available"] == 944


def test_both_stages_reach_the_predicate(client, vulnerable_diff,
                                         localizer_result):
    body = review(client, vulnerable_diff,
                  localizer=localizer_result.model_dump(mode="json")).json()
    stages = [r["stage"] for r in body["predicate"]["predicate"]["reviewers"]]
    assert stages == ["localize", "explain"]


def test_corroboration_is_marked_when_files_agree(client, vulnerable_diff,
                                                  localizer_result):
    """Stage A flagged app.py; the demo's findings are in AccountResource.java,
    so nothing should be marked corroborated here."""
    body = review(client, vulnerable_diff,
                  localizer=localizer_result.model_dump(mode="json")).json()
    assert all(not f["corroborated_by_localizer"] for f in body["findings"])


def test_corroboration_matches_on_path_suffix(client, vulnerable_diff,
                                              localizer_result):
    """Antares reports paths relative to the scan root; the reasoner reports
    repo-relative paths. They must still be recognised as the same file."""
    payload = localizer_result.model_dump(mode="json")
    payload["findings"][0]["file"] = (
        "src/main/java/com/redhat/demo/accounts/AccountResource.java")
    body = review(client, vulnerable_diff, localizer=payload).json()
    assert any(f["corroborated_by_localizer"] for f in body["findings"])


def test_corroboration_does_not_change_the_verdict(client, fixed_diff,
                                                   localizer_result):
    """Two models agreeing is weaker evidence than it feels like - same vendor,
    overlapping training data. Corroboration is recorded, never acted on."""
    payload = localizer_result.model_dump(mode="json")
    for f in payload["findings"]:
        f["file"] = "src/main/java/com/redhat/demo/accounts/AccountResource.java"
    body = review(client, fixed_diff, localizer=payload).json()
    assert body["verdict"] == "pass", \
        "a localizer hit must not fail a change the reasoner cleared"


def test_ui_shows_stage_a_and_its_limits(client, vulnerable_diff,
                                         localizer_result):
    review(client, vulnerable_diff,
           localizer=localizer_result.model_dump(mode="json"))
    html = client.get("/").text
    assert "Stage A" in html
    assert "file-level localization only" in html
    assert "944" in html, "coverage must be visible, not just in the predicate"
