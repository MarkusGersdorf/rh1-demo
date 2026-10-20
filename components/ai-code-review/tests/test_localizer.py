"""Stage A ingestion, exercised against the real vendored Antares output.

These run against `antares/reports/cpu-sweep` in the pinned `antares`
submodule - a genuine recorded CPU run from sroecker/supreme-couscous (MIT),
not hand-written data. That matters: the parser's job is to match a format
this project does not control, and the submodule pin means an upstream push
cannot change it from under these tests.
"""

import json

import pytest

from app.localizer import LIKELIHOOD_TO_SEVERITY, parse_report_dir
from app.schema import LocalizerResult


def test_parses_the_real_recorded_run(localizer_result):
    assert isinstance(localizer_result, LocalizerResult)
    assert localizer_result.tool == "antares-cli"
    assert localizer_result.model == "antares-1b"
    assert localizer_result.mode == "cwe_sweep"
    assert len(localizer_result.findings) == 3


def test_findings_have_no_line_numbers(localizer_result):
    """Antares is a file-level localizer. If this ever changes, the talk track
    and the Stage B prompt both need revisiting, so pin it."""
    for finding in localizer_result.findings:
        assert not hasattr(finding, "line")
        assert finding.file


def test_finding_fields(localizer_result):
    by_cwe = {f.cwe: f for f in localizer_result.findings}
    assert set(by_cwe) == {"CWE-20", "CWE-78", "CWE-79"}
    f = by_cwe["CWE-78"]
    assert f.file == "app.py"
    assert f.likelihood == "high"
    assert f.rank == 1
    assert "OS Command" in f.title


def test_coverage_is_read_from_the_nested_selection_block(localizer_result):
    """Regression: `selection` lives under `metadata`, not at the top level.

    Reading it from the wrong place silently reported zero coverage, which
    would have made the attestation understate what Stage A examined - a
    claim-weakening bug that no exception would ever surface.
    """
    assert localizer_result.cwes_available == 944
    assert localizer_result.cwes_checked == ["CWE-79", "CWE-20", "CWE-78"]
    assert localizer_result.truncated is True


def test_run_statistics_are_captured(localizer_result):
    assert localizer_result.duration_seconds == pytest.approx(167.57, abs=0.1)
    assert localizer_result.tool_calls == 47
    assert localizer_result.failed_tool_calls == 7
    assert localizer_result.incomplete_reason is None


def test_nondeterminism_is_detected(localizer_result):
    """Antares ships temperature 0.3 and may vary by provider.

    Stage B runs at 0. The predicate must not imply both are reproducible.
    """
    decoding = localizer_result.decoding
    assert decoding["temperature"] == 0.3
    assert decoding["deterministic"] is False
    assert decoding["providerMayVary"] is True
    assert decoding["contextWindow"] == 16384


def test_digest_sidecar_is_read(localizer_result):
    assert localizer_result.model_digest.startswith("sha256:7c1e4b0a")


def test_files_helper(localizer_result):
    assert localizer_result.files() == {"app.py"}


def test_missing_digest_sidecar_is_none(antares_report_dir):
    (antares_report_dir / "model.sha256").unlink()
    assert parse_report_dir(antares_report_dir).model_digest is None


def test_sarif_fallback_when_json_absent(antares_report_dir):
    (antares_report_dir / "report.json").unlink()
    result = parse_report_dir(antares_report_dir)
    assert len(result.findings) == 3
    assert {f.cwe for f in result.findings} == {"CWE-20", "CWE-78", "CWE-79"}
    assert result.mode == "sarif"


def test_sarif_severity_comes_from_likelihood_not_level(antares_report_dir):
    """SARIF `level` is always "note" in Antares output; severity is in
    properties.likelihoodOfExploit. Reading `level` would flatten everything
    to informational."""
    sarif = json.loads((antares_report_dir / "report.sarif").read_text())
    levels = {r["level"] for r in sarif["runs"][0]["results"]}
    assert levels == {"note"}, "upstream changed SARIF levels - revisit the mapping"

    (antares_report_dir / "report.json").unlink()
    result = parse_report_dir(antares_report_dir)
    assert all(f.likelihood == "high" for f in result.findings)


def test_likelihood_maps_to_our_severity_vocabulary():
    assert LIKELIHOOD_TO_SEVERITY["high"] == "high"
    assert LIKELIHOOD_TO_SEVERITY["unknown"] == "info"


def test_empty_report_directory_raises(tmp_path):
    with pytest.raises(FileNotFoundError) as exc:
        parse_report_dir(tmp_path)
    assert "did the Antares CLI run" in str(exc.value)


def test_report_with_no_findings(antares_report_dir):
    data = json.loads((antares_report_dir / "report.json").read_text())
    data["findings"] = []
    data["summary"]["total_findings"] = 0
    (antares_report_dir / "report.json").write_text(json.dumps(data))
    result = parse_report_dir(antares_report_dir)
    assert result.findings == []
    assert result.files() == set()
    # Still reports coverage: silence from a 3-of-944 scan is weak evidence and
    # the predicate has to be able to say so.
    assert result.cwes_available == 944
