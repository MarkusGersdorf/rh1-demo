#!/usr/bin/env bash
# Why is an Application not synced?
#
# Read-only. Answers the three questions that matter during bring-up, in the
# order they usually bite:
#
#   1. Which channels does THIS cluster's catalog actually offer?
#      (A channel named here that the catalog lacks leaves the Subscription on
#      ResolutionFailed forever, and every later wave starves of its CRDs.)
#   2. Did each operator actually install, or is it stuck resolving?
#   3. Which CRDs the manifests need are missing, and who was meant to supply
#      them - the operator, or an operator's CR reconciling?
source "$(dirname "$0")/lib.sh"
require_cluster

# --------------------------------------------------------------------------
step "Argo CD applications"
# --------------------------------------------------------------------------
printf '  %-18s %-10s %-12s %s\n' APPLICATION SYNC HEALTH MESSAGE
oc get applications -n openshift-gitops -o json 2>/dev/null | python3 -c '
import json, sys
d = json.load(sys.stdin)
for a in sorted(d.get("items", []), key=lambda x: x["metadata"]["name"]):
    st = a.get("status", {})
    sync = st.get("sync", {}).get("status", "?")
    health = st.get("health", {}).get("status", "?")
    msg = st.get("health", {}).get("message", "") or ""
    if not msg:
        for c in st.get("conditions", []) or []:
            if c.get("type", "").endswith("Error") or c.get("type") == "ComparisonError":
                msg = c.get("message", ""); break
    if not msg:
        ops = st.get("operationState", {}) or {}
        msg = ops.get("message", "") or ""
    flag = "" if (sync == "Synced" and health == "Healthy") else "  <-- "
    print(f"  {a['metadata']['name']:<18} {sync:<10} {health:<12}{flag}{msg[:150]}")
' 2>/dev/null || warn "could not read applications"

# --------------------------------------------------------------------------
step "What channels does this cluster's catalog actually offer?"
# --------------------------------------------------------------------------
printf '  %-34s %-18s %s\n' PACKAGE DEFAULT ALL
for p in advanced-cluster-management rhacs-operator rhods-operator \
         openshift-pipelines-operator-rh rhtas-operator rhdh \
         openshift-gitops-operator; do
  line="$(oc get packagemanifest "$p" -n openshift-marketplace -o json 2>/dev/null \
    | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: print("|"); raise SystemExit
s=d.get("status",{})
print(s.get("defaultChannel","?")+"|"+" ".join(c["name"] for c in s.get("channels",[])))
' 2>/dev/null)"
  if [[ -z "${line}" || "${line}" == "|" ]]; then
    printf '  %-34s %s\n' "$p" "${RED}NOT IN THIS CATALOG${RST}"
  else
    printf '  %-34s %-18s %s\n' "$p" "${line%%|*}" "${line#*|}"
  fi
done
cat <<'MSG'

     gitops/components/operators/subscriptions.yaml sets no channel at all, so
     OLM uses the DEFAULT column above. That is deliberate - see the file
     header. Pin one only if you have a reason, and only after reading this.
MSG

# --------------------------------------------------------------------------
step "Did the operators install?"
# --------------------------------------------------------------------------
printf '  %-34s %-18s %-22s %s\n' SUBSCRIPTION STATE INSTALLED-CSV NOTE
oc get subscriptions.operators.coreos.com -A -o json 2>/dev/null | python3 -c '
import json, sys
d = json.load(sys.stdin)
for s in sorted(d.get("items", []), key=lambda x: x["metadata"]["name"]):
    st = s.get("status", {}) or {}
    name = s["metadata"]["name"]
    state = st.get("state", "-")
    csv = st.get("installedCSV", "-") or "-"
    note = ""
    for c in st.get("conditions", []) or []:
        if c.get("type") == "ResolutionFailed" and c.get("status") == "True":
            note = "RESOLUTION FAILED: " + (c.get("message") or "")[:110]
        elif c.get("type") == "CatalogSourcesUnhealthy" and c.get("status") == "True" and not note:
            note = "catalog unhealthy"
    print(f"  {name:<34} {state:<18} {csv[:22]:<22} {note}")
' 2>/dev/null || warn "could not read subscriptions"

# --------------------------------------------------------------------------
step "CRDs the manifests need"
# --------------------------------------------------------------------------
# Second-order CRDs do not come from the operator - they appear when an
# operator's own CR reconciles. Those are the ones that look inexplicable.
check_crd() {
  local crd="$1" who="$2"
  if oc get crd "${crd}" >/dev/null 2>&1; then
    printf '  %s%-8s%s %-58s %s\n' "${GRN}" "present" "${RST}" "${crd}" "${who}"
  else
    printf '  %s%-8s%s %-58s %s\n' "${RED}" "MISSING" "${RST}" "${crd}" "${who}"
  fi
}
check_crd multiclusterhubs.operator.open-cluster-management.io "advanced-cluster-management"
check_crd centrals.platform.stackrox.io                        "rhacs-operator"
check_crd securedclusters.platform.stackrox.io                 "rhacs-operator"
check_crd dscinitializations.dscinitialization.opendatahub.io  "rhods-operator"
check_crd datascienceclusters.datasciencecluster.opendatahub.io "rhods-operator"
check_crd securesigns.rhtas.redhat.com                         "rhtas-operator"
check_crd backstages.rhdh.redhat.com                           "rhdh"
check_crd pipelines.tekton.dev                                 "openshift-pipelines"
check_crd tasks.tekton.dev                                     "openshift-pipelines"
echo
echo "  second-order - these appear only after an operator CR reconciles:"
check_crd inferenceservices.serving.kserve.io   "<- DataScienceCluster (KServe component)"
check_crd servingruntimes.serving.kserve.io     "<- DataScienceCluster (KServe component)"
check_crd policies.policy.open-cluster-management.io "<- MultiClusterHub"
check_crd placements.cluster.open-cluster-management.io "<- MultiClusterHub"
check_crd securitypolicies.config.stackrox.io   "<- RHACS, version dependent (optional)"

# --------------------------------------------------------------------------
step "Operator CRs"
# --------------------------------------------------------------------------
for item in "multiclusterhub:multiclusterhub:open-cluster-management:{.status.phase}" \
            "central:stackrox-central-services:stackrox:{.status.conditions[?(@.type=='Deployed')].status}" \
            "datasciencecluster:default-dsc::{.status.phase}" \
            "securesign:securesign:trusted-artifact-signer:{.status.conditions[?(@.type=='Ready')].status}"; do
  IFS=: read -r kind name ns path <<< "${item}"
  args=("${kind}" "${name}")
  [[ -n "${ns}" ]] && args+=(-n "${ns}")
  if val="$(oc get "${args[@]}" -o jsonpath="${path}" 2>/dev/null)"; then
    printf '  %-22s %s\n' "${kind}" "${val:-<no status yet>}"
  else
    printf '  %-22s %s\n' "${kind}" "not created (its CRD is probably missing above)"
  fi
done

# --------------------------------------------------------------------------
step "Is the Subscription health check in place?"
# --------------------------------------------------------------------------
if oc get argocd openshift-gitops -n openshift-gitops \
     -o jsonpath='{.spec.resourceHealthChecks}' 2>/dev/null | grep -q Subscription; then
  ok "yes - sync waves will wait for operators to actually install"
else
  warn "NO. Without it a Subscription reads Healthy the instant it exists, so"
  warn "wave -5 finishes before any CRD is registered and wave 0 fails on"
  warn "missing kinds. Apply it:"
  warn "  oc patch argocd openshift-gitops -n openshift-gitops --type merge \\"
  warn "    --patch-file gitops/bootstrap/04-argocd-subscription-health.yaml"
fi

echo
info "After fixing a channel or applying the health check, re-sync from the top:"
echo "     oc -n openshift-gitops patch application lightwell-inward-root \\"
echo "       --type merge -p '{\"operation\":{\"sync\":{\"revision\":\"main\"}}}'"
