"""Lightwell Inward - AI code review gate.

POST a diff, get back structured security findings, a pass/fail verdict, and an
in-toto statement ready for Trusted Artifact Signer to sign.
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from .attest import build_statement
from .backends import BackendError, build_backend
from .prompts import PROMPT_VERSION
from .schema import ReviewRequest, ReviewResponse

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
)
LOG = logging.getLogger("lightwell-inward")

app = FastAPI(
    title="Lightwell Inward - AI Code Review",
    description="Security review of first-party code, as a signed attestation.",
    version="1.0.0",
)

BACKEND = build_backend()
HISTORY: deque[ReviewResponse] = deque(maxlen=25)

SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}


def gate(findings) -> str:
    """Decide pass/fail.

    Deliberately simple and entirely in the open: a finding fails the build if
    its severity is in FAIL_ON *and* its confidence clears MIN_CONFIDENCE. The
    confidence floor exists so a hedged "medium confidence, might be an issue"
    does not block a release - that is how security gates get switched off.
    """
    fail_on = {s.strip().lower() for s in os.getenv("FAIL_ON", "critical,high").split(",")}
    floor = CONFIDENCE_RANK.get(os.getenv("MIN_CONFIDENCE", "medium").lower(), 2)
    for finding in findings:
        if finding.severity.lower() in fail_on and \
                CONFIDENCE_RANK.get(finding.confidence.lower(), 0) >= floor:
            return "fail"
    return "pass"


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "backend": BACKEND.name}


@app.get("/readyz")
def readyz() -> JSONResponse:
    """Ready means the model can actually be reached.

    For the replay backend readiness is just "cache loaded" - there is no
    endpoint to probe.
    """
    if BACKEND.name == "replay":
        count = len(getattr(BACKEND, "entries", []))
        if count == 0:
            return JSONResponse({"status": "no replay entries"}, status_code=503)
        return JSONResponse({"status": "ok", "backend": "replay", "entries": count})

    endpoint = os.getenv("MODEL_ENDPOINT", "")
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{endpoint.rstrip('/')}/health")
        if resp.status_code < 400:
            return JSONResponse({"status": "ok", "backend": BACKEND.name})
        return JSONResponse(
            {"status": "model unhealthy", "code": resp.status_code}, status_code=503
        )
    except httpx.HTTPError as exc:
        return JSONResponse({"status": f"model unreachable: {exc}"}, status_code=503)


@app.post("/review", response_model=ReviewResponse)
def review(req: ReviewRequest) -> ReviewResponse:
    started = time.monotonic()
    LOG.info("review request repo=%s commit=%s diff_bytes=%d files=%d "
             "localizer=%s",
             req.repository, req.commit[:12], len(req.diff), len(req.files),
             f"{len(req.localizer.findings)} findings" if req.localizer else "absent")

    if not req.diff.strip():
        raise HTTPException(status_code=400, detail="diff is empty - nothing to review")

    try:
        parsed, usage, replayed = BACKEND.review(req)
    except BackendError as exc:
        # Fail closed. The pipeline step reads verdict and stops.
        LOG.error("backend failure: %s", exc)
        response = ReviewResponse(
            verdict="error",
            findings=[],
            backend=BACKEND.name,
            model=BACKEND.model,
            prompt_version=PROMPT_VERSION,
            latency_ms=int((time.monotonic() - started) * 1000),
            error=str(exc),
        )
        HISTORY.appendleft(response)
        return response

    # Mark which findings Stage A independently implicated. Recorded in the
    # predicate, deliberately NOT used to change the verdict: the two models
    # share a vendor and overlapping training data, so agreement is weaker
    # evidence than it feels like.
    if req.localizer is not None:
        flagged = req.localizer.files()
        for finding in parsed.findings:
            finding.corroborated_by_localizer = any(
                finding.file.endswith(f) or f.endswith(finding.file)
                for f in flagged if f
            )

    verdict = gate(parsed.findings)
    response = ReviewResponse(
        verdict=verdict,
        findings=sorted(
            parsed.findings,
            key=lambda f: -SEVERITY_RANK.get(f.severity.lower(), 0),
        ),
        summary=parsed.summary,
        backend=BACKEND.name,
        model=BACKEND.model,
        prompt_version=PROMPT_VERSION,
        latency_ms=int((time.monotonic() - started) * 1000),
        usage=usage,
        replayed=replayed,
        localizer=req.localizer,
    )
    response.predicate = build_statement(req, response)

    corroborated = sum(1 for f in response.findings if f.corroborated_by_localizer)
    LOG.info("review complete verdict=%s findings=%d (%d corroborated) "
             "latency_ms=%d replayed=%s",
             verdict, len(response.findings), corroborated,
             response.latency_ms, replayed)
    HISTORY.appendleft(response)
    return response


@app.get("/api/reviews")
def reviews() -> list[dict]:
    return [r.model_dump() for r in HISTORY]


SEV_COLOR = {
    "critical": "#e8503a", "high": "#f0862e", "medium": "#f4c145",
    "low": "#73bcf7", "info": "#8a8d90",
}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """A minimal operator view.

    Exists so the walkthrough has something to show on a projector that is not
    a Tekton log pane. Deliberately plain - the findings are the content.
    """
    if not HISTORY:
        body = ('<p class="empty">No reviews yet. Push a commit, or '
                '<code>POST /review</code>.</p>')
    else:
        cards = []
        for r in HISTORY:
            badge = {"pass": "ok", "fail": "bad", "error": "err"}[r.verdict]
            tags = []
            if r.replayed:
                tags.append('<span class="tag replay">REPLAY</span>')
            else:
                tags.append('<span class="tag live">LIVE</span>')
            findings_html = "".join(
                f'''<div class="finding" style="border-left-color:{SEV_COLOR.get(f.severity, "#888")}">
                  <div class="fhead"><span class="sev" style="color:{SEV_COLOR.get(f.severity, "#888")}">
                  {f.severity.upper()}</span> {_esc(f.title)}</div>
                  <div class="meta">{_esc(f.cwe)} &middot; {_esc(f.owasp)} &middot;
                  confidence {_esc(f.confidence)} &middot; <code>{_esc(f.file)}:{f.line}</code>
                  {' &middot; <span class="corr">also flagged by Stage A</span>' if f.corroborated_by_localizer else ''}</div>
                  <p>{_esc(f.rationale)}</p>
                  {f'<p class="x"><b>Exploit</b> {_esc(f.exploit)}</p>' if f.exploit else ""}
                  {f'<p class="fix"><b>Fix</b> {_esc(f.fix)}</p>' if f.fix else ""}
                </div>'''
                for f in r.findings
            )
            loc = r.localizer
            loc_html = ""
            if loc is not None:
                items = "".join(
                    f'<li><code>{_esc(f.file)}</code> &middot; {_esc(f.cwe)} '
                    f'&middot; likelihood {_esc(f.likelihood)}</li>'
                    for f in loc.findings
                ) or "<li>no files flagged</li>"
                loc_html = (
                    f'<div class="stage"><div class="stagehead">Stage A &middot; '
                    f'{_esc(loc.tool)} ({_esc(loc.model)}) &middot; '
                    f'file-level localization only</div>'
                    f'<ul>{items}</ul>'
                    f'<div class="meta">checked {len(loc.cwes_checked)} of '
                    f'{loc.cwes_available or "?"} CWE classes &middot; '
                    f'{loc.duration_seconds:.0f}s'
                    f'{" &middot; coverage truncated" if loc.truncated else ""}'
                    f'</div></div>'
                )
            cards.append(f'''<section class="card">
              <header>
                <span class="verdict {badge}">{r.verdict.upper()}</span>
                {"".join(tags)}
                <span class="lat">{r.latency_ms} ms</span>
              </header>
              <div class="meta">{_esc(r.model)} &middot; {_esc(r.prompt_version)}
                &middot; backend <code>{_esc(r.backend)}</code></div>
              {f'<p class="summary">{_esc(r.summary)}</p>' if r.summary else ""}
              {f'<p class="err">{_esc(r.error)}</p>' if r.error else ""}
              {loc_html}
              {f'<div class="stagehead">Stage B &middot; {_esc(r.model)} &middot; reasoning</div>' if r.findings else ""}
              {findings_html}
            </section>''')
        body = "".join(cards)

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lightwell Inward</title>
<meta http-equiv="refresh" content="10">
<style>
  :root {{ color-scheme: dark; --bg:#151515; --panel:#1f1f1f; --line:#3d3d3d;
           --fg:#e0e0e0; --dim:#9a9a9a; }}
  * {{ box-sizing:border-box }}
  body {{ margin:0; padding:24px 16px; background:var(--bg); color:var(--fg);
         font:15px/1.6 "Red Hat Text", "Red Hat Display", system-ui, sans-serif }}
  .wrap {{ max-width:960px; margin:0 auto }}
  h1 {{ font-size:22px; margin:0 0 4px; font-weight:600 }}
  .sub {{ color:var(--dim); margin:0 0 28px; font-size:14px }}
  .card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px;
          padding:18px; margin-bottom:18px }}
  .card header {{ display:flex; align-items:center; gap:10px; margin-bottom:8px; flex-wrap:wrap }}
  .verdict {{ font-weight:700; font-size:13px; letter-spacing:.06em; padding:3px 10px;
             border-radius:4px }}
  .verdict.ok {{ background:#1e4620; color:#7ee787 }}
  .verdict.bad {{ background:#5c1d14; color:#ff9b8a }}
  .verdict.err {{ background:#4d3b00; color:#f4c145 }}
  .tag {{ font-size:11px; padding:2px 7px; border-radius:3px; letter-spacing:.05em }}
  .tag.replay {{ background:#2b2b4a; color:#b3b3f0 }}
  .tag.live {{ background:#14372b; color:#7ee7c0 }}
  .lat {{ margin-left:auto; color:var(--dim); font-size:12px }}
  .meta {{ color:var(--dim); font-size:12.5px }}
  .summary {{ margin:10px 0 14px }}
  .finding {{ border-left:3px solid #888; background:#191919; padding:12px 14px;
             margin:12px 0; border-radius:0 6px 6px 0 }}
  .fhead {{ font-weight:600; margin-bottom:4px }}
  .sev {{ font-size:12px; font-weight:700; letter-spacing:.05em; margin-right:6px }}
  .finding p {{ margin:8px 0 0; font-size:14px }}
  .x {{ color:#ffb3a7 }} .fix {{ color:#9be89b }} .err {{ color:#f4c145 }}
  code {{ background:#2a2a2a; padding:1px 5px; border-radius:3px; font-size:12.5px }}
  .empty {{ color:var(--dim) }}
  .stage {{ background:#191919; border:1px solid var(--line); border-radius:6px;
           padding:10px 12px; margin:12px 0 }}
  .stagehead {{ font-size:12px; letter-spacing:.04em; color:var(--dim);
               text-transform:uppercase; margin:14px 0 4px }}
  .stage ul {{ margin:6px 0 8px; padding-left:18px; font-size:13.5px }}
  .corr {{ color:#7ee7c0 }}
  @media (max-width:600px) {{ body {{ padding:16px }} .lat {{ margin-left:0 }} }}
</style></head>
<body><div class="wrap">
  <h1>Lightwell Inward</h1>
  <p class="sub">AI security review of first-party code &middot; findings become a
  signed in-toto attestation &middot; backend <code>{_esc(BACKEND.name)}</code></p>
  {body}
</div></body></html>"""


def _esc(value) -> str:
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))
