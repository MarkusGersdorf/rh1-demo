"""The in-toto statement.

This is the artifact the whole demo argues for, so its shape is pinned here.
A silent change to the predicate breaks the Enterprise Contract rego in
gitops/components/pipelines/tasks.yaml, which is the thing that enforces it.
"""

from app.attest import PREDICATE_TYPE, build_statement, diff_scope, model_digest
from app.schema import Finding, ReviewResponse


def response(**kw):
    defaults = dict(
        verdict="pass", findings=[], backend="replay",
        model="fdtn-ai/Foundation-Sec-8B-Reasoning",
        prompt_version="lightwell-inward/prompts@v3",
        latency_ms=100, replayed=False,
    )
    defaults.update(kw)
    return ReviewResponse(**defaults)


def finding():
    return Finding(
        title="Ownership check discarded", severity="critical", confidence="high",
        cwe="CWE-639", owasp="A01:2021-Broken Access Control",
        file="AccountResource.java", line=67, rationale="r", exploit="e", fix="f",
    )


# --------------------------------------------------------------------------
# diff_scope
# --------------------------------------------------------------------------

def test_scope_of_the_real_vulnerable_diff(vulnerable_diff):
    scope = diff_scope(vulnerable_diff)
    assert scope["filesReviewed"] == 1
    assert scope["files"] == ["src/main/java/com/redhat/demo/accounts/AccountResource.java"]
    assert scope["hunks"] == 2
    assert scope["linesAdded"] > 40
    assert scope["linesRemoved"] == 0


def test_scope_of_the_real_fix_diff(fixed_diff):
    scope = diff_scope(fixed_diff)
    assert scope["filesReviewed"] == 1
    assert scope["linesAdded"] == 3
    assert scope["linesRemoved"] == 2


def test_scope_counts_multiple_files():
    diff = (
        "diff --git a/a/One.java b/b/One.java\n@@ -1 +1 @@\n+x\n"
        "diff --git a/a/Two.java b/b/Two.java\n@@ -1 +1 @@\n+y\n-z\n"
    )
    scope = diff_scope(diff)
    assert scope["filesReviewed"] == 2
    assert scope["hunks"] == 2
    assert scope["linesAdded"] == 2
    assert scope["linesRemoved"] == 1


def test_scope_ignores_file_header_markers():
    """`+++ b/x` and `--- a/x` are headers, not added or removed lines."""
    diff = "diff --git a/b/x b/b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n+real\n"
    scope = diff_scope(diff)
    assert scope["linesAdded"] == 1
    assert scope["linesRemoved"] == 0


def test_empty_diff_scope_is_zero():
    assert diff_scope("")["filesReviewed"] == 0


# --------------------------------------------------------------------------
# build_statement
# --------------------------------------------------------------------------

def test_statement_is_in_toto_v1(make_request, vulnerable_diff):
    stmt = build_statement(make_request(vulnerable_diff), response())
    assert stmt["_type"] == "https://in-toto.io/Statement/v1"
    assert stmt["predicateType"] == PREDICATE_TYPE


def test_subject_binds_to_the_image_digest(make_request):
    req = make_request(
        "+x\n",
        image="registry/demo/accounts-api",
        image_digest="sha256:9f2a1c3d",
    )
    subject = build_statement(req, response())["subject"][0]
    assert subject["name"] == "registry/demo/accounts-api"
    assert subject["digest"]["sha256"] == "9f2a1c3d", "the sha256: prefix must be stripped"


def test_subject_falls_back_to_the_commit_pre_build(make_request):
    """A review happens before the image exists, so bind to the commit."""
    req = make_request("+x\n")
    subject = build_statement(req, response())["subject"][0]
    assert req.commit in subject["name"]
    assert subject["digest"]["gitCommit"] == req.commit


def test_verdict_and_commits_are_recorded(make_request):
    req = make_request("+x\n")
    pred = build_statement(req, response(verdict="fail"))["predicate"]
    assert pred["verdict"] == "fail"
    assert pred["commit"] == req.commit
    assert pred["baseCommit"] == req.base_commit


def test_reviewer_is_fully_pinned(make_request, monkeypatch):
    """Model, digest, prompt version and decoding must all be in the claim.

    Without these the attestation is not revocable, which removes the main
    reason to sign it.
    """
    monkeypatch.setenv("MODEL_DIGEST", "sha256:abc123")
    reviewer = build_statement(make_request("+x\n"), response())["predicate"]["reviewer"]
    assert reviewer["model"] == "fdtn-ai/Foundation-Sec-8B-Reasoning"
    assert reviewer["modelDigest"] == "sha256:abc123"
    assert reviewer["promptVersion"] == "lightwell-inward/prompts@v3"
    assert reviewer["decoding"]["temperature"] == 0.0
    assert reviewer["decoding"]["schemaConstrained"] is True
    assert reviewer["kind"] == "llm"


def test_replayed_flag_reaches_the_signed_claim(make_request):
    """A recorded answer must never pass as live inference, even in the signature."""
    pred = build_statement(make_request("+x\n"), response(replayed=True))["predicate"]
    assert pred["reviewer"]["replayed"] is True

    pred = build_statement(make_request("+x\n"), response(replayed=False))["predicate"]
    assert pred["reviewer"]["replayed"] is False


def test_findings_are_embedded(make_request):
    pred = build_statement(make_request("+x\n"),
                           response(verdict="fail", findings=[finding()]))["predicate"]
    assert len(pred["findings"]) == 1
    assert pred["findings"][0]["cwe"] == "CWE-639"
    assert pred["findings"][0]["exploit"] == "e"


def test_passing_review_has_no_findings(make_request):
    pred = build_statement(make_request("+x\n"), response())["predicate"]
    assert pred["findings"] == []


def test_gate_configuration_is_recorded(make_request, monkeypatch):
    """Which severities blocked must be in the claim, or 'pass' is ambiguous."""
    monkeypatch.setenv("FAIL_ON", "critical")
    monkeypatch.setenv("MIN_CONFIDENCE", "high")
    pred = build_statement(make_request("+x\n"), response())["predicate"]
    assert pred["gate"]["failOnSeverity"] == ["critical"]
    assert pred["gate"]["minConfidence"] == "high"


def test_complementary_scanners_passed_through(make_request):
    req = make_request("+x\n", complementary=[{"name": "semgrep", "verdict": "pass"}])
    pred = build_statement(req, response())["predicate"]
    assert pred["complementaryScanners"] == [{"name": "semgrep", "verdict": "pass"}]


def test_complementary_defaults_to_empty(make_request):
    pred = build_statement(make_request("+x\n"), response())["predicate"]
    assert pred["complementaryScanners"] == []


def test_unpinned_model_is_reported_honestly(monkeypatch, tmp_path):
    """Enterprise Contract denies on 'unpinned', so it must not be faked."""
    monkeypatch.delenv("MODEL_DIGEST", raising=False)
    monkeypatch.chdir(tmp_path)
    assert model_digest() == "unpinned"


def test_env_digest_takes_precedence(monkeypatch):
    monkeypatch.setenv("MODEL_DIGEST", "sha256:fromenv")
    assert model_digest() == "sha256:fromenv"


def test_predicate_keys_match_the_enterprise_contract_rego(make_request):
    """Pins the exact field names the rego in tasks.yaml reads.

    If you rename any of these, update
    gitops/components/pipelines/tasks.yaml:ec-validate at the same time.
    """
    pred = build_statement(make_request("+x\n"), response())["predicate"]
    assert "verdict" in pred
    assert "filesReviewed" in pred["scope"]
    assert "modelDigest" in pred["reviewer"]
