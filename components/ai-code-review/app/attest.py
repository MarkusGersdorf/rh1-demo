"""Builds the in-toto statement that Trusted Artifact Signer signs.

This module is the actual thesis of the demo. Everything else is plumbing to
get a model's opinion into this structure, and this structure is what turns an
opinion into a supply-chain claim you can enforce and later revoke.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

PREDICATE_TYPE = "https://redhat.com/lightwell-inward/code-review/v1"


def diff_scope(diff: str) -> dict:
    """Summarise what was actually reviewed.

    Recorded in the predicate so a verifier can tell a review of a 40-line
    change from a review of a 4000-line one. An attestation that does not say
    what it covered is close to worthless.
    """
    files: set[str] = set()
    hunks = added = removed = 0
    for line in diff.splitlines():
        if line.startswith("diff --git"):
            match = re.search(r" b/(\S+)$", line)
            if match:
                files.add(match.group(1))
        elif line.startswith("@@"):
            hunks += 1
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return {
        "filesReviewed": len(files),
        "files": sorted(files),
        "hunks": hunks,
        "linesAdded": added,
        "linesRemoved": removed,
    }


def model_digest() -> str:
    """Digest of the served model artifact.

    Pinning this is what makes the claim revocable: if this model is later
    found blind to a bug class, you query Rekor for every artifact approved
    under this digest and re-review exactly those. Same capability Lightwell
    Clearinghouse gives you for an embargoed dependency CVE, pointed inward.
    """
    explicit = os.getenv("MODEL_DIGEST")
    if explicit:
        return explicit
    # Written by the model-fetch job alongside the GGUF.
    for path in ("/mnt/models/model.sha256", "/app/replay/model.sha256"):
        try:
            with open(path) as handle:
                return "sha256:" + handle.read().split()[0]
        except (OSError, IndexError):
            continue
    return "unpinned"


def build_statement(req, response) -> dict:
    """Assemble the in-toto v1 statement for a completed review."""
    subject_name = req.image or f"{req.repository}@{req.commit}"
    digest = req.image_digest or ""
    subject: dict = {"name": subject_name}
    if digest:
        subject["digest"] = {"sha256": digest.removeprefix("sha256:")}
    else:
        # No image yet (pre-build review). Bind to the commit instead, so the
        # statement is still about something specific and verifiable.
        subject["digest"] = {"gitCommit": req.commit}

    return {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [subject],
        "predicateType": PREDICATE_TYPE,
        "predicate": {
            "verdict": response.verdict,
            "scannedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "repository": req.repository,
            "commit": req.commit,
            "baseCommit": req.base_commit,
            "reviewer": {
                "kind": "llm",
                "model": response.model,
                "modelDigest": model_digest(),
                "servedBy": os.getenv("MODEL_SERVED_BY", "openshift-ai/kserve"),
                "backend": response.backend,
                # Recorded, not live. Stated in the signed claim itself so a
                # replayed answer can never silently pass as fresh inference.
                "replayed": response.replayed,
                "promptVersion": response.prompt_version,
                "decoding": {
                    "temperature": 0.0,
                    "topP": 1.0,
                    "seed": 42,
                    "maxTokens": int(os.getenv("MODEL_MAX_TOKENS", "2048")),
                    "schemaConstrained": True,
                },
            },
            "gate": {
                "failOnSeverity": _fail_on(),
                "minConfidence": os.getenv("MIN_CONFIDENCE", "medium"),
            },
            "scope": diff_scope(req.diff),
            "findings": [f.model_dump() for f in response.findings],
            "summary": response.summary,
            # Reported by the pipeline. Empty means the review ran on its
            # own, which is itself worth knowing when auditing the claim.
            "complementaryScanners": req.complementary,
        },
    }


def _fail_on() -> list[str]:
    return [s.strip() for s in os.getenv("FAIL_ON", "critical,high").split(",") if s.strip()]
