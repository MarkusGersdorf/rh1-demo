"""Versioned prompts.

The version string lands in the signed attestation. Change the prompt, change
the version - otherwise you have an unreproducible security claim, which is
the thing this whole demo argues against.
"""

PROMPT_VERSION = "lightwell-inward/prompts@v4"

SYSTEM = """\
You are a senior application security engineer reviewing a code change before \
it is built into a release artifact. You are the last gate before signing.

You are reviewing FIRST-PARTY code - code written by this organisation's \
developers or by an AI assistant on their behalf. Third-party dependencies are \
already covered by a separate, trusted remediation process; do not report \
findings about dependency versions or known CVEs in libraries.

Concentrate on what pattern-matching static analysis structurally cannot see:

  - Broken access control: authorisation checks that are missing, checked \
against the wrong subject, or whose result is computed and then ignored.
  - Logic flaws: conditions inverted, comparisons on the wrong variable, \
early returns lost in a refactor, error branches that fall through to success.
  - Authentication and session handling: token validation that omits issuer, \
audience, expiry or algorithm; privilege decisions made from client-supplied data.
  - Unsafe data exposure: internal state, stack traces or other users' records \
reaching the response.
  - Injection and deserialisation where the sink is reached through non-obvious \
control flow.
  - Cryptographic misuse: weak primitives, static IVs, non-constant-time \
comparison of secrets.

Rules you must follow:

  1. Report only what you can justify from the code shown. No speculation \
about code you cannot see.
  2. Severity reflects real-world impact given this code's apparent purpose, \
not a generic category score.
  3. Set confidence honestly. "high" means you can name the exact line and the \
exact consequence. If you are reasoning about probable surrounding behaviour, \
that is "medium" at best.
  4. Style, naming, performance and test coverage are out of scope. Security only.
  5. An empty findings list is a valid and expected answer for a clean change. \
Do not invent a finding to appear useful.
  6. Point `file` and `line` at the vulnerable line in the post-change file, \
not at the diff offset.
"""

USER_TEMPLATE = """\
Repository: {repository}
Commit under review: {commit}
Compared against: {base_commit}
{localizer_section}
## The change

```diff
{diff}
```
{file_sections}
Review the change. Respond with JSON matching the required schema and nothing else.
"""

LOCALIZER_TEMPLATE = """
## Prior signal from a separate tool

A file-level vulnerability localizer ({tool}, {model}) was run first. It reports
which files it believes contain which CWE classes. It does NOT report line
numbers, reasoning or remediation, and it is a different model from you.

{findings}

Treat this as a hint about where to look, not as a conclusion:

  - Confirm or contradict each entry from the code itself. If you cannot see
    the weakness it names, say nothing about that entry rather than inventing
    support for it.
  - A file it did not flag is not thereby clean. Review the whole change.
  - It checked {cwes_checked} of {cwes_available} CWE classes, so its silence
    carries very little information.
"""

FILE_SECTION_TEMPLATE = """
## Full content of `{path}` after the change

```
{content}
```
"""


def _localizer_section(localizer) -> str:
    """Render Stage A output as a prior, with its limits stated.

    The wording matters. An unqualified list of "findings" from another model
    invites the reasoner to rubber-stamp them, which would make the two stages
    correlated and the corroboration signal worthless.
    """
    if localizer is None or not localizer.findings:
        return ""
    lines = "\n".join(
        f"  - `{f.file}` — {f.cwe} {f.title} (likelihood: {f.likelihood})"
        for f in localizer.findings
    )
    return LOCALIZER_TEMPLATE.format(
        tool=localizer.tool,
        model=localizer.model,
        findings=lines,
        cwes_checked=len(localizer.cwes_checked),
        cwes_available=localizer.cwes_available or "an unknown number of",
    )


def build_messages(repository: str, commit: str, base_commit: str | None,
                   diff: str, files: list, localizer=None) -> list[dict]:
    """Assemble the chat messages.

    Full file content is included for changed files, not just the hunks: an
    authorisation bug is frequently only visible when you can see that the
    check's result goes nowhere, which a three-line context window hides.
    """
    sections = "".join(
        FILE_SECTION_TEMPLATE.format(path=f.path, content=f.content) for f in files
    )
    return [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": USER_TEMPLATE.format(
                repository=repository,
                commit=commit,
                base_commit=base_commit or "(unknown)",
                diff=diff,
                file_sections=sections,
                localizer_section=_localizer_section(localizer),
            ),
        },
    ]
