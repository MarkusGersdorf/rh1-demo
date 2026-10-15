# AGENTS.md

This file provides guidance to coding agents working in this repository.

`CLAUDE.md` is a symlink to this file, so Claude Code (claude.ai/code) picks it
up automatically. Edit this file, not the link.

## What this repo is

A **demo for a Red Hat One tech event**, not a production system. It is walked
through manually on stage in ~22 minutes. "Lightwell Inward" is a demo concept
extending a real product (IBM/Red Hat Lightwell) with an idea — it is not a
Red Hat product, and `README.md` says so deliberately.

Target: OpenShift 4.22, single cluster, **no GPU**. Everything on the cluster is
installed by Argo CD from this repo.

**The stage narrative is load-bearing.** Several things that look like bugs or
omissions are deliberate and protected by tests. Read "Invariants" before
changing anything under `components/`.

## Commands

### Scanner service (the main codebase)

Use **Python 3.12**, matching the `ubi9/python-312` base image. The pinned
`pydantic` has no wheel for 3.14, so a default `python3` on a current macOS
fails to install (`pydantic-core` build error).

```sh
cd components/ai-code-review
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt

pytest                                          # 133 tests
pytest tests/test_api.py                        # one file
pytest tests/test_api.py::test_empty_diff_is_rejected   # one test
pytest -k replay                                # by expression
pytest tests/test_demo_integrity.py             # the demo's premise
pytest --cov=app --cov-report=term-missing      # coverage (floor 90%, actual ~98%)
```

Run the service locally:

```sh
SCANNER_BACKEND=replay REPLAY_DIR=replay uvicorn app.main:app --reload --port 8080
```

### Demo app

```sh
cd components/vuln-demo-app
mvn -B verify          # the three shipped tests MUST pass on the vulnerable code
```

Needs JDK 21 and the Red Hat GA Maven repo (declared in `pom.xml` — the Red Hat
build of Quarkus is not on Central).

**Never guess a Red Hat GA version string.** They are not derivable: the
platform BOM carries a different `.redhat-NNNNN` suffix from the artifacts it
manages (currently BOM `3.20.6.redhat-00004`, artifacts `3.20.6.redhat-00002`),
and plausible-looking suffixes simply do not exist. Query the repository:

```sh
curl -s https://maven.repository.redhat.com/ga/com/redhat/quarkus/platform/quarkus-bom/maven-metadata.xml \
  | grep -o '<version>[^<]*' | sed 's/<version>//' | tail -20
```

Then confirm both artifacts actually exist before committing, because `mvn`
is the only thing that catches this and it is the slowest job in CI:

```sh
V=3.20.6.redhat-00004
B=https://maven.repository.redhat.com/ga/com/redhat/quarkus/platform
curl -o /dev/null -w '%{http_code}\n' -s "$B/quarkus-bom/$V/quarkus-bom-$V.pom"
curl -o /dev/null -w '%{http_code}\n' -s "$B/quarkus-maven-plugin/$V/quarkus-maven-plugin-$V.jar"
```

If you change a dependency, also add it to the Lightwell catalog ConfigMap
(`gitops/components/pipelines/lightwell-catalog.yaml`) or it prints as
"uncovered" on stage, and update the dependency count in the
`lightwell-verify` talk track in `docs/DEMO-SCRIPT.md`.

### Manifest validation

What CI's `manifests` job runs; worth running before committing GitOps changes:

```sh
# every kustomization builds
for d in gitops/components/*/; do kubectl kustomize "$d" >/dev/null || echo "FAIL $d"; done

# every YAML parses (needs pyyaml)
python3 -c "
import glob,yaml
for f in glob.glob('gitops/**/*.yaml',recursive=True): list(yaml.safe_load_all(open(f)))
print('ok')"

for f in scripts/*.sh; do bash -n "$f" || echo "FAIL $f"; done
```

### Cluster lifecycle

Order matters; see `docs/SETUP.md` for what each one waits on.

```sh
./scripts/00-prereqs.sh            # fails loudly on expensive-to-discover problems
./scripts/01-set-cluster-domain.sh # resolve placeholders, then COMMIT AND PUSH
./scripts/bootstrap.sh             # GitOps operator + root app-of-apps
./scripts/02-signing-setup.sh      # cosign keypair, RHDH secrets
./scripts/03-acs-init-bundle.sh    # RHACS SecuredCluster init bundle
./scripts/04-seed-gitea.sh         # repo, webhook, local clone at the base commit
./scripts/05-acs-policy.sh         # signature integration + policy check
./scripts/06-build-scanner.sh      # in-cluster image build
./scripts/capture-replay.sh        # record real model output (do not skip)
./scripts/verify.sh                # pre-flight; run before going on stage
```

Stage flow: `demo-push-vulnerable.sh` → `demo-push-fix.sh` →
`demo-rogue-deploy.sh` → `demo-deploy-signed.sh` → `demo-reset.sh`.

## Architecture

### The argument the code implements

Lightwell signs and remediates the **dependencies you consume**. Nothing covers
**first-party code**. This repo applies Lightwell's pattern inward: an AI
security review of a developer's diff becomes a **signed in-toto attestation**
that gates admission and is **revocable by model digest**. The review is the
least novel part; the attestation is the point.

Pipeline (`gitops/components/pipelines/pipeline.yaml`, 8 tasks):

```
fetch-source → lightwell-verify → sast-semgrep → ai-code-review
                                                      ↓ fail ⇒ no artifact
                                   build-image → sign-and-attest → ec-validate
                                               → acs-image-check
```

- `lightwell-verify` — the half Lightwell covers. **Passes.**
- `sast-semgrep` — **passes clean on the planted flaw.** This is the point, not
  a Semgrep failure: there is no taint flow and the check is syntactically
  present, so pattern matching has nothing to match.
- `ai-code-review` — **blocks.** Calls the scanner service.
- `sign-and-attest` — RHTAS/cosign signs the image *and* attaches the verdict.
- `ec-validate` — Enterprise Contract evaluates rego against the predicate.
  RHACS can only verify *that* an image is signed; EC reads inside the claim.

Enforcement chain worth understanding before touching the policies: only the
pipeline holds the signing key, and it only signs on a pass — so "signed by us"
*implies* "reviewed and passed". RHACS enforces the signature at admission;
RHACM asserts the RHACS policy, admission control, and the absence of a
break-glass annotation, continuously.

### Scanner service (`components/ai-code-review/`)

FastAPI, one meaningful endpoint (`POST /review`). Backends behind one
OpenAI-compatible interface, selected by `SCANNER_BACKEND`:

| | |
|---|---|
| `replay` | Captured real output, keyed by normalised diff. Stage default (CPU inference is 30–90s). |
| `llamacpp` | Live CPU inference via the KServe InferenceService. |
| `vllm` | Live GPU inference. |

`app/attest.py` builds the in-toto statement and is the actual thesis of the
demo — everything else is plumbing to get a model's opinion into that structure.

### Model serving

`Foundation-Sec-8B-Reasoning` (Cisco, open weights) on RHOAI via KServe with a
**custom llama.cpp ServingRuntime** at Q4_K_M, because there is no GPU. KServe
runs in **RawDeployment** mode, which deliberately avoids installing OpenShift
Serverless and Service Mesh. The GPU path (`servingruntime-vllm-gpu.yaml`) ships
but is not in the kustomization.

### GitOps

App-of-apps. Two things applied by hand (`gitops/bootstrap/`), everything else
reconciled. Sync waves: `-10` namespaces → `-5` subscriptions → `0` operator CRs
→ `5` Gitea → `6` model fetch → `10` scanner+pipeline → `15` RHACM policies.
Operator CRs are applied in the same pass as the operators defining their CRDs,
so the first sync shows transient "no matches for kind" errors and converges on
retry. That is expected — do not hand-apply to "fix" it.

## Invariants

Breaking any of these breaks the demo on stage rather than in CI, except where
a test catches it.

**If you edit `AccountResource.java`, you must regenerate `.demo/*.diff` and
re-run `scripts/capture-replay.sh`.** The chain
`AccountResource.java → .demo/*.diff → replay/*.json → the finding` is asserted
by `tests/test_demo_integrity.py`. Run it before trusting the demo again.

**The planted flaw must stay planted.** In `statements()`, the ownership branch
logs and must have **no `return`**. `get()` ten lines above must keep returning
403 — that contrast carries the demo.

**The app's own test suite must not test cross-tenant access.** Act 2 is "the
tests are green and the code is still wrong." A `403` assertion in
`src/test/` makes the vulnerable commit fail its unit tests and the narrative
collapses. Those assertions live in `test_demo_integrity.py` instead.

**Fail closed.** Model unreachable, timeout, malformed output, or an unmatched
replay with no fallback ⇒ `verdict: error` ⇒ pipeline fails. No configuration
may turn a backend error into a pass; if you add a setting, extend
`test_api.py::test_no_configuration_makes_a_backend_error_pass`.

**Never commit a private key.** `sast-semgrep` runs `--config=p/secrets`; a hit
would make SAST non-clean and destroy Act 2. Only `publicKey.pem` is committed.

**The predicate shape is coupled to rego.** Renaming a field in
`app/attest.py` requires the same change in the `ec-validate` task in
`gitops/components/pipelines/tasks.yaml`. Pinned by
`test_attest.py::test_predicate_keys_match_the_enterprise_contract_rego`.

**Replay provenance must stay honest.** Entries are `captured` (real model) or
`hand-authored-seed` (worked example shipped so the pipeline is testable before
the model is up). The service logs a warning per seed, the UI labels every
replay `REPLAY`, and the flag reaches the signed predicate. Never present a
recorded answer as live inference.

**Env vars are read at call time, not module import.** Deliberate — module-level
reads could not be reconfigured without a module reload and caused test
pollution. Keep new config reads inside functions or `__init__`.

**`apps.CLUSTER_DOMAIN` is intentional on `main`** and is the only placeholder
left; the repo slug is already resolved. `scripts/01-set-cluster-domain.sh`
rewrites both (the slug from `git remote`, for forks) across **only**
`gitops/**/*.yaml` and `README.md` — prose under `docs/` and the `README.md`
files inside `gitops/` describe the placeholders and must not be rewritten.
CI asserts `components/` never contains them.

Anything that *checks* for unresolved placeholders must match the substituted
strings (`apps\.CLUSTER_DOMAIN`, `CHANGEME/rh1-demo`) in `*.yaml` only. Two
separate bugs came from grepping the bare tokens across all files: the setter
reported its own documentation as unresolved work, and `bootstrap.sh` refused
to run because a README under `gitops/` mentions `CHANGEME`.

## CI

`.github/workflows/ci.yml` — four jobs: `scanner` (pytest + coverage),
`demo-app` (`mvn verify`), `manifests` (YAML, kustomize, Application paths,
shell, embedded Tekton Python), `images` (both Containerfiles build).

**CI builds images but never pushes them,** on purpose: the scanner is built by
an in-cluster BuildConfig, and the demo app is built by the Tekton pipeline —
which is the demo. An `accounts-api` image from GitHub Actions would carry no
attestation and RHACS would refuse it. `publish-scanner.yml` is
`workflow_dispatch`-only, for disconnected clusters.

## Deliberate stand-ins

Be precise about these if asked; they are documented in-place and in
`docs/DEMO-SCRIPT.md`.

- `lightwell-verify` reads a **ConfigMap standing in for the Lightwell Network
  catalog** — the consumption API is not publicly documented. The shape of the
  check is real; the data feed is not.
- Signing uses a **cosign key, not keyless**. Keyless flags are commented in the
  `sign-and-attest` task; key-based avoids losing stage time to OIDC trust
  config. Signatures still go to the real RHTAS Rekor.
- Two replay entries ship as **hand-authored seeds** until
  `capture-replay.sh` is run.

## Key documents

- `docs/DEMO-SCRIPT.md` — the stage walkthrough: talk track, tab order,
  recovery table, anticipated Q&A. Read this to understand *why* the code is
  shaped as it is.
- `docs/ARCHITECTURE.md` — the predicate format and the three properties
  (attributable, revocable, enforceable) that justify it.
- `docs/SETUP.md` — build order, sizing, known rough edges, GPU path.
