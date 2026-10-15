#!/usr/bin/env bash
# Preflight. Run before you build anything; it is cheaper than discovering a
# missing operator catalog after 40 minutes of syncing.
source "$(dirname "$0")/lib.sh"
require_cluster

FAIL=0

step "Cluster"
VER="$(oc get clusterversion version -o jsonpath='{.status.desired.version}')"
info "OpenShift ${VER}"
case "${VER}" in
  4.22.*) ok "4.22 as expected" ;;
  *) warn "demo was written against 4.22; API versions may differ on ${VER}" ;;
esac
info "domain apps.$(cluster_domain)"

step "Capacity"
# CPU-only serving of an 8B model needs real headroom: ~8 cores and 16Gi for
# the model pod alone, plus RHACM, RHACS, RHOAI and RHDH on top.
TOTAL_CPU=0; TOTAL_MEM=0
while read -r cpu mem; do
  TOTAL_CPU=$(( TOTAL_CPU + ${cpu%m} / 1000 ))
  TOTAL_MEM=$(( TOTAL_MEM + ${mem%Ki} / 1048576 ))
done < <(oc get nodes -o jsonpath='{range .items[*]}{.status.allocatable.cpu}{" "}{.status.allocatable.memory}{"\n"}{end}' \
          | sed 's/\([0-9]\+\)$/\1000m/' )
info "allocatable: ~${TOTAL_CPU} cores, ~${TOTAL_MEM}Gi across $(oc get nodes --no-headers | wc -l | tr -d ' ') node(s)"
if (( TOTAL_CPU < 24 )); then
  warn "under 24 cores. RHACM+RHACS+RHOAI+RHDH plus an 8B CPU model will be tight."
  warn "either add workers, or run with SCANNER_BACKEND=replay and scale the"
  warn "InferenceService to 0 until you need it."
fi
if (( TOTAL_MEM < 64 )); then
  warn "under 64Gi of memory. Same advice as above."
fi

step "GPU"
if oc get nodes -o jsonpath='{.items[*].status.allocatable}' | grep -q 'nvidia.com/gpu'; then
  ok "GPU node present - you can use the vLLM runtime instead of llama.cpp"
else
  info "no GPU - using the llama.cpp CPU runtime (expected for this build)"
fi

step "Operator catalog"
for p in advanced-cluster-management rhacs-operator rhods-operator \
         openshift-pipelines-operator-rh rhtas-operator rhdh \
         openshift-gitops-operator; do
  CH="$(oc get packagemanifest "$p" -n openshift-marketplace \
        -o jsonpath='{.status.defaultChannel}' 2>/dev/null || true)"
  if [[ -n "${CH}" ]]; then
    ok "$(printf '%-34s' "$p") default channel: ${CH}"
  else
    printf '%sfail%s %-34s NOT in the catalog\n' "${RED}" "${RST}" "$p"
    FAIL=1
  fi
done
cat <<'MSG'

     If a channel above differs from gitops/components/operators/subscriptions.yaml,
     correct the file and commit. Argo CD will not fix a wrong channel for you.
MSG

step "Storage"
SC="$(oc get storageclass -o jsonpath='{range .items[?(@.metadata.annotations.storageclass\.kubernetes\.io/is-default-class=="true")]}{.metadata.name}{end}')"
if [[ -n "${SC}" ]]; then
  ok "default StorageClass: ${SC}"
else
  printf '%sfail%s no default StorageClass - the model PVC will not bind\n' "${RED}" "${RST}"
  FAIL=1
fi

step "Registry credentials"
if oc get secret -n openshift-config pull-secret >/dev/null 2>&1; then
  if oc get secret -n openshift-config pull-secret -o jsonpath='{.data.\.dockerconfigjson}' \
     | base64 -d | grep -q 'registry.redhat.io'; then
    ok "pull secret includes registry.redhat.io"
  else
    warn "pull secret has no registry.redhat.io entry - RHTAS cosign and ec images will not pull"
    FAIL=1
  fi
fi

step "Hugging Face token"
if oc get secret -n lightwell-inward huggingface-token >/dev/null 2>&1; then
  ok "huggingface-token present"
else
  warn "no huggingface-token secret yet. Foundation-Sec is a Llama derivative"
  warn "behind a license acceptance, so the model download needs one:"
  warn "  oc create secret generic huggingface-token -n lightwell-inward --from-literal=token=hf_..."
  warn "Not fatal: set MODEL_GGUF_URL instead, or stay on the replay backend."
fi

echo
if (( FAIL )); then
  die "preflight found blocking problems - fix them before bootstrapping"
fi
ok "preflight passed"
