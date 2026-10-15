"""diff_fingerprint must be stable across the noise that changes every run.

If this normalisation drifts, the replay cache silently misses on stage and
the gate returns `error` in front of an audience. These tests are the guard.
"""

from app.backends import diff_fingerprint


def test_identical_diff_same_fingerprint(vulnerable_diff):
    assert diff_fingerprint(vulnerable_diff) == diff_fingerprint(vulnerable_diff)


def test_vulnerable_and_fixed_differ(vulnerable_diff, fixed_diff):
    assert diff_fingerprint(vulnerable_diff) != diff_fingerprint(fixed_diff)


def test_blob_hashes_ignored():
    """`index 62b81b6..95aa027` changes with every commit; content does not."""
    a = "diff --git a/x b/x\nindex 62b81b6..95aa027 100644\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n+int x = 1;\n"
    b = "diff --git a/x b/x\nindex aaaaaaa..bbbbbbb 100644\n--- a/x\n+++ b/x\n@@ -9 +9 @@\n+int x = 1;\n"
    assert diff_fingerprint(a) == diff_fingerprint(b)


def test_hunk_offsets_ignored():
    a = "@@ -1,5 +1,9 @@\n+added line\n"
    b = "@@ -40,5 +44,9 @@\n+added line\n"
    assert diff_fingerprint(a) == diff_fingerprint(b)


def test_trailing_whitespace_ignored():
    assert diff_fingerprint("+int x = 1;\n") == diff_fingerprint("+int x = 1;   \n")


def test_blank_lines_ignored():
    assert diff_fingerprint("+a\n+b\n") == diff_fingerprint("+a\n\n\n+b\n")


def test_substantive_change_moves_fingerprint():
    assert diff_fingerprint("+int x = 1;\n") != diff_fingerprint("+int x = 2;\n")


def test_removed_line_matters():
    """A deletion is a real change, even though '-' lines are kept verbatim."""
    assert diff_fingerprint("+a\n-b\n") != diff_fingerprint("+a\n")


def test_empty_diff_is_stable():
    assert diff_fingerprint("") == diff_fingerprint("\n\n\n")
