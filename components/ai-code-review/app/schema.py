"""Wire types for the review API.

The finding shape is deliberately close to what Cisco's own `ai-deep-sast`
project settled on (CWE + OWASP + CVSS + exploit sketch + minimal fix). That
is not imitation for its own sake: those fields are what a reviewer needs to
act, and what a policy engine needs to decide.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["critical", "high", "medium", "low", "info"]
Confidence = Literal["high", "medium", "low"]
Verdict = Literal["pass", "fail", "error"]


class Finding(BaseModel):
    title: str = Field(description="One line, specific. Not 'security issue'.")
    severity: Severity
    confidence: Confidence
    cwe: str = Field(description="e.g. CWE-639")
    owasp: str = Field(description="e.g. A01:2021-Broken Access Control")
    cvss_vector: str = Field(default="", description="CVSS v3.1 vector string")
    cvss_score: float = Field(default=0.0, ge=0.0, le=10.0)
    file: str
    line: int = Field(default=0, ge=0)
    rationale: str = Field(description="Why this is exploitable, in terms of this code.")
    # True when Stage A independently implicated the same file. Recorded, not
    # acted on: the two models share a vendor and overlapping training data, so
    # agreement is weaker evidence than it looks.
    corroborated_by_localizer: bool = False
    exploit: str = Field(default="", description="Concrete request or input that abuses it.")
    fix: str = Field(default="", description="Smallest correct change.")


class LocalizerFinding(BaseModel):
    """One Antares finding. File-level: there is no line, by design."""

    title: str
    file: str
    cwe: str = ""
    cwes: list[str] = Field(default_factory=list)
    likelihood: str = "unknown"
    rank: int = 0


class LocalizerResult(BaseModel):
    """Stage A output, recorded in the attestation as a second reviewer."""

    tool: str = "antares-cli"
    model: str = "unknown"
    mode: str = "unknown"
    findings: list[LocalizerFinding] = Field(default_factory=list)
    # How much of the CWE catalogue was actually examined. "Clean" after three
    # of 944 CWEs is a far smaller claim than "clean" unqualified.
    cwes_checked: list[str] = Field(default_factory=list)
    cwes_available: int = 0
    truncated: bool = False
    duration_seconds: float = 0.0
    tool_calls: int = 0
    failed_tool_calls: int = 0
    incomplete_reason: str | None = None
    request_hash: str | None = None
    model_digest: str | None = None
    decoding: dict = Field(default_factory=dict)

    def files(self) -> set[str]:
        return {f.file for f in self.findings if f.file}


class FileContext(BaseModel):
    path: str
    content: str


class ReviewRequest(BaseModel):
    repository: str
    commit: str
    base_commit: str | None = None
    diff: str
    files: list[FileContext] = Field(default_factory=list)
    # Set by the pipeline so the attestation can name its subject.
    image: str | None = None
    image_digest: str | None = None
    # Results from the other scanners in the same pipeline, recorded in the
    # attestation. The AI review is one input among several and the signed
    # claim should say so rather than imply it stood alone.
    complementary: list[dict] = Field(default_factory=list)
    # Stage A output, passed as a prior. Stage B still reviews the whole diff -
    # gating the explanation on a localizer hit would let one model's miss
    # silently disable the other.
    localizer: LocalizerResult | None = None


class ModelResponse(BaseModel):
    """What the LLM is constrained to emit."""

    findings: list[Finding] = Field(default_factory=list)
    summary: str = ""


class ReviewResponse(BaseModel):
    verdict: Verdict
    findings: list[Finding]
    summary: str = ""
    predicate: dict | None = None
    backend: str
    model: str
    prompt_version: str
    latency_ms: int
    usage: dict = Field(default_factory=dict)
    # True when served from the replay cache. Surfaced in the UI and in the
    # attestation so a recorded answer is never mistaken for a live one.
    replayed: bool = False
    localizer: LocalizerResult | None = None
    error: str | None = None
