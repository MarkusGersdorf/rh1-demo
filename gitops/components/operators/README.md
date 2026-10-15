# Operators

Channel names drift between releases. Before the first sync, confirm each one
against the cluster rather than trusting this file:

```sh
for p in advanced-cluster-management rhacs-operator rhods-operator \
         openshift-pipelines-operator-rh rhtas-operator rhdh; do
  echo "== $p"
  oc get packagemanifest "$p" -n openshift-marketplace \
    -o jsonpath='{.status.defaultChannel}{"  all: "}{range .status.channels[*]}{.name}{" "}{end}{"\n"}' 2>/dev/null \
    || echo "   NOT FOUND - check catalog source"
done
```

Then correct `spec.channel` here and commit. Argo CD will reconcile.

Note: KServe runs in **RawDeployment** mode in this demo, which deliberately avoids
installing OpenShift Serverless and Service Mesh. That is a large reduction in
moving parts for a CPU-only single-cluster demo. If you later want Serverless
model serving (autoscale-to-zero, traffic splitting), those two operators come back.
