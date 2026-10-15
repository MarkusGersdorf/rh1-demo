"""Guards the demo's premise.

Everything here protects a property the walkthrough depends on. If one of
these fails, the demo is broken on stage rather than in CI - which is why
they are tests and not comments.

The chain they protect:
  AccountResource.java  ->  .demo/*.diff  ->  replay/*.json  ->  the finding
If anyone edits the Java without regenerating the diffs and the replay cache,
the scanner misses and the gate returns `error` in front of an audience.
"""

import difflib
import json
import re

import pytest

from app.backends import diff_fingerprint
from app.schema import ModelResponse

OWNERSHIP_CHECK = 'if (!account.ownerId().equals(jwt.getSubject())) {'
FORBIDDEN_RETURN = 'return Response.status(Response.Status.FORBIDDEN).build();'


# --------------------------------------------------------------------------
# the planted vulnerability must actually be planted
# --------------------------------------------------------------------------

def statements_method(source: str) -> str:
    """Isolate statements(), so we never assert against get()'s correct check."""
    start = source.index("public Response statements(")
    end = source.index("private static LocalDate parseDate")
    return source[start:end]


def test_vulnerable_source_has_the_ownership_check(vulnerable_source):
    assert OWNERSHIP_CHECK in statements_method(vulnerable_source), \
        "the check must be present - a missing check is a different, easier bug"


def test_vulnerable_source_discards_the_check_result(vulnerable_source):
    """The bug: the branch logs and falls through. No return.

    This is what makes it invisible to SAST and visible to a reasoning model.
    """
    body = statements_method(vulnerable_source)
    branch_start = body.index(OWNERSHIP_CHECK)
    branch_end = body.index("}", body.index("LOG.warnf", branch_start))
    branch = body[branch_start:branch_end]
    assert "LOG.warnf" in branch
    assert "return" not in branch, \
        "the ownership branch must not return - that IS the vulnerability"


def test_get_method_still_does_it_correctly(vulnerable_source):
    """The contrast carries the demo: same class, same check, one returns."""
    get_start = vulnerable_source.index("public Response get(")
    get_end = vulnerable_source.index("public Response statements(")
    assert FORBIDDEN_RETURN in vulnerable_source[get_start:get_end]


def test_vulnerable_source_leaks_the_parser_exception(vulnerable_source):
    """The second, lower-severity finding, so the model has something to rank."""
    assert 'e.getMessage()' in vulnerable_source
    assert 'e.toString()' in vulnerable_source


def test_fixed_source_closes_the_authorisation_hole(fixed_source):
    body = statements_method(fixed_source)
    branch_start = body.index(OWNERSHIP_CHECK)
    branch_end = body.index("}", body.index("LOG.warnf", branch_start))
    assert FORBIDDEN_RETURN in body[branch_start:branch_end]


def test_fixed_source_stops_leaking_the_exception(fixed_source):
    assert 'e.getMessage()' not in fixed_source
    assert 'ISO-8601' in fixed_source, "expected a fixed, non-reflective message"


def test_base_source_predates_the_feature():
    """main starts here, so the pushed diff is the reviewed change."""
    from tests.conftest import DEMO_DIR
    base = (DEMO_DIR / "AccountResource.base.java").read_text()
    assert "statements(" not in base
    assert "public Response get(" in base
    assert base.count("class AccountResource") == 1
    assert base.rstrip().endswith("}")


# --------------------------------------------------------------------------
# the shipped tests must pass on the vulnerable code
# --------------------------------------------------------------------------

def test_app_test_suite_does_not_cover_cross_tenant_access():
    """Load-bearing for the narrative: 'the tests pass'.

    If someone adds a cross-tenant assertion to the app's own suite, the
    vulnerable commit starts failing its unit tests and Act 2 collapses - the
    point is that the tests are green and the code is still wrong.

    The cross-tenant assertions live here in CI instead, above.
    """
    from tests.conftest import REPO_ROOT
    suite = (REPO_ROOT / "components" / "vuln-demo-app" / "src" / "test" / "java"
             / "com" / "redhat" / "demo" / "accounts" / "AccountResourceTest.java").read_text()
    assert "ACC-2002" not in suite, \
        "a test touching Bob's account would fail on the vulnerable code"
    for forbidden in ("403", "FORBIDDEN", "statusCode(403)"):
        assert forbidden not in suite, f"{forbidden!r} in the app suite breaks Act 2"


def test_app_test_suite_only_asserts_the_happy_paths():
    from tests.conftest import REPO_ROOT
    suite = (REPO_ROOT / "components" / "vuln-demo-app" / "src" / "test" / "java"
             / "com" / "redhat" / "demo" / "accounts" / "AccountResourceTest.java").read_text()
    codes = set(re.findall(r"statusCode\((\d+)\)", suite))
    assert codes == {"200", "404"}, f"unexpected status assertions: {codes}"


# --------------------------------------------------------------------------
# the committed diffs must match the committed sources
# --------------------------------------------------------------------------

PATH = "src/main/java/com/redhat/demo/accounts/AccountResource.java"


def regenerate(before: str, after: str) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{PATH}", tofile=f"b/{PATH}", n=3))


def test_vulnerable_diff_matches_base_and_vulnerable_sources(
        vulnerable_diff, vulnerable_source):
    """Catches the classic drift: Java edited, .demo/*.diff not regenerated."""
    from tests.conftest import DEMO_DIR
    base = (DEMO_DIR / "AccountResource.base.java").read_text()
    assert diff_fingerprint(regenerate(base, vulnerable_source)) == \
        diff_fingerprint(vulnerable_diff), \
        "01-introduce-statements-endpoint.diff is stale - regenerate it"


def test_fix_diff_matches_vulnerable_and_fixed_sources(
        fixed_diff, vulnerable_source, fixed_source):
    assert diff_fingerprint(regenerate(vulnerable_source, fixed_source)) == \
        diff_fingerprint(fixed_diff), \
        "02-fix-ownership-check.diff is stale - regenerate it"


def test_fix_diff_adds_exactly_the_forbidden_return(fixed_diff):
    added = [l[1:].strip() for l in fixed_diff.splitlines()
             if l.startswith("+") and not l.startswith("+++")]
    assert FORBIDDEN_RETURN in added
    assert len(added) == 3, f"the fix should be three added lines, got {added}"


# --------------------------------------------------------------------------
# the replay cache must match the diffs
# --------------------------------------------------------------------------

def test_replay_entries_exist(replay_entries):
    assert len(replay_entries) >= 2


def test_replay_entries_validate_against_the_schema(replay_entries):
    """A malformed entry is skipped at load time, i.e. silently missing."""
    for entry in replay_entries:
        ModelResponse.model_validate(entry["response"])


def test_replay_entries_declare_provenance(replay_entries):
    for entry in replay_entries:
        assert entry.get("provenance") in ("captured", "hand-authored-seed"), \
            f"{entry.get('label')} has no honest provenance label"


def test_replay_fingerprints_match_the_committed_diffs(
        replay_entries, vulnerable_diff, fixed_diff):
    by_fp = {entry["fingerprint"] for entry in replay_entries}
    assert diff_fingerprint(vulnerable_diff) in by_fp, \
        "no replay entry matches the vulnerable diff - run scripts/capture-replay.sh"
    assert diff_fingerprint(fixed_diff) in by_fp, \
        "no replay entry matches the fix diff - run scripts/capture-replay.sh"


def test_replay_contains_all_markers_actually_match(
        replay_entries, vulnerable_diff, fixed_diff):
    """The stage safety net has to work. Verify each marker is really present."""
    haystacks = vulnerable_diff + fixed_diff
    for entry in replay_entries:
        for needle in entry.get("match", {}).get("contains_all", []):
            assert needle in haystacks, \
                f"marker {needle!r} in {entry['label']!r} matches neither diff"


def test_the_vulnerable_entry_reports_a_blocking_finding(replay_entries):
    """If this entry stops being critical, the pipeline stops blocking."""
    entries = [e for e in replay_entries
               if "vulnerable" in e.get("label", "").lower()]
    assert entries, "no vulnerable replay entry found"
    findings = entries[0]["response"]["findings"]
    assert any(f["severity"] == "critical" and f["confidence"] == "high"
               for f in findings), "the demo needs a critical, high-confidence finding"
    assert any(f["cwe"] == "CWE-639" for f in findings)


def test_the_clean_entry_reports_nothing(replay_entries):
    entries = [e for e in replay_entries if "fixed" in e.get("label", "").lower()
               or "clean" in e.get("label", "").lower()]
    assert entries, "no clean replay entry found"
    assert entries[0]["response"]["findings"] == [], \
        "the fix run must be clean or Act 3 does not happen"


def test_findings_point_at_real_lines(replay_entries, vulnerable_source):
    """A finding citing a line past the end of the file reads as a hallucination."""
    line_count = len(vulnerable_source.splitlines())
    for entry in replay_entries:
        for finding in entry["response"]["findings"]:
            assert 0 < finding["line"] <= line_count, \
                f"{entry['label']}: line {finding['line']} is outside the file"


def test_the_critical_finding_cites_the_ownership_check(
        replay_entries, vulnerable_source):
    """The cited line should be the check itself, give or take a line."""
    lines = vulnerable_source.splitlines()
    for entry in replay_entries:
        for finding in entry["response"]["findings"]:
            if finding["severity"] != "critical":
                continue
            window = "\n".join(lines[max(0, finding["line"] - 2):finding["line"] + 3])
            assert "ownerId()" in window or "ownership" in window, \
                f"critical finding cites line {finding['line']}, which is not the check"
