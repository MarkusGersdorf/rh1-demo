# Setup

Target: **OpenShift 4.22**, single cluster, no GPU.

Budget **half a day** for the first build, most of it waiting. Do it at least
three days before the event — the model download and the replay capture are
the two steps that bite, and both need slack.

## Sizing

RHACM + RHACS + RHOAI + RHDH + Tekton on one cluster, plus **two** models on
CPU — Antares 1B for Stage A and Foundation-Sec-8B for Stage B:

| | Minimum | Comfortable |
|---|---|---|
| Worker cores | 24 | 32+ |
| Worker memory | 64 Gi | 96 Gi |
| Storage | 250 Gi | 400 Gi |
| Workers | 3 | 3-5 |

The two model pods request 8 CPU / 17 Gi between them (Stage B: 6/12,
Stage A: 2/5). If you are tight, scale the Stage B `InferenceService` to zero
and demo it on the replay backend — the walkthrough is identical and you lose
only the live-inference aside. Stage A is small enough to leave running.

## Prerequisites on your laptop

`oc`, `git`, `jq`, `curl`, `cosign`, and optionally `tkn` (much nicer pipeline
logs).

**A Hugging Face token is mandatory, not optional.** Both models are gated:
`Foundation-Sec-8B-Reasoning` is a Llama 3.1 derivative, and `fdtn-ai/antares-1b`
is behind its own licence click-through. For Antares there is no token-free
path at all, because **the Antares CLI is distributed inside the gated model
repository** (`assets/antares-cli.zip`) — so the token buys you the scanner as
well as the weights. Accept both licences on the account you use.

## Order of operations

### 0. Fork and check the cluster

```sh
# Fork this repo first - Argo CD and RHDH both read from your fork, not mine.
# --recurse-submodules brings in the pinned antares/ demo repo.
git clone --recurse-submodules https://github.com/<you>/rh1-demo.git && cd rh1-demo
oc login ...
./scripts/00-prereqs.sh
```

`00-prereqs.sh` fails loudly on the things that are expensive to discover late:
missing operator catalog entries, no default StorageClass, a pull secret
without `registry.redhat.io`. **Fix everything it flags before continuing.**

It also prints each operator's default channel. Compare them against
`gitops/components/operators/subscriptions.yaml` and correct that file if they
differ — Argo CD will faithfully reconcile a wrong channel forever.

### 1. Resolve the placeholders

```sh
./scripts/01-set-cluster-domain.sh
git diff --stat
git commit -am "Point demo at this cluster" && git push
```

`apps.CLUSTER_DOMAIN` is the **only placeholder left on `main`** — the repo
slug is already resolved to `MarkusGersdorf/rh1-demo` everywhere. **The commit
matters**: Argo CD syncs from git, so an unpushed change does not exist.

The script still rewrites the slug as well, derived from `git remote`, so
**re-run it after forking**. It touches only files something consumes — the
manifests Argo CD applies, plus the CI badge URLs in `README.md`. Prose under
`docs/` and the `README.md` files inside `gitops/` describe these placeholders
and are left alone.

If `origin` is not github.com, the script warns you: the RHDH catalog uses
`raw.githubusercontent.com` URLs and the badges are GitHub Actions badges, so
both need adjusting by hand.

Note the CI badge will not render for anonymous visitors while the repository
is private — GitHub serves workflow badges only to viewers with access. It
renders fine when you are signed in.

### 2. Bootstrap GitOps

```sh
./scripts/bootstrap.sh
oc get applications -n openshift-gitops -w
```

Installs the GitOps operator, grants Argo CD cluster-admin, applies the root
app-of-apps. Everything else follows from git.

**Expect 30-60 minutes and expect red in the Argo CD UI while it converges.**
Operator CRs are applied in the same pass as the operators that define their
CRDs, so the first few attempts fail with "no matches for kind". Argo retries
with backoff (20 Applications' worth of retry config) and it resolves. Do not
start hand-applying things.

Sync waves, for when you need to reason about ordering:

| Wave | What |
|---|---|
| -10 | namespaces |
| -5 | operator subscriptions |
| 0 | MultiClusterHub, Central, DataScienceCluster, Securesign, Backstage |
| 5 | Gitea |
| 6 | model fetch job |
| 10 | scanner, pipeline |
| 15 | RHACM policies |

### 3. Secrets (the parts that cannot be GitOps)

```sh
./scripts/02-signing-setup.sh     # cosign keypair, RHDH secrets
./scripts/03-acs-init-bundle.sh   # RHACS SecuredCluster init bundle
```

`03` waits for Central, which takes a while on first boot. If the
`SecuredCluster` CR shows degraded before you run this, that is expected — it
is waiting for exactly this bundle.

### 4. The models

```sh
oc create secret generic huggingface-token -n lightwell-inward \
  --from-literal=token=hf_xxxxxxxx

# Stage B - Foundation-Sec-8B
oc delete job model-fetch -n lightwell-inward --ignore-not-found
oc logs -f job/model-fetch -n lightwell-inward

# Stage A - Antares 1B, and the Antares CLI
oc delete job antares-fetch -n lightwell-inward --ignore-not-found
oc logs -f job/antares-fetch -n lightwell-inward
```

`antares-fetch` does two things: converts Antares 1B to an **F16** GGUF (CPU
inference needs F16; the upstream POC builds BF16 for Apple Metal, which is the
wrong precision here), and extracts the CLI into a virtualenv on a PVC that the
pipeline mounts. It is much quicker than Stage B — roughly 2.5 GB rather than
16 GB.

The job downloads ~16 GB, converts to GGUF and quantises to `Q4_K_M`. **Budget
40-70 minutes.** It is idempotent, so a re-run after success is free.

Much faster, if you have a GGUF mirrored internally: put its URL in
`MODEL_GGUF_URL` in `gitops/components/rhoai/model-fetch-config`, commit, and
the job downloads ~5 GB instead of converting.

Then wait for KServe on both:

```sh
oc get inferenceservice -n lightwell-inward -w
# foundation-sec-reasoning  (Stage B)
# antares                   (Stage A)
```

First load of a 5 GB GGUF off a demo PVC is slow; the readiness probe allows
ten minutes before giving up. If it restart-loops, read the pod log before
assuming the model is at fault — it is usually memory.

### 5. Scanner, repo, policy

```sh
./scripts/06-build-scanner.sh   # builds the image in-cluster
./scripts/04-seed-gitea.sh      # admin user, repo, webhook, local clone
./scripts/05-acs-policy.sh      # signature integration + policy check
```

`05` prompts for an RHACS API token — generate one in the RHACS console under
**Platform Configuration → Integrations → API Token**, Admin role.

### 6. Capture the replay cache

**Do not skip this.** The repo ships two *hand-authored seed* entries so the
pipeline is testable before the model is up. They are worked examples of the
expected answer, not model output, and the service logs a warning for each.

```sh
./scripts/capture-replay.sh
```

This switches the scanner to live inference, submits both demo diffs, records
what the model actually says, and switches back. Then **read the captures**:

```sh
jq -r '.provenance, (.response.findings[] | "\(.severity) \(.cwe) \(.title)")' \
  components/ai-code-review/replay/*.json
```

If the real model does not find the ownership bug, you need to know that now,
not on stage. Usual cause is truncated reasoning — raise `MODEL_MAX_TOKENS`.
`capture-replay.sh` prints the remedies in order.

Commit the captures and rebuild so they are baked into the image:

```sh
git add components/ai-code-review/replay
git commit -m "Capture replay entries from the live model" && git push
./scripts/06-build-scanner.sh
```

### 7. Verify

```sh
./scripts/verify.sh
```

Checks every component, the scanner's readiness, the replay provenance, the
RHACM policy's compliance, and whether the git repo is at the base commit. It
also prints every route you will open on stage — keep that output.

### 8. Rehearse

```sh
./scripts/demo-push-vulnerable.sh   # must block
./scripts/demo-push-fix.sh          # must pass, sign, attest
./scripts/demo-rogue-deploy.sh      # must be rejected at admission
./scripts/demo-deploy-signed.sh     # must be admitted
./scripts/demo-reset.sh             # back to the start
```

Rehearse the whole thing at least twice, with a reset between. The second run
is where you find the state you forgot to clear.

## Going GPU

If a GPU node appears:

1. Install the NFD and NVIDIA GPU operators.
2. Uncomment `servingruntime-vllm-gpu.yaml` in
   `gitops/components/rhoai/kustomization.yaml`.
3. In `inferenceservice.yaml`, set `runtime: vllm-gpu-runtime` and
   `modelFormat.name: vLLM`, pointing `storageUri` at the HF snapshot
   directory rather than the GGUF.
4. Set `SCANNER_BACKEND: "vllm"` in
   `gitops/components/ai-code-review/configmap.yaml`.
5. Commit and push.

Inference drops to a few seconds and you can demo live with no replay cache.

## Continuous integration

`.github/workflows/ci.yml` runs on push and PR:

| Job | What it protects |
|---|---|
| `scanner` | 165 pytest tests, 90% coverage floor |
| `demo-app` | `mvn verify` — the three shipped tests must stay green **on the vulnerable code**, because Act 2 depends on it |
| `manifests` | Every YAML parses, every kustomization builds, every Argo CD Application points at a directory that exists, shell and embedded Tekton Python parse |
| `images` | Both Containerfiles build |

### Do we need to build and push images in CI?

**No, and it would work against the demo.** Both images are built where the
story needs them built:

- the **scanner** by the in-cluster `BuildConfig`, so the cluster is
  self-contained and needs no registry credentials;
- the **demo app** by the Tekton pipeline — which is the entire point. An
  `accounts-api` image that arrived from GitHub Actions would carry no review
  attestation, so RHACS would refuse to admit it. That is the gate working
  correctly, but it is not a demo.

CI does **build** both images without pushing. That is worth the two minutes:
a broken Containerfile otherwise surfaces during cluster setup or on stage,
and the demo app's build is also what proves the Red Hat GA Maven repository
in `pom.xml` still resolves.

There is one real case for publishing: a **disconnected cluster**, where the
in-cluster build cannot reach github.com for the source. For that,
`.github/workflows/publish-scanner.yml` is manual-only (`workflow_dispatch`)
and pushes the scanner to GHCR. It also serves as a known-good fallback image.
It never publishes the demo app, for the reason above. The workflow header has
the two `oc` commands to repoint the deployment — including removing the
ImageStream trigger, without which OpenShift puts the in-cluster image
straight back.

## Expect Stage A to be noisy

Upstream's own recorded results are worth reading before you trust Stage A on
stage. They are in the pinned `antares/` submodule under `antares/reports/`:

- A single-file sweep on CPU took **167 s** with 47 tool calls, 7 of which failed.
- Antares examined **3 of 944** CWE classes under `--max-cwes 3`.
- The 350M model flagged all three *corrected* OAuth controls; 1B flagged two
  of three.

None of that breaks the demo, because Stage A cannot pass or fail a build. It
is why the architecture puts it where it is. But do not promise the room that
the small model will find the Java bug, and do not be surprised if it flags the
fixed file — say "it's a search step, not the reviewer" and move on.

## Known rough edges

**RHTAS timestamp authority** wants a `tsa-cert-chain` secret. The demo does
not use timestamping; if `Securesign` hangs on it, delete the
`timestampAuthority` block from `securesign.yaml` and commit.
`02-signing-setup.sh` detects this and tells you.

**RHACS `SecurityPolicy` CRD** only exists on recent RHACS. If
`oc get crd securitypolicies.config.stackrox.io` is empty, `05-acs-policy.sh`
prints the console steps to create the equivalent policy by hand. You lose
GitOps reconciliation of the policy, not the enforcement.

**`publish:gitea` scaffolder action** may not be registered in your RHDH
build. See `gitops/components/rhdh/README.md` — the walkthrough works fine
from the pre-seeded repo, and scaffolding is scene-setting rather than content.

**RHDH catalog needs egress to GitHub** (it reads your fork over HTTPS). For a
disconnected cluster, push this repo to the in-cluster Gitea and repoint
`catalog.locations`.

**Antares may not find the Java flaw.** It is a Python-leaning localizer and
the planted bug is a Java authorization logic error with no injection sink.
Stage B reviews the whole diff regardless, so the demo holds either way — but
verify what actually happens in rehearsal rather than discovering it on stage.

**Argo CD has cluster-admin.** Correct for a demo cluster that installs
operators and cluster-scoped policy. If a security-minded attendee asks, the
production answer is scoped `AppProject`s with per-destination allow-lists, and
it is a fair question to concede.
