# Architecture

## 1. Flow

```
  ┌──────────────────────────────────────────────────────────────────────────┐
  │ Red Hat Developer Hub (Backstage)                                        │
  │  • Software Template "quarkus-lightwell-inward" scaffolds the repo       │
  │  • Component page shows: TPA SBOM card, AI review history, pipeline runs │
  └────────────────────────────────┬─────────────────────────────────────────┘
                                   │ git push
                                   ▼
  ┌──────────────────────────────────────────────────────────────────────────┐
  │ Gitea (in-cluster, keeps the demo self-contained)                        │
  └────────────────────────────────┬─────────────────────────────────────────┘
                                   │ webhook → EventListener
                                   ▼
  ┌──────────────────────────────────────────────────────────────────────────┐
  │ OpenShift Pipelines — pipeline: lightwell-inward                         │
  │                                                                          │
  │  1. fetch-source            git clone, resolve the diff vs base          │
  │  2. lightwell-verify        ✦ EXTENSION POINT: verify every dependency   │
  │                               against the Lightwell Network catalog —    │
  │                               cosign verify of the signed artifact +     │
  │                               SBOM presence. Fails on unremediated dep.  │
  │  3. sast-semgrep            deterministic SAST. PASSES on the planted    │
  │                               flaw — this is the point.                  │
  │  4. ai-code-review          ✦ POST diff → ai-code-review svc → RHOAI →   │
  │                               Foundation-Sec-8B-Reasoning. Structured    │
  │                               findings. CRITICAL ⇒ pipeline fails here.  │
  │  5. build                   Buildah → image in internal registry        │
  │  6. sign-and-attest         ✦ RHTAS/cosign: sign image, then attach the  │
  │                               AI verdict as an in-toto predicate         │
  │  7. sbom-publish            SBOM → Trusted Profile Analyzer              │
  │  8. acs-image-check         roxctl image check                           │
  └────────────────────────────────┬─────────────────────────────────────────┘
                                   │
                                   ▼
  ┌──────────────────────────────────────────────────────────────────────────┐
  │ RHACS admission controller                                               │
  │  policy "lightwell-inward-attestation-required":                         │
  │   deploy is REJECTED unless the image carries a verifiable               │
  │   lightwell-inward attestation with verdict=pass                        │
  └──────────────────────────────────────────────────────────────────────────┘
                                   ▲
                                   │ delivered as cluster policy, GitOps-managed
  ┌────────────────────────────────┴─────────────────────────────────────────┐
  │ RHACM Governance — Policy + Placement                                    │
  └──────────────────────────────────────────────────────────────────────────┘
```

Steps marked ✦ are the parts that do not exist today. Everything else is off-the-shelf.

## 2. Why an attestation and not a report

A scan result in a dashboard is advisory. Nobody can enforce it, nothing downstream can verify it, and it rots the moment the image is re-tagged.

Lightwell's own design answers this for dependencies: ship the fix *with a signature and an SBOM* so the receiving pipeline can verify it without trusting the courier. Lightwell Inward applies the identical move to the AI's judgment about first-party code.

Concretely, after the build we emit an in-toto v1 statement, sign it with Trusted Artifact Signer (Sigstore — Fulcio for the cert, Rekor for the transparency log), and attach it to the image:

```jsonc
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [{
    "name": "image-registry.openshift-image-registry.svc:5000/demo/accounts-api",
    "digest": { "sha256": "9f2a…" }
  }],
  "predicateType": "https://redhat.com/lightwell-inward/code-review/v1",
  "predicate": {
    "verdict": "pass",                      // pass | fail
    "scannedAt": "2026-10-15T09:41:22Z",
    "repository": "http://gitea.../demo/accounts-api",
    "commit": "4c1b9e7…",
    "baseCommit": "0ad3f11…",
    "reviewer": {
      "kind": "llm",
      "model": "fdtn-ai/Foundation-Sec-8B-Reasoning",
      "modelDigest": "sha256:1b7c…",        // the served artifact, pinned
      "servedBy": "openshift-ai/kserve",
      "promptVersion": "lightwell-inward/prompts@v3",
      "decoding": { "temperature": 0.0, "top_p": 1.0, "maxTokens": 1536 }
    },
    "scope": { "filesReviewed": 3, "hunks": 5, "linesAdded": 48, "linesRemoved": 6 },
    "findings": [],                          // empty on a pass
    "complementaryScanners": [
      { "name": "semgrep", "verdict": "pass",
        "note": "Ran in the same pipeline. Covers a different bug class." }
    ]
  }
}
```

Three properties matter, and they are the talk track:

1. **Attributable** — the model, its digest, the prompt version and the decoding parameters are all pinned. "The AI approved it" becomes a specific, reproducible claim.
2. **Revocable** — if `Foundation-Sec-8B-Reasoning` is later found blind to some bug class, you query Rekor for every artifact approved by that `modelDigest` and re-review exactly those. This is the same capability Lightwell Clearinghouse gives you for an embargoed dependency CVE, pointed inward.
3. **Enforceable** — RHACS checks it at admission. The claim has teeth outside the pipeline that made it.

Property 2 is the one that lands with a security audience. Lead with it if you only have time for one.

## 3. Model serving on RHOAI (CPU)

This build assumes no GPU, so KServe gets a **custom ServingRuntime** wrapping `llama.cpp`'s OpenAI-compatible server, running `Foundation-Sec-8B-Reasoning` at `Q4_K_M`.

```
InferenceService  foundation-sec-reasoning
  └── ServingRuntime  llamacpp-cpu-runtime
        image: ghcr.io/ggml-org/llama.cpp:server
        args:  --model /mnt/models/… --ctx-size 8192 --jinja
        resources: 8 CPU / 16Gi
        PVC: model-store  ← populated by a one-shot fetch Job from Hugging Face
```

Honest numbers on 8 vCPU: **~6-9 tok/s**, so a reasoning review of one diff is **30-90s**. That is fine for a narrated walkthrough and wrong for a tight stage slot. Hence the backend abstraction in the scanner:

| `SCANNER_BACKEND` | Behaviour | Use |
|---|---|---|
| `replay` | Serves previously-captured real output, keyed by diff SHA-256. Response labelled `REPLAY` in UI and attestation. | Stage default. Deterministic, instant. |
| `llamacpp` | Live CPU inference against the InferenceService. | The "yes it's really running" moment. |
| `vllm` | Live GPU inference, vLLM OpenAI endpoint. | Drop in a GPU node, flip the env var. |

The replay cache is generated by `scripts/capture-replay.sh` against the live model — it is a recording, not a fabrication. Keep that distinction crisp if asked.

**To go GPU:** add a GPU MachineSet, install the NFD + NVIDIA GPU operators, swap the ServingRuntime to `vllm-runtime` (`gitops/components/rhoai/servingruntime-vllm-gpu.yaml`, shipped but not synced), set `SCANNER_BACKEND=vllm`. Nothing else changes.

## 4. The scanner service

`components/ai-code-review/` — FastAPI, one meaningful endpoint.

```
POST /review
  { repository, commit, baseCommit, diff, files[] }
  →  { verdict, findings[], predicate, usage, backend }
```

Design decisions worth defending on stage:

- **Diff-scoped, not repo-scoped.** We review what the developer changed, with surrounding context pulled per-hunk. Keeps the prompt small (CPU budget), keeps findings actionable, and avoids drowning the signal in pre-existing debt.
- **Temperature 0, pinned prompt, JSON schema-constrained output.** A security gate that returns different answers on re-run is not a gate. Non-conforming output fails the step rather than being coerced.
- **The model never sees secrets and never writes code.** It returns findings. Remediation is a human action. (An agentic auto-fix variant is the obvious next demo — deliberately out of scope here so the trust story stays clean.)
- **Fail closed.** Model unreachable, timeout, or malformed response ⇒ `verdict: error` ⇒ pipeline fails. A security control that fails open is decoration.

Prompt lives in `app/prompts.py`, versioned. It asks for CWE, OWASP category, CVSS v3.1 vector, exploit sketch, and a minimal fix — the structure Cisco's own `ai-deep-sast` project converged on, which is a useful citation if challenged on prompt design.

## 5. Planted vulnerability

`components/vuln-demo-app` is a Quarkus REST service. The flaw, in `AccountResource.java`:

```java
if (!account.ownerId().equals(principal.getSubject())) {
    LOG.warnf("ownership mismatch on account %s", id);   // ← no return
}
return Response.ok(account.statements()).build();
```

The authorization check runs, logs, and then serves the data anyway. A `return` was lost in a refactor.

Why this specific bug:

- **Semgrep/SonarQube cannot see it.** There is no taint flow, no dangerous sink, no injection. The code is syntactically clean and the check is *present*. Pattern matching has nothing to match on.
- **The unit tests pass.** They cover the owner's happy path and a 404. Nobody wrote the cross-tenant test — which is itself the realistic failure.
- **It reads in four seconds on a slide**, and the room will groan, because everyone present has shipped this.
- **It is CWE-639 / OWASP A01:2021 Broken Access Control** — the #1 category, which makes the "SAST-clean is not secure" point without strawmanning SAST.

There is a second, lesser finding (a handler returning the raw exception message to the client, CWE-209) so the model has something to rank, demonstrating severity triage rather than a single binary answer.

`git` history in the scaffolded repo is arranged as: clean base → `feat: add statement listing` (introduces the flaw) → the fix commit sits in `fix/ownership-check` ready to merge on stage.
