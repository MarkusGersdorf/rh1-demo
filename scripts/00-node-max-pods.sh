#!/usr/bin/env bash
# Raise the kubelet pod ceiling on workers, as far as this cluster allows.
#
# OpenShift defaults to 250 pods per node. This demo installs RHACM, RHACS,
# RHOAI, RHDH, Pipelines, Gitea and two model servers, which saturates that on
# a small cluster.
#
# The ceiling is NOT arbitrary: every pod needs an IP from the node's subnet,
# sized by clusterNetwork hostPrefix, which is fixed at install time. This
# works out what your cluster can actually support, applies it, and waits for
# the Machine Config Operator to finish rolling the workers.
#
# RUN THIS BEFORE bootstrap.sh. It reboots every worker, one at a time. Doing
# it during a sync restarts Argo CD and anything else in flight.
#
#   ./scripts/00-node-max-pods.sh          # compute and apply
#   MAX_PODS=500 ./scripts/00-node-max-pods.sh   # force a value
#   DRY_RUN=1 ./scripts/00-node-max-pods.sh      # report only
source "$(dirname "$0")/lib.sh"
require_cluster
ROOT="$(repo_root)"

# --------------------------------------------------------------------------
step "What this cluster can support"
# --------------------------------------------------------------------------
HOST_PREFIX="$(oc get network.config/cluster \
  -o jsonpath='{.spec.clusterNetwork[0].hostPrefix}' 2>/dev/null || true)"
CIDR="$(oc get network.config/cluster \
  -o jsonpath='{.spec.clusterNetwork[0].cidr}' 2>/dev/null || true)"
[[ -n "${HOST_PREFIX}" ]] || die "could not read clusterNetwork hostPrefix"

# Usable addresses in the node subnet, less a few the SDN reserves.
USABLE=$(( (1 << (32 - HOST_PREFIX)) - 3 ))
info "cluster network : ${CIDR} hostPrefix=${HOST_PREFIX}"
info "per-node IPs    : ~${USABLE} usable"

# Red Hat's supported maximum is 500 pods per node regardless of address space.
SUPPORTED_MAX=500
if   (( HOST_PREFIX <= 22 )); then CEILING=${SUPPORTED_MAX}
elif (( HOST_PREFIX == 23 )); then CEILING=450   # 509 IPs; leave real headroom
else                               CEILING=$(( USABLE - 10 ))
fi
(( CEILING > SUPPORTED_MAX )) && CEILING=${SUPPORTED_MAX}

CURRENT="$(oc get nodes -o jsonpath='{.items[0].status.allocatable.pods}' 2>/dev/null || echo '?')"
info "current maxPods : ${CURRENT}"
info "recommended     : ${CEILING}"

DEFAULT_MAX_PODS=250
if (( CEILING <= DEFAULT_MAX_PODS )); then
  warn "hostPrefix ${HOST_PREFIX} gives only ~${USABLE} addresses per node, so the"
  warn "address-space ceiling (${CEILING}) is at or below OpenShift's ${DEFAULT_MAX_PODS} default."
  warn "Applying it would LOWER your capacity, not raise it, so this script will not."
  warn ""
  warn "hostPrefix is fixed at install time and cannot be changed. The ways up are:"
  warn "  - add worker nodes (the real fix)"
  warn "  - install less: see the DataScienceCluster, which already declares only"
  warn "    dashboard and kserve, and consider Removed for rhdh or gitea"
  warn "  - reinstall with hostPrefix 22 if you need 500 pods on one node"
  die "not applying a lower ceiling than the default"
fi

TARGET="${MAX_PODS:-${CEILING}}"
if [[ -n "${MAX_PODS:-}" ]]; then
  if (( MAX_PODS > CEILING )); then
    warn "MAX_PODS=${MAX_PODS} is above what hostPrefix ${HOST_PREFIX} supports (${CEILING})."
    warn "Pods beyond the subnet's addresses never get an IP and stay Pending."
  fi
  if (( MAX_PODS < 250 )); then
    warn "MAX_PODS=${MAX_PODS} is below OpenShift's 250 default - this lowers capacity."
  fi
fi

# --------------------------------------------------------------------------
step "Is raising it actually the right fix?"
# --------------------------------------------------------------------------
# A pod ceiling raised on a node with no CPU or memory left just converts
# "too many pods" into eviction churn, which is harder to diagnose.
oc get nodes -o json 2>/dev/null | python3 -c '
import json, sys
d = json.load(sys.stdin)
for n in d.get("items", []):
    name = n["metadata"]["name"]
    alloc = n["status"]["allocatable"]
    cpu = alloc.get("cpu", "?")
    mem = int(alloc.get("memory", "0Ki").rstrip("Ki") or 0) // 1048576
    pods = alloc.get("pods", "?")
    roles = ",".join(k.split("/")[1] for k in n["metadata"]["labels"]
                     if k.startswith("node-role.kubernetes.io/")) or "-"
    print(f"  {name[:40]:<40} {roles:<16} cpu={cpu:<6} mem={mem}Gi pods={pods}")
' 2>/dev/null || warn "could not read node capacity"

RUNNING="$(oc get pods -A --no-headers 2>/dev/null | grep -c Running || echo '?')"
info "pods Running cluster-wide: ${RUNNING}"
cat <<'MSG'

     If a node is already short on CPU or memory, raising this ceiling trades
     "pod limit reached" for eviction churn, which is harder to debug. Adding a
     worker is the better fix in that case. The other lever is installing less:
     the DataScienceCluster here already declares only dashboard and kserve.
MSG

# --------------------------------------------------------------------------
step "Applying maxPods=${TARGET}"
# --------------------------------------------------------------------------
MANIFEST="${ROOT}/gitops/components/node-config/kubeletconfig.yaml"
[[ -f "${MANIFEST}" ]] || die "missing ${MANIFEST}"

if [[ -n "${DRY_RUN:-}" ]]; then
  ok "DRY_RUN set - nothing applied. Would set maxPods=${TARGET}."
  exit 0
fi

cat <<MSG
     This reboots every worker, one at a time, via the Machine Config
     Operator. Workloads drain and reschedule. Expect several minutes per node.
MSG
printf '     Continue? [y/N] '
read -r reply < /dev/tty
[[ "${reply}" == "y" || "${reply}" == "Y" ]] || { info "aborted"; exit 0; }

# Keep git and the cluster in step: the manifest is the source of truth and
# Argo CD reconciles it, so edit the file rather than patching live.
if [[ "${TARGET}" != "450" ]]; then
  perl -pi -e "s/^    maxPods: \\d+\$/    maxPods: ${TARGET}/" "${MANIFEST}"
  ok "set maxPods: ${TARGET} in the manifest - commit this"
fi
oc apply -f "${MANIFEST}"

step "Waiting for the workers to roll"
# The MCP goes Updating, then Updated with every machine at the new config.
sleep 15
if ! oc wait --for=condition=Updating=True machineconfigpool/worker --timeout=180s 2>/dev/null; then
  info "pool did not report Updating - it may have applied without a reboot"
fi
oc wait --for=condition=Updated=True machineconfigpool/worker --timeout=90m \
  || die "worker pool did not finish updating - oc describe mcp worker"

step "Result"
oc get nodes -o custom-columns=NAME:.metadata.name,PODS:.status.allocatable.pods \
  --no-headers 2>/dev/null | sed 's/^/  /'
ok "done - commit the manifest change, then run ./scripts/bootstrap.sh"
