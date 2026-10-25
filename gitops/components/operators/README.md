# Operators

**No Subscription here sets `spec.channel`, deliberately.** OLM falls back to
the package's own `defaultChannel` from the catalog on whichever cluster this
is applied to.

Hardcoding channels was wrong twice over. The names differ per OCP version —
this file previously pinned `advanced-cluster-management` to `release-2.15`,
which OCP 4.22's catalog does not carry at all (it has `release-2.16` and
`release-2.17`). And the failure mode is silent: a Subscription that cannot
resolve sits on `ResolutionFailed` while Argo CD reports it Synced, so every
later sync wave starves of CRDs with no indication why.

To see what this cluster actually offers, and whether an operator installed:

```sh
./scripts/diagnose-sync.sh
```

To pin a channel on purpose, read that output first, then add `channel:` back
for that one Subscription — and expect to revisit it on the next OCP minor.

Note: KServe runs in **RawDeployment** mode in this demo, which deliberately avoids
installing OpenShift Serverless and Service Mesh. That is a large reduction in
moving parts for a CPU-only single-cluster demo. If you later want Serverless
model serving (autoscale-to-zero, traffic splitting), those two operators come back.
