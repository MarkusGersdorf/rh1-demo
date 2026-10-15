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
    exploit: str = Field(default="", description="Concrete request or input that abuses it.")
    fix: str = Field(default="", description="Smallest correct change.")


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
    error: str | None = None
