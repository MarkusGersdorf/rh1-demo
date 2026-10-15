"""The pass/fail decision.

This is the security control. It must be boring, predictable, and impossible
to accidentally configure into uselessness.
"""

import pytest

from app.main import gate
from app.schema import Finding


def finding(severity="critical", confidence="high"):
    return Finding(
        title="t", severity=severity, confidence=confidence,
        cwe="CWE-639", owasp="A01:2021", file="X.java", line=1,
        rationale="r",
    )


def test_no_findings_passes():
    assert gate([]) == "pass"


@pytest.mark.parametrize("severity", ["critical", "high"])
def test_blocking_severities_fail_by_default(severity):
    assert gate([finding(severity=severity)]) == "fail"


@pytest.mark.parametrize("severity", ["medium", "low", "info"])
def test_non_blocking_severities_pass_by_default(severity):
    assert gate([finding(severity=severity)]) == "pass"


def test_low_confidence_cannot_block(monkeypatch):
    """A hedged finding must not stop a release.

    The confidence floor exists so the gate stays credible. A gate that blocks
    on "might be an issue" is a gate people switch off.
    """
    monkeypatch.setenv("MIN_CONFIDENCE", "medium")
    assert gate([finding(severity="critical", confidence="low")]) == "pass"


def test_medium_confidence_blocks_at_default_floor(monkeypatch):
    monkeypatch.setenv("MIN_CONFIDENCE", "medium")
    assert gate([finding(severity="critical", confidence="medium")]) == "fail"


def test_raising_the_floor_to_high(monkeypatch):
    monkeypatch.setenv("MIN_CONFIDENCE", "high")
    assert gate([finding(confidence="medium")]) == "pass"
    assert gate([finding(confidence="high")]) == "fail"


def test_fail_on_can_be_widened(monkeypatch):
    monkeypatch.setenv("FAIL_ON", "critical,high,medium")
    assert gate([finding(severity="medium")]) == "fail"


def test_fail_on_can_be_narrowed(monkeypatch):
    monkeypatch.setenv("FAIL_ON", "critical")
    assert gate([finding(severity="high")]) == "pass"
    assert gate([finding(severity="critical")]) == "fail"


def test_one_blocking_finding_among_many_fails():
    assert gate([
        finding(severity="low"),
        finding(severity="info"),
        finding(severity="critical"),
    ]) == "fail"


def test_schema_rejects_non_lowercase_severity():
    """Normalisation happens at the schema boundary, not in the gate.

    `gate()` lowercases defensively, but a Finding can never carry an
    uppercase severity: the Literal rejects it first. Worth pinning, because
    it means the model's output is normalised before any decision is made.
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        finding(severity="CRITICAL")


def test_fail_on_whitespace_is_tolerated(monkeypatch):
    monkeypatch.setenv("FAIL_ON", " critical , high ")
    assert gate([finding(severity="high")]) == "fail"


def test_empty_fail_on_blocks_nothing(monkeypatch):
    """Documenting a sharp edge: FAIL_ON="" disables blocking entirely.

    Findings are still produced and still attested - the verdict just stops
    being "fail". If you set this, the attestation is your audit trail and the
    gate is advisory. Worth knowing it is reachable by config.
    """
    monkeypatch.setenv("FAIL_ON", "")
    assert gate([finding(severity="critical")]) == "pass"
