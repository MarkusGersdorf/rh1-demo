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
