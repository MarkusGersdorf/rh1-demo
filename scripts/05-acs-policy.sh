#!/usr/bin/env bash
# Register the cosign public key as an RHACS signature integration, and make
# sure the enforcement policy is in place.
#
# The signature integration is created through the RHACS API because it is not
# expressible as a CR. The policy itself is GitOps-managed via the RHACM Policy
# in gitops/components/policies - this script only verifies it landed.
source "$(dirname "$0")/lib.sh"
require_cluster
need curl
need jq

PUB="$(repo_root)/.local/cosign.pub"
[[ -f "${PUB}" ]] || {
  oc get secret cosign-keypair -n lightwell-inward \
    -o jsonpath='{.data.cosign\.pub}' | base64 -d > "${PUB}" 2>/dev/null \
    || die "no cosign public key - run ./scripts/02-signing-setup.sh first"
}

step "RHACS API token"
if oc get secret acs-api-token -n lightwell-inward >/dev/null 2>&1; then
  TOKEN="$(oc get secret acs-api-token -n lightwell-inward -o jsonpath='{.data.token}' | base64 -d)"
  ok "reusing existing acs-api-token"
else
  cat <<'MSG'

     An API token with Admin role is needed. Create one in the RHACS console:
       Platform Configuration -> Integrations -> API Token -> Generate Token
     then paste it below. It is stored as the acs-api-token secret and reused
     by the pipeline's acs-image-check task.

MSG
  read -rsp "     RHACS API token: " TOKEN; echo
  [[ -n "${TOKEN}" ]] || die "no token given"
  oc create secret generic acs-api-token -n lightwell-inward --from-literal=token="${TOKEN}"
  ok "stored acs-api-token"
fi

CENTRAL="https://$(oc get route central -n stackrox -o jsonpath='{.spec.host}')"
info "central: ${CENTRAL}"

api() {
  local method="$1" path="$2"; shift 2
  curl -sk -X "${method}" -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" "${CENTRAL}${path}" "$@"
}

step "Signature integration"
EXISTING="$(api GET /v1/signatureintegrations | jq -r \
  '.integrations[]? | select(.name=="lightwell-inward-signer") | .id')"

PAYLOAD="$(jq -n --arg key "$(cat "${PUB}")" '{
  name: "lightwell-inward-signer",
  cosign: { publicKeys: [ { name: "lightwell-inward", publicKeyPemEnc: $key } ] }
}')"

if [[ -n "${EXISTING}" ]]; then
  api PUT "/v1/signatureintegrations/${EXISTING}" \
    -d "$(jq --arg id "${EXISTING}" '. + {id:$id}' <<< "${PAYLOAD}")" >/dev/null
  ok "updated signature integration ${EXISTING}"
else
  NEW="$(api POST /v1/signatureintegrations -d "${PAYLOAD}" | jq -r '.id // empty')"
  [[ -n "${NEW}" ]] || die "could not create the signature integration - check the token's permissions"
  ok "created signature integration ${NEW}"
fi

step "Internal registry integration"
# RHACS autogenerates registry integrations from openshift-config/pull-secret,
# which only names the EXTERNAL registries (quay.io, registry.redhat.io, ...).
# Nothing there covers the cluster's own registry, so `roxctl image check`
# against a freshly built image fails before it evaluates a single policy:
#
#   image enrichment error: error getting metadata for image:
#   image-registry.openshift-image-registry.svc:5000/demo-accounts/accounts-api@sha256:...
#   error: no matching image registries found
#
# That reads like a policy or signature problem on stage and is neither -
# Central simply cannot see into the registry the pipeline just pushed to.
#
# Central needs pull credentials of its own. A dedicated ServiceAccount with
# system:image-puller on demo-accounts is the smallest thing that works; its
# SA + RoleBinding are declared in gitops/components/pipelines/rbac.yaml, and
# only the token is minted here, because a token cannot live in git.
PULL_NS=demo-accounts
PULL_SA=acs-registry-puller
PULL_SECRET="${PULL_SA}-token"

if ! oc get sa "${PULL_SA}" -n "${PULL_NS}" >/dev/null 2>&1; then
  warn "ServiceAccount ${PULL_NS}/${PULL_SA} is missing - is the pipelines"
  warn "Application synced? Skipping the registry integration."
else
  # OpenShift no longer auto-creates SA token secrets, so ask for one
  # explicitly. Long-lived on purpose: Central holds it, and a bounded token
  # would silently stop enriching images partway through the demo.
  oc apply -f - >/dev/null <<YAML
apiVersion: v1
kind: Secret
metadata:
  name: ${PULL_SECRET}
  namespace: ${PULL_NS}
  annotations:
    kubernetes.io/service-account.name: ${PULL_SA}
type: kubernetes.io/service-account-token
YAML
  for _ in $(seq 1 15); do
    PULL_TOKEN="$(oc get secret "${PULL_SECRET}" -n "${PULL_NS}" \
      -o jsonpath='{.data.token}' 2>/dev/null | base64 -d || true)"
    [[ -n "${PULL_TOKEN}" ]] && break
    sleep 2
  done

  if [[ -z "${PULL_TOKEN:-}" ]]; then
    warn "the ${PULL_SECRET} secret never got populated - add the registry"
    warn "integration by hand in the RHACS console."
  else
    REG_ENDPOINT="image-registry.openshift-image-registry.svc:5000"
    REG_NAME="openshift-internal-registry"
    REG_PAYLOAD="$(jq -n --arg ep "${REG_ENDPOINT}" --arg pw "${PULL_TOKEN}" \
      --arg name "${REG_NAME}" '{
        name: $name, type: "docker", categories: ["REGISTRY"],
        docker: { endpoint: $ep, username: "serviceaccount",
                  password: $pw, insecure: true }
      }')"
    REG_EXISTING="$(api GET /v1/imageintegrations | jq -r \
      --arg name "${REG_NAME}" '.integrations[]? | select(.name==$name) | .id')"
    if [[ -n "${REG_EXISTING}" ]]; then
      api PUT "/v1/imageintegrations/${REG_EXISTING}" \
        -d "$(jq --arg id "${REG_EXISTING}" '. + {id:$id}' <<< "${REG_PAYLOAD}")" >/dev/null
      ok "updated registry integration ${REG_EXISTING}"
    else
      REG_NEW="$(api POST /v1/imageintegrations -d "${REG_PAYLOAD}" | jq -r '.id // empty')"
      [[ -n "${REG_NEW}" ]] \
        && ok "created registry integration ${REG_NEW}" \
        || warn "could not create the registry integration - check the token's permissions"
    fi
  fi
fi

step "Enforcement policy"
if oc get crd securitypolicies.config.stackrox.io >/dev/null 2>&1; then
  ok "policy-as-code CRD present - the RHACM Policy manages the policy"
  oc get securitypolicy -n stackrox lightwell-inward-attestation-required >/dev/null 2>&1 \
    && ok "SecurityPolicy reconciled onto the cluster" \
    || warn "SecurityPolicy not present yet - check the RHACM policy status"
else
  warn "no securitypolicies.config.stackrox.io CRD on this RHACS version."
  warn "Create the policy in the RHACS console instead:"
  warn "  Policy Management -> Create policy"
  warn "  Lifecycle: Deploy | Enforce: on"
  warn "  Criteria: 'Image Signature Verified By' NOT 'lightwell-inward-signer'"
  warn "  Exclude namespaces: lightwell-inward, openshift*, kube*, stackrox, rhdh, gitea"
  warn "You lose GitOps reconciliation of the policy, not the enforcement."
fi

step "Verify the policy actually bites"
POL="$(api GET /v1/policies | jq -r '.policies[]? | select(.name|test("Lightwell Inward")) | "\(.name) | enforcement=\(.enforcementActions // [] | join(","))"')"
if [[ -n "${POL}" ]]; then
  ok "${POL}"
else
  warn "no Lightwell Inward policy visible via the API yet"
fi
