"""The prompt is part of the signed claim, so changes to it are deliberate."""

from app.prompts import PROMPT_VERSION, build_messages
from app.schema import FileContext


def test_version_is_pinned():
    """This string lands in the attestation.

    If you change the prompt, bump this - otherwise two different reviews
    claim to have been produced the same way, and the attestation lies.
    """
    assert PROMPT_VERSION == "lightwell-inward/prompts@v4"


def test_messages_are_system_then_user():
    msgs = build_messages("repo", "abc", "def", "+x\n", [])
    assert [m["role"] for m in msgs] == ["system", "user"]


def test_system_prompt_scopes_out_dependencies():
    """Lightwell covers dependencies; this reviewer must not duplicate it."""
    system = build_messages("r", "c", "b", "+x\n", [])[0]["content"]
    assert "do not report findings about dependency versions" in system.lower()


def test_system_prompt_targets_what_sast_cannot_see():
    system = build_messages("r", "c", "b", "+x\n", [])[0]["content"]
    for topic in ("broken access control", "computed and then ignored",
                  "early returns lost in a refactor"):
        assert topic in system.lower()


def test_system_prompt_permits_an_empty_answer():
    """Without this the model invents findings to look useful."""
    system = build_messages("r", "c", "b", "+x\n", [])[0]["content"]
    assert "empty findings list is a valid" in system.lower()


def test_user_message_carries_the_diff_and_revisions():
    user = build_messages("myrepo", "commit1", "base1", "+DIFFMARKER\n", [])[1]["content"]
    assert "+DIFFMARKER" in user
    assert "myrepo" in user
    assert "commit1" in user
    assert "base1" in user


def test_unknown_base_is_stated_not_blank():
    user = build_messages("r", "c", None, "+x\n", [])[1]["content"]
    assert "(unknown)" in user


def test_full_file_content_is_included():
    """A discarded check is often only visible with the whole method in view."""
    files = [FileContext(path="A.java", content="class A { int FULLBODY; }")]
    user = build_messages("r", "c", "b", "+x\n", files)[1]["content"]
    assert "FULLBODY" in user
    assert "A.java" in user


def test_multiple_files_each_get_a_section():
    files = [FileContext(path="A.java", content="aaa"),
             FileContext(path="B.java", content="bbb")]
    user = build_messages("r", "c", "b", "+x\n", files)[1]["content"]
    assert user.count("## Full content of") == 2


def test_no_localizer_section_when_stage_a_absent():
    user = build_messages("r", "c", "b", "+x\n", [], localizer=None)[1]["content"]
    assert "Prior signal" not in user


def test_no_localizer_section_when_stage_a_found_nothing(localizer_result):
    """An empty prior is noise. Say nothing rather than 'it found no files'."""
    localizer_result.findings = []
    user = build_messages("r", "c", "b", "+x\n", [],
                          localizer=localizer_result)[1]["content"]
    assert "Prior signal" not in user


def test_localizer_section_states_its_limits(localizer_result):
    """The wording is load-bearing. An unqualified list of another model's
    findings invites rubber-stamping, which would correlate the two stages and
    make corroboration meaningless."""
    user = build_messages("r", "c", "b", "+x\n", [],
                          localizer=localizer_result)[1]["content"]
    # Collapse whitespace: the template hard-wraps, so substrings must not be
    # sensitive to where a line happens to break.
    flat = " ".join(user.split())
    assert "does NOT report line numbers, reasoning or remediation" in flat
    assert "not as a conclusion" in flat
    assert "is not thereby clean" in flat
    assert "944 CWE classes" in flat, "coverage must be stated so silence is discounted"
    assert "app.py" in flat
    assert "CWE-78" in flat
