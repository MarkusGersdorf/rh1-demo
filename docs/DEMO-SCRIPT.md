# Demo script

**Format:** manual walkthrough, ~22 minutes plus questions.
**Shape:** a developer ships a one-line authorization bug. Everything that is
supposed to catch it doesn't. Then one thing does, and its verdict becomes a
signed fact that the cluster enforces.

---

## Ten minutes before

```sh
./scripts/demo-reset.sh
./scripts/verify.sh
```

`verify.sh` must end green and must say `main is at: Initial accounts API`. It
prints every route — leave that in a scrollback you can reach.

**Open these tabs, in this order, and leave them on the right page:**

1. **Developer Hub** → the `accounts-api` component page
2. **Gitea** → `demo/accounts-api`, commits view
3. **OpenShift console** → Pipelines → `lightwell-inward` project
4. **Scanner UI** → the route for `ai-code-review` (empty; it fills live)
5. **RHACS** → Policy Management, filtered to "Lightwell"
6. **Rekor Search UI**
7. **RHACM** → Governance → `lightwell-inward-gate`
8. **RHOAI dashboard** → Model Serving → `foundation-sec-reasoning`

**Two terminals, large font.** Left for pushes, right for `tkn` logs.

**Decide before you walk on:** live inference or replay?

- Replay — instant and deterministic. Say "this is a recorded response from
  this model; I'll show it running live at the end." Then do.
- Live — 60-90 seconds of CPU inference. Good if you can talk over it; show
  the RHOAI tab and the model pod's log while it thinks.

Either is fine. Claiming the first is the second is not.

---

## Act 0 — The gap (2 min)

> In May, IBM and Red Hat put five billion dollars and twenty thousand
> engineers behind Lightwell. In July the commercial offerings shipped:
> Lightwell Network gives you six and a half thousand remediated dependencies
> as signed binaries with SBOMs, delivered into your existing pipelines.
> Four hundred previously-unknown CVEs fixed in Java libraries alone.
>
> That is real new infrastructure, and it covers somewhere between eighty-five
> and ninety-five percent of what's running in your containers.
>
> It covers everything in your application except the part you wrote.
>
> And the part you wrote is increasingly the part an AI assistant wrote for
> you. So: what signs *that*?

Hold that question. You will answer it with two models and a signature.

---

## Act 1 — The developer (3 min)

**Tab 1, Developer Hub.** Show the `accounts-api` component.

> This is where our developer lives. Red Hat Developer Hub — the golden path
> for this organisation. Service catalog, templates, pipelines, the works.

Click into the template list, show **"Quarkus service (Lightwell Inward
secured)"**.

> Worth noticing: the security gate isn't something a developer opts into.
> Scaffold a service from the golden path and the supply chain comes attached
> from the first commit. You can't forget to add it.

**Now the change.** Show the code — your editor, or Gitea's file view.

> The ask is mundane. The mobile team wants statement history with a date
> filter. Our developer adds an endpoint.

Walk through `AccountResource.statements()`. Draw attention to the ownership
check — **and to the correct one in `get()` directly above it**:

```java
// in get(), ten lines above:
if (!account.get().ownerId().equals(jwt.getSubject())) {
    return Response.status(Response.Status.FORBIDDEN).build();
}

// in statements():
if (!account.ownerId().equals(jwt.getSubject())) {
    LOG.warnf("ownership mismatch: subject=%s account=%s owner=%s", ...);
}
return Response.ok(...).build();
```

> Same class. Same check. One returns, one logs.
>
> There's a `@RolesAllowed("customer")` on the method, so you're authenticated
> and you hold the customer role. Nobody checked whether it's *your* account.
>
> The return statement was there. It got lost in a refactor. Three tests, all
> green: owner reads their own statements, unknown account is a 404, the date
> filter narrows. Nobody wrote the cross-tenant test — which is the realistic
> part of this whole demo.

Let that land. Most of the room has shipped this.

---

## Act 2 — Everything fails to catch it (6 min)

**Left terminal.**

```sh
./scripts/demo-push-vulnerable.sh
```

**Tab 2, Gitea** — the commit appears. **Tab 3** — the PipelineRun starts.

**Right terminal:**

```sh
tkn pipelinerun logs -f -n lightwell-inward --last
```

### `lightwell-verify`

> First stage checks the dependency supply chain against the Lightwell Network
> catalog. Every Quarkus library: remediated, signed, Rekor log index, SBOM
> reference.
>
> Eight remediated, nothing flagged. This half is clean — and this is the half
> Lightwell already solves for you.

*(If asked: this stage reads a ConfigMap standing in for the catalog, because
Lightwell Network's consumption API isn't publicly documented. The shape of
the check is right; the data feed is a stand-in. Say so plainly.)*

### `sast-semgrep`

> Semgrep. Java rules, OWASP top ten, secrets.
>
> **Clean. Zero findings.**
>
> That is not Semgrep failing. There is no taint flow here. No injection, no
> dangerous sink, no unsanitised input reaching anything. The authorization
> check is *present* — it's three lines of entirely valid Java. Pattern
> matching has nothing to match on.
>
> This is the structural limit of static analysis, not a tuning problem. You
> cannot write a rule for "the result of this comparison should have changed
> control flow."

### `antares-localize` — Stage A

> Now two models, with two different jobs.
>
> First one is **Antares 1B** — Cisco Foundation AI, open weights, one billion
> parameters. Its job is narrow and it's honest about it: given a tree of code,
> which *files* probably contain which *class* of weakness. That's it. No line
> numbers, no explanation, no fix. Cisco's own model card calls it
> "file-level vulnerability localization."
>
> Why bother? Because it's cheap enough to run on every commit, and it tells
> the expensive model where to look.

Point at the output. It flags files with a CWE and a likelihood.

> And notice what the task says at the end: **it cannot fail the build.** It
> examined a handful of the nine hundred and forty-four CWE classes in the
> catalogue, so its silence tells you almost nothing. A gate built on that
> would block clean releases.

*(If Stage A does not flag the Java file, say so plainly and move on — see the
note below. It changes nothing about what happens next.)*

### `ai-code-review` — Stage B

**Switch to Tab 4, the scanner UI.** It fills in as the task runs.

> Now the second model, and a different job entirely: the diff goes to
> `Foundation-Sec-8B-Reasoning`. Open weights, eight billion parameters, a
> security reasoning model. Where Antares says *which file*, this one says
> *which line, why, how you'd exploit it, and what to change.*
>
> It gets Stage A's output as a hint — "look here first" — and it reviews the
> whole diff anyway. Deliberately. If I gated this on the small model finding
> something, then one model's miss silently switches off the other.
>
> It's running on Red Hat OpenShift AI. **Our cluster, our GPU-less hardware,
> our data.** No diff leaves this cluster. For anyone whose lawyers have
> opinions about source code and third-party APIs, that is the whole
> conversation.

*(Tab 8 if you want to show it in the RHOAI model-serving UI.)*

Read the finding out loud from the scanner UI. The sentence that matters:

> **CRITICAL. CWE-639. Broken Access Control.**
>
> "The branch correctly detects that the JWT subject is not the account owner,
> but its body only emits a warning log. Control flow continues, which fetches
> and returns the statements regardless."
>
> And it noticed the asymmetry: "the `get()` method immediately above handles
> the same check correctly by returning 403 — consistent with a return
> statement lost during a refactor."
>
> It reconstructed how the bug happened.
>
> With a working exploit: authenticate as Alice, request Bob's account, expect
> 403, get 200 and Bob's transaction history.
>
> And a second finding it ranked lower — the date parser's exception message
> going back to the client. Medium. It triaged.

**Back to the right terminal.** The pipeline has stopped.

> Pipeline failed. **No image was built.** There is no artifact to deploy,
> because we don't build artifacts from code that fails review.

---

## Act 3 — The fix, and the claim (5 min)

```sh
./scripts/demo-push-fix.sh
```

> One line. `return Response.status(FORBIDDEN).build()`. Plus the generic
> error message for the second finding.

Follow the run. `ai-code-review` passes — empty findings list.

> Empty findings. The prompt explicitly tells the model not to invent a
> finding to look useful, and a clean answer on a clean diff is the correct
> behaviour for a gate.

Now `build-image` and `sign-and-attest`.

> Red Hat Trusted Artifact Signer — part of the Advanced Developer Suite. This
> is the product that builds and signs your artifacts. It signs the image.
>
> **And then it does the thing this whole demo is about.**

Stop here. This is the point of the talk.

> We take the AI's verdict and attach it to the image as an **in-toto
> attestation** — signed, and logged in Rekor.
>
> Not a dashboard entry. Not a line in a CI log that scrolls away. A
> cryptographically signed statement that this specific image digest was
> reviewed by this specific model — pinned by digest — with this prompt
> version, at temperature zero, covering these files, and the verdict was
> pass.

**Tab 6, Rekor Search.** Find the entry.

> Three things that gets you.
>
> **One: it's attributable.** "The AI approved it" is a shrug. "Model digest
> `sha256:1b7c…`, prompt version v3, temperature zero, these three files" is
> a claim you can audit.
>
> **Two: it's enforceable** — in a second.
>
> **Three, and this is the one I'd keep:** it's **revocable**.
>
> Suppose in four months we learn this model is blind to some class of bug.
> With a dashboard, you re-scan everything and hope. With this, you query
> Rekor for every artifact ever approved under that model digest, and you
> re-review *exactly those*. Nothing more.
>
> That is precisely what Lightwell Clearinghouse gives you for an embargoed
> dependency CVE. We just pointed it at our own code.

Then `ec-validate`:

> Enterprise Contract. RHACS can verify that an image is signed; it can't read
> inside the attestation. Enterprise Contract evaluates rego against the
> predicate: is there a review, did it pass, did it cover a non-zero number of
> files, is the model pinned. A signed-but-failing artifact doesn't promote.

---

## Act 4 — Enforcement (4 min)

> Everything so far lives inside a pipeline. Pipelines are a convention.
> Anyone with edit rights can delete a Tekton task.
>
> So let's be the developer who's had enough of this.

```sh
./scripts/demo-rogue-deploy.sh
```

> Builds the vulnerable code straight to the registry. No pipeline, no review,
> no signature. Then deploys it. This isn't malice — it's a Tuesday afternoon
> and a deadline.

The `oc apply` is **rejected**. Read the admission error aloud.

**Tab 5, RHACS** — show the policy and the violation.

> RHACS admission control. The image isn't signed by the Lightwell Inward
> signer, so it doesn't run. And the only thing that holds that signing key is
> the pipeline, which only signs after the review passes.
>
> So "signed by us" *means* "reviewed and passed." The developer didn't need
> to be stopped from bypassing the pipeline. The cluster just asked for a
> verifiable claim, and there wasn't one.

```sh
./scripts/demo-deploy-signed.sh
```

> Same application. Same cluster. Same policy. This one carries the
> attestation. Admitted.

---

## Act 5 — Why this is a platform property (2 min)

**Tab 7, RHACM Governance.**

> Last piece. That RHACS policy isn't something someone clicked into a
> console. It's an RHACM policy, in git, with `remediationAction: enforce`.
>
> Three things it asserts continuously: the policy exists, admission control
> is switched on, and nobody has set the break-glass annotation on the
> namespace to quietly opt out.
>
> Turn any of those off and RHACM turns it back on, and reports
> non-compliance while it does.
>
> One cluster here. Label a hundred more and the placement fans this out
> unchanged. The gate stops being a pipeline convention and becomes a property
> of the fleet.

> And all of it — every operator, every CR, the policy, the pipeline, the
> model serving runtime — came out of one Argo CD app-of-apps. One commit
> builds this cluster.

---

## Close (1 min)

> Lightwell's real insight isn't "scan more." It's that **a security claim is
> only useful when it's signed, verifiable, and enforceable.** A remediated
> dependency you can't verify is a rumour.
>
> We applied that to the five to fifteen percent Lightwell doesn't reach — the
> code your developers and their AI assistants write — using an open-weight
> security model on OpenShift AI, signed by Trusted Artifact Signer, enforced
> by RHACS, governed by RHACM.
>
> One honest caveat, because this room will think it anyway: **attesting an
> AI's verdict does not make the verdict correct.** The model will miss
> things. What the attestation buys you is *accountability* — you know which
> model said it, on what, under what prompt, and you can revoke it.
>
> That's not a smaller claim than "AI finds your bugs." I'd argue it's the
> only one worth building on.

---

## When it breaks

| Symptom | Do this |
|---|---|
| Webhook doesn't fire | `./scripts/run-pipeline.sh main`. Narrate it as "let's trigger it directly." Nobody minds. |
| AI task times out | You're on `llamacpp` and it's slow. Let it run while you show the RHOAI tab and the model pod log. If it dies: switch `SCANNER_BACKEND` to `replay` (`oc set env deploy/ai-code-review -n lightwell-inward SCANNER_BACKEND=replay`) and re-run. |
| Stage A flags nothing, or flags the *fixed* file | Both are fine and neither is a failure. Antares is advisory: it cannot pass or fail anything. Say "the small model is a search step, not the reviewer" and carry on. Upstream's own numbers show it flagging corrected code, which is why it does not gate. |
| Stage A is slow (minutes) | Recorded CPU runs took 167s for a single-file sweep. Lower `--max-cwes`, or narrate over it — the RHOAI tab and the Antares pod log are both worth showing. If it is really stuck, the task has a 25m timeout and the pipeline continues without it. |
| Antares CLI missing on the PVC | The CLI ships inside the gated HF download; the `antares-fetch` job extracts it. The task detects this, prints why, and exits zero — the demo proceeds with Stage B only. |
| Replay miss (verdict `error`) | The diff changed. `./scripts/demo-reset.sh` and re-push — the `contains_all` matcher should catch it. Don't debug fingerprints on stage. |
| Rogue deploy is **admitted** | Enforcement is off. The script prints the four things to check. If you can't fix it in 30 seconds, show the policy and the violation list in RHACS instead and say enforcement is toggled off for the room's safety — then move on. |
| Pipeline stuck on a PVC | Workspace PVC didn't bind. `oc get pvc -n lightwell-inward`. Usually storage pressure from a previous run — `demo-reset.sh` clears old PipelineRuns and their PVCs. |
| `cosign` step fails | `oc get secret cosign-keypair -n lightwell-inward`. Re-run `./scripts/02-signing-setup.sh`. |
| Argo CD shows red mid-demo | Ignore it unless it's a component you're about to show. It self-heals. |

**The universal recovery:** you have the scanner UI with the previous run's
findings in it. The finding is the content. If the pipeline is being difficult,
show the finding, show the attestation in Rekor from the rehearsal, and keep
talking. The argument doesn't depend on the plumbing cooperating.

---

## Questions you will get

**"Isn't this just AI code review? GitHub does that."**
The review is the least novel part. The novel part is that the verdict becomes
a signed in-toto attestation that gates admission and can be revoked by model
digest. Advisory review tools can't gate anything, and when the model turns
out to be wrong you have no way to find what it approved.

**"What if the model is wrong — false negatives?"**
It will be. Which is why SAST still runs, why RHACS still scans the image, and
why the attestation pins the model digest — so the day you learn it's blind to
something, you can enumerate exactly what it approved. This isn't a soundness
proof. It's accountability for a judgment you were going to make anyway.

**"False positives blocking developers?"**
Two controls: `FAIL_ON` (which severities block) and `MIN_CONFIDENCE` (a
hedged finding can't block). Lower either and findings are still attested, they
just don't stop the build. That's deliberate — a gate people switch off is
worse than a gate with a confidence floor.

**"Why these models and not a frontier model?"**
Open weights, so they run in your cluster and your source never leaves. That is
the whole argument, and it is enough. A frontier model would find more real
bugs than either of these — say so if asked, because it is true. What you give
up is that your source leaves the building, and that you cannot pin a model
digest: a hosted endpoint moves under you, so `modelDigest` becomes
unpinnable and the revocability property weakens. Self-hosted open weights
make that claim real. If you want a frontier model anyway, the scanner's
backend interface takes one more class.

**"Isn't a 1B model too small to be useful?"**
For explaining a bug, yes — which is exactly why it is not the reviewer here.
It is a localizer, and localization is a much easier problem than explanation.
It cannot block the build, and the reasoning model behind it reviews the whole
diff regardless. Use it for what it is good at and the size stops mattering.

**"Two models from the same vendor — isn't the agreement meaningless?"**
Largely, yes, and the demo says so. When both stages implicate a file the
finding is marked corroborated and that is *all* that happens; it does not
change the verdict. Same vendor, overlapping training data, so they are not
independent observers. The honest value of Stage A is cost and attention, not
a second opinion.

**"Could the model be prompt-injected by the code it reviews?"**
Yes, and it's the sharpest question here. The model only emits
schema-constrained JSON findings — it can't be talked into signing anything,
because it isn't what signs. A crafted comment could suppress a finding, which
is a real false-negative vector. Mitigations: the diff is reviewed, not the
whole repo; SAST runs independently; and the attestation records what was
reviewed so a suspicious scope is auditable. Honest answer: this is unsolved
and worth saying so.

**"Does this work with GitHub / GitLab / Jenkins instead?"**
Yes. Gitea is here so the demo is self-contained. The scanner is an HTTP
service, and Red Hat Advanced Developer Suite already documents the Jenkins
path for signing and attestation.

**"Is Lightwell Inward a product?"**
No. It's a demo concept extending a real product with an idea. Say it before
someone else does.
