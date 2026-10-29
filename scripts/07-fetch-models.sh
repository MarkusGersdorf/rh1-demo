#!/usr/bin/env bash
# Populate the model PVCs: the Foundation-Sec GGUF and the Antares weights
# plus the Antares CLI.
#
#   ./scripts/07-fetch-models.sh          # apply both and wait
#   ./scripts/07-fetch-models.sh antares  # just the Antares half
#   ./scripts/07-fetch-models.sh model    # just the Foundation-Sec half
#
# WHY THIS IS A SCRIPT AND NOT RECONCILED BY ARGO CD
#
# Each Job writes a ReadWriteOnce PVC that an InferenceService then mounts
# and keeps mounted. Once a predictor is serving, the volume is attached to
# its node and a Job wanting the same volume on another node can never
# schedule. Argo CD recreating these Jobs on every sync therefore produced a
# pod that sits Pending forever - and because antares-fetch also mounts
# antares-cli, it took antares-localize down with it every time:
#
#   Multi-Attach error for volume "pvc-...": Volume is already used by
#   pod(s) antares-fetch-...
#
# So the Jobs live in gitops/components/rhoai/fetch-jobs.yaml, outside the
# kustomization, and are applied deliberately from here.
source "$(dirname "$0")/lib.sh"
require_cluster
ROOT="$(repo_root)"
NS=lightwell-inward
MANIFEST="${ROOT}/gitops/components/rhoai/fetch-jobs.yaml"
[[ -f "${MANIFEST}" ]] || die "missing ${MANIFEST}"

WHICH="${1:-both}"
case "${WHICH}" in
  both|antares|model) ;;
  *) die "usage: $0 [both|antares|model]" ;;
esac

# A predictor holding the volume is the one thing that makes these Jobs
# unschedulable, so stand it down first and bring it back afterwards. Scaling
# the Deployment rather than deleting the InferenceService keeps KServe's
# object intact - Argo CD will not fight a replica count it does not manage.
predictor_for() {
  case "$1" in
    antares) echo "antares-predictor" ;;
    model)   echo "foundation-sec-reasoning-predictor" ;;
  esac
}

job_for() {
  case "$1" in
    antares) echo "antares-fetch" ;;
    model)   echo "model-fetch" ;;
  esac
}

run_one() {
  local kind="$1" dep job
  dep="$(predictor_for "${kind}")"
  job="$(job_for "${kind}")"

  step "${job}"
  local scaled=0
  if oc get deploy "${dep}" -n "${NS}" >/dev/null 2>&1; then
    local replicas
    replicas="$(oc get deploy "${dep}" -n "${NS}" -o jsonpath='{.spec.replicas}')"
    if [[ "${replicas}" != "0" ]]; then
      info "scaling ${dep} to 0 so the Job can attach its RWO volume"
      oc scale deploy "${dep}" -n "${NS}" --replicas=0 >/dev/null
      oc wait --for=delete pod -l "app=${dep}" -n "${NS}" --timeout=180s >/dev/null 2>&1 || true
      scaled=1
    fi
  else
    info "${dep} not present yet - nothing to stand down"
  fi

  # Jobs are immutable, so a previous run has to go before this one lands.
  oc delete job "${job}" -n "${NS}" --ignore-not-found --wait=true >/dev/null 2>&1

  # Apply only the Job we want. The file holds both.
  oc apply -f "${MANIFEST}" --selector="lightwell-inward.redhat.com/fetch=${kind}" >/dev/null 2>&1 \
    || oc apply -f "${MANIFEST}" >/dev/null

  info "waiting for ${job} (the first run downloads several GB - this is slow)"
  if oc wait --for=condition=complete "job/${job}" -n "${NS}" --timeout=90m 2>/dev/null; then
    ok "${job} completed"
  else
    warn "${job} did not complete - check: oc logs -n ${NS} job/${job}"
  fi

  if (( scaled )); then
    info "scaling ${dep} back up"
    oc scale deploy "${dep}" -n "${NS}" --replicas=1 >/dev/null
  fi
}

[[ "${WHICH}" == "both" || "${WHICH}" == "model"   ]] && run_one model
[[ "${WHICH}" == "both" || "${WHICH}" == "antares" ]] && run_one antares

cat <<MSG

$(ok "model fetch complete")

  The predictors are scaled back up. Give them a minute to load, then:
    oc get inferenceservice -n ${NS}

  Re-running this script is safe: both fetch scripts are idempotent and
  exit immediately when the data is already on the volume.
MSG
