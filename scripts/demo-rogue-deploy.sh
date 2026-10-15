#!/usr/bin/env bash
# Act 3a. The bypass attempt.
#
# "Fine, I'll build it myself." A developer with registry push rights builds
# the vulnerable code straight into an image, skipping the pipeline entirely -
# so there is no review, no signature and no attestation - and tries to deploy.
#
# RHACS admission control rejects it, which is the point: the gate is not a
# pipeline convention, it is a property of the cluster.
source "$(dirname "$0")/lib.sh"
require_cluster
NS=demo-accounts
IMG="image-registry.openshift-image-registry.svc:5000/${NS}/accounts-api-rogue"

step "Building the vulnerable code outside the pipeline"
if oc get buildconfig accounts-api-rogue -n "${NS}" >/dev/null 2>&1; then
  ok "rogue BuildConfig already exists"
else
  GITEA_SVC="http://gitea.gitea.svc.cluster.local:3000/demo/accounts-api.git"
  oc new-build --name=accounts-api-rogue -n "${NS}" \
    --strategy=docker --dockerfile-path=Containerfile \
    --code="${GITEA_SVC}#main" >/dev/null 2>&1 \
    || oc new-build --name=accounts-api-rogue -n "${NS}" \
       --strategy=docker --binary >/dev/null
  ok "created BuildConfig accounts-api-rogue"
fi

info "building - no review, no signature, no attestation"
oc start-build accounts-api-rogue -n "${NS}" --follow --wait >/dev/null 2>&1 \
  || warn "build did not complete; the admission test below still works if a tag exists"

step "Attempting to deploy it"
cat <<'MSG'

     Watch the output of the apply. This is the moment.

MSG

set +e
oc apply -n "${NS}" -f - <<YAML 2>&1 | sed 's/^/     /'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: accounts-api
  namespace: demo-accounts
  labels:
    app: accounts-api
spec:
  replicas: 1
  selector:
    matchLabels:
      app: accounts-api
  template:
    metadata:
      labels:
        app: accounts-api
    spec:
      containers:
        - name: accounts-api
          image: ${IMG}:latest
          ports:
            - containerPort: 8080
YAML
RC=$?
set -e

echo
if (( RC != 0 )); then
  ok "rejected at admission, exactly as intended"
  cat <<'MSG'

     Say this part out loud: the image was never reviewed, so it was never
     signed, so RHACS will not admit it. The developer did not need to be
     malicious - bypassing a pipeline is a Tuesday afternoon. What stops it is
     that the cluster asks for a verifiable claim and this image has none.
MSG
else
  warn "the deployment was ADMITTED. The gate is not enforcing."
  cat <<'MSG'

     Check, in this order:
       oc get policy lightwell-inward-gate -n open-cluster-management
       oc get securitypolicy -n stackrox
       oc rollout status deploy/admission-control -n stackrox
       # and in the RHACS console: is the policy's enforcement actually on?
       #   Policy Management -> "Lightwell Inward ..." -> Enforcement
       # and does the signature integration exist?
       #   ./scripts/05-acs-policy.sh
MSG
  exit 1
fi
