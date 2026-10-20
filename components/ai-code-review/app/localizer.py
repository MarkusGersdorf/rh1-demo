"""Stage A: ingest Antares CLI output.

Antares is a **file-level vulnerability localizer**, not an explainer. Its own
model spec says so, and a finding is exactly five fields:

    {"title", "file_path", "cwe_ids", "submission_rank", "likelihood_of_exploit"}

No line number, no rationale, no exploit, no fix. So this module does not try
to turn Antares output into a reviewable finding - it turns it into a *prior*
that Stage B (the reasoning model) is told about, and that the attestation
records as a second, independent reviewer.

Two deliberate consequences:

  - Stage B reviews the whole diff regardless of what Antares said. Gating the
    explanation on a localizer hit would mean one model's miss silently
    disables the other, and Antares is a Python-leaning localizer being asked
    about Java here.
  - When both stages implicate the same file, the finding is marked
    corroborated. That is recorded, not acted on: two models agreeing is
    weaker evidence than it feels like, because they are not independent (same
    vendor, overlapping training data).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .schema import LocalizerFinding, LocalizerResult

LOG = logging.getLogger("localizer")

# Antares reports likelihood as a word; map to our severity vocabulary. This is
# a translation, not a judgement - Antares does not claim a severity.
LIKELIHOOD_TO_SEVERITY = {
    "high": "high",
    "medium": "medium",
    "low": "low",
    "unknown": "info",
}


def parse_report_dir(path: str | Path) -> LocalizerResult:
    """Read an Antares output directory (report.json preferred, SARIF fallback)."""
    directory = Path(path)
    report = directory / "report.json"
    sarif = directory / "report.sarif"

    if report.is_file():
        return _parse_json(report)
    if sarif.is_file():
        LOG.warning("no report.json in %s - falling back to SARIF, which omits "
                    "the summary and metadata blocks", directory)
        return _parse_sarif(sarif)
    raise FileNotFoundError(
        f"no report.json or report.sarif in {directory} - did the Antares CLI run?"
    )


def _parse_json(path: Path) -> LocalizerResult:
    data = json.loads(path.read_text())
    summary = data.get("summary") or {}
    metadata = data.get("metadata") or {}

    findings = [
        LocalizerFinding(
            title=f.get("title", ""),
            file=f.get("file_path", ""),
            cwe=(f.get("cwe_ids") or [""])[0],
            cwes=f.get("cwe_ids") or [],
            likelihood=str(f.get("likelihood_of_exploit", "unknown")).lower(),
            rank=int(f.get("submission_rank", 0) or 0),
        )
        for f in data.get("findings") or []
    ]

    # The CWE catalogue is huge and Antares scans a ranked subset, so how much
    # it looked at is as important as what it found. An attestation that says
    # "clean" after checking 3 of 944 CWEs is making a much smaller claim than
    # it appears to.
    # Nested under metadata, not at the top level.
    selection = metadata.get("selection") or {}
    return LocalizerResult(
        tool="antares-cli",
        model=metadata.get("model", "unknown"),
        mode=metadata.get("mode", "unknown"),
        findings=findings,
        cwes_checked=metadata.get("cwe_ids") or selection.get("selected_cwe_ids") or [],
        cwes_available=int(selection.get("candidate_cwe_count", 0) or 0),
        truncated=bool(selection.get("truncated", False)),
        duration_seconds=float(summary.get("duration_seconds", 0.0) or 0.0),
        tool_calls=int(summary.get("tool_call_count", 0) or 0),
        failed_tool_calls=int(summary.get("failed_tool_calls", 0) or 0),
        incomplete_reason=summary.get("incomplete_reason"),
        request_hash=(metadata.get("reproducibility") or {}).get("request_hash"),
        model_digest=_sidecar_digest(path.parent),
        decoding=_decoding(metadata),
    )


def _sidecar_digest(directory: Path) -> str | None:
    """Read model.sha256 written next to the report by the pipeline."""
    try:
        return "sha256:" + (directory / "model.sha256").read_text().strip().split()[0]
    except (OSError, IndexError):
        return None


def _decoding(metadata: dict) -> dict:
    """Extract Antares' own decoding settings.

    Recorded verbatim because they are NOT deterministic - the shipped spec
    uses temperature 0.3 and the report's reproducibility block sets
    remote_provider_may_vary. Stage B runs at temperature 0. The attestation
    must not imply both stages are reproducible.
    """
    backend = ((metadata.get("model_configuration") or {}).get("backend")) or {}
    repro = metadata.get("reproducibility") or {}
    temperature = backend.get("temperature")
    return {
        "temperature": temperature,
        "topP": backend.get("top_p"),
        "maxTokens": backend.get("max_tokens"),
        "contextWindow": backend.get("context_window"),
        "deterministic": temperature == 0 and not repro.get("remote_provider_may_vary", False),
        "providerMayVary": bool(repro.get("remote_provider_may_vary", False)),
    }


def _parse_sarif(path: Path) -> LocalizerResult:
    """Minimal SARIF 2.1.0 reader.

    Note SARIF `level` is always "note" in Antares output, so severity comes
    from properties.likelihoodOfExploit instead. Locations carry only an
    artifactLocation.uri - there is no region, hence no line number.
    """
    data = json.loads(path.read_text())
    runs = data.get("runs") or []
    results = runs[0].get("results") if runs else []
    driver = (runs[0].get("tool", {}).get("driver", {}) if runs else {})

    findings = []
    for r in results or []:
        props = r.get("properties") or {}
        locations = r.get("locations") or []
        uri = ""
        if locations:
            uri = (locations[0].get("physicalLocation", {})
                   .get("artifactLocation", {}).get("uri", ""))
        cwes = props.get("cweIds") or ([r["ruleId"]] if r.get("ruleId") else [])
        findings.append(LocalizerFinding(
            title=props.get("title") or (r.get("message") or {}).get("text", ""),
            file=uri,
            cwe=cwes[0] if cwes else "",
            cwes=cwes,
            likelihood=str(props.get("likelihoodOfExploit", "unknown")).lower(),
            rank=int(props.get("submissionRank", 0) or 0),
        ))

    rules = driver.get("rules") or []
    return LocalizerResult(
        tool=driver.get("name", "antares-cli"),
        model=os.getenv("ANTARES_MODEL", "unknown"),
        mode="sarif",
        findings=findings,
        cwes_checked=[r["id"] for r in rules if r.get("id")],
    )
