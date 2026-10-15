#!/usr/bin/env bash
# Act 3b. The same application, built through the gate, deploys.
source "$(dirname "$0")/lib.sh"
require_cluster
NS=demo-accounts
IMG="image-registry.openshift-image-registry.svc:5000/${NS}/accounts-api"

step "Resolving the signed image"
DIGEST="$(oc get istag accounts-api:latest -n "${NS}" \
  -o jsonpath='{.image.metadata.name}' 2>/dev/null || true)"
[[ -n "${DIGEST}" ]] || die "no accounts-api:latest in ${NS} - run the pipeline to a passing verdict first"
REF="${IMG}@${DIGEST}"
info "${REF}"

step "What the cluster can verify about it"
cat <<'MSG'
     Before deploying, show the audience the claim itself:

       cosign verify-attestation \
         --key k8s://lightwell-inward/cosign-keypair \
         --rekor-url http://rekor-server.trusted-artifact-signer.svc.cluster.local \
         --type https://redhat.com/lightwell-inward/code-review/v1 \
         <image> | jq -r '.payload | @base64d | fromjson | .predicate'

MSG

step "Deploying"
oc delete deployment accounts-api -n "${NS}" --ignore-not-found >/dev/null 2>&1 || true
oc apply -n "${NS}" -f - <<YAML | sed 's/^/     /'
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
          image: ${REF}
          ports:
            - containerPort: 8080
          readinessProbe:
            httpGet:
              path: /q/health/ready
              port: 8080
            initialDelaySeconds: 10
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
YAML

oc rollout status deploy/accounts-api -n "${NS}" --timeout=180s
ok "admitted and running"

cat <<'MSG'

     Same code path, same cluster, same policy. The difference is that this
     image carries a signed, verifiable statement about a security review of
     the exact commit it was built from - and the cluster checked it.
MSG
