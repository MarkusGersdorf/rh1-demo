# Lightwell Inward

[![CI](https://github.com/MarkusGersdorf/rh1-demo/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/MarkusGersdorf/rh1-demo/actions/workflows/ci.yml)
[![coverage](https://img.shields.io/badge/scanner_coverage-%E2%89%A590%25-brightgreen)](components/ai-code-review/pyproject.toml)
[![OpenShift](https://img.shields.io/badge/OpenShift-4.22-EE0000?logo=redhat&logoColor=white)](docs/SETUP.md)

**A Red Hat One demo: extending the Lightwell trust model from the dependencies you consume to the code your developers write.**

Runs on OpenShift 4.22. GitOps-managed end to end. Walked through manually on stage.

---

## The argument

In May 2026 IBM and Red Hat committed $5B to [Project Lightwell](https://www.redhat.com/en/about/press-releases/project-lightwell-secure-open-source) — 20,000 engineers and frontier AI aimed at securing open source. In July the commercial offerings landed: **Lightwell Network** ships "a continuous stream of digitally signed binaries, source code, and comprehensive compliance artifacts, including complete SBOMs, delivered directly into existing pipelines," and **Lightwell Clearinghouse Premier** coordinates embargoed patches. 6,500+ remediated dependencies at launch, 400+ previously-unknown Java CVEs fixed.

That is a genuinely new piece of trust infrastructure. And it covers **everything in your application except the part you wrote.**

```
   ┌─────────────────────────────────────────────────┐
   │  Your running application                       │
   ├─────────────────────────────────────────────────┤
   │  ~85-95%  open source dependencies  ← LIGHTWELL │
   │                                       signed,   │
   │                                       remediated│
   │                                       SBOM'd    │
   ├─────────────────────────────────────────────────┤
   │  ~5-15%   first-party code          ← ???       │
   │           (increasingly AI-generated)           │
   └─────────────────────────────────────────────────┘
```

Lightwell's insight is not "scan more." It is that a **security claim becomes useful when it is signed, attestable, and enforceable at deploy time.** A remediated dependency you cannot verify is just a rumour.

So: apply that same pattern inward. Treat an AI security review of a developer's diff as a **first-class, cryptographically signed supply-chain attestation** — and refuse to admit any workload to the cluster that cannot produce one.

That is Lightwell Inward.

## What the demo shows

A developer writes a one-line authorization bug. It passes code review by eye, passes the unit tests, and passes Semgrep SAST clean. Then:

1. **Cisco's `Foundation-Sec-8B-Reasoning`** — an open-weight security reasoning model — served on **Red Hat OpenShift AI**, reads the diff and explains exactly why it is a broken access control, with CWE, CVSS and a working attack payload.
2. The pipeline **fails closed**. No artifact is built.
3. The developer fixes it. On the clean run, **Red Hat Trusted Artifact Signer** signs the image *and* attaches the AI review verdict as an in-toto attestation predicate.
4. **RHACS** admission control rejects any image lacking a passing `lightwell-inward` attestation — proven live by trying to deploy the vulnerable build directly.
5. **RHACM** distributes that policy as GitOps-managed cluster policy, so the gate is a fleet property, not a pipeline convention.

The whole point: *the AI's judgment is not advice in a web UI. It is a signed claim that gates production.*

## Red Hat components

| Component | Role in the demo |
|---|---|
| **Red Hat Developer Hub** | Where the developer lives. Software Template scaffolds the app; the AI findings surface on the component page. |
| **Red Hat Advanced Developer Suite** | Trusted Artifact Signer (sign + attest), Trusted Profile Analyzer (SBOM/VEX). The "build and sign artifacts" product. |
| **OpenShift Pipelines** (Tekton) | The pipeline that orchestrates verify → SAST → AI review → build → sign → attest. |
| **Red Hat OpenShift AI** | Serves the Cisco security model via KServe with a custom llama.cpp ServingRuntime. |
| **RHACS** | Image scanning, and the admission policy that enforces the attestation. |
| **RHACM** | Governance: ships the policy and the gate as GitOps cluster policy. |
| **OpenShift GitOps** (Argo CD) | Installs and owns all of the above. App-of-apps, sync waves. |

## Repo layout

```
docs/
  ARCHITECTURE.md      how the pieces fit, and the attestation format
  SETUP.md             cluster build, in order, with verification gates
  DEMO-SCRIPT.md       the stage walkthrough: clicks, talk track, recovery
.github/workflows/
  ci.yml               tests, manifest validation, image builds (no push)
  publish-scanner.yml  manual-only GHCR publish, for disconnected clusters
gitops/
  bootstrap/           the two things you apply by hand
  apps/                Argo CD app-of-apps
  components/          every operator, CR and workload
components/
  ai-code-review/      the scanner service (FastAPI + model backends + attestation)
    tests/             133 tests, incl. demo-integrity guards
  vuln-demo-app/       Quarkus app with the planted flaw
scripts/               bootstrap, model fetch, demo reset
```

## What CI checks

The badge covers four jobs in [`ci.yml`](.github/workflows/ci.yml):

| Job | Protects |
|---|---|
| `scanner` | 133 tests over the gate logic, replay matching, attestation shape, and the fail-closed guarantee |
| `demo-app` | `mvn verify` — the three shipped tests must stay green **on the vulnerable code**, because Act 2 of the walkthrough depends on that |
| `manifests` | Every YAML parses, every kustomization builds, every Argo CD Application points at a directory that exists, shell and embedded Tekton Python parse |
| `images` | Both Containerfiles build (nothing is pushed — see [docs/SETUP.md](docs/SETUP.md#do-we-need-to-build-and-push-images-in-ci)) |

A green badge means the demo's *premise* still holds — that the planted flaw is
still planted, the committed diffs still match the sources, and the replay
cache still matches the diffs. It does not mean the cluster is healthy; that is
what `scripts/verify.sh` is for.

The coverage badge is static because it reflects the floor enforced by
`fail_under` in [`pyproject.toml`](components/ai-code-review/pyproject.toml),
not the current figure — so it cannot drift out of date without CI failing.
Actual coverage is 98%.

## Start here

- Building the cluster: **[docs/SETUP.md](docs/SETUP.md)**
- Running it on stage: **[docs/DEMO-SCRIPT.md](docs/DEMO-SCRIPT.md)**
- Understanding the design: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**

## Honesty notes

Carry these into the room; a security audience will ask.

- **Lightwell Inward is a demo concept, not a Red Hat product.** It extends a real product with an idea. Say so.
- **The model runs quantized on CPU** in this build (no GPU assumed). It is the real Cisco model and real inference, just slow — 30-90s per review. The stage path uses a replay cache of *actual captured model output* for determinism; the UI labels it `REPLAY` so you are never claiming live inference when it isn't. One env var switches to live.
- **An LLM is not a soundness proof.** It finds classes of bug SAST structurally cannot (intent/logic) and misses things SAST catches trivially. The demo runs both, on purpose. Do not let the story become "AI replaces SAST."
- **Attesting an AI verdict does not make the verdict correct.** It makes it *accountable* — you know which model, which prompt, which commit, and you can revoke. That is the actual contribution, and it is the Lightwell lesson.
