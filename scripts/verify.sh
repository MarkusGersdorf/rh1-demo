#!/usr/bin/env bash
# Pre-flight the demo itself. Run it the morning of, and again ten minutes
# before you walk on. Every check maps to something the audience would see fail.
source "$(dirname "$0")/lib.sh"
require_cluster

FAIL=0
check() {
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then
    ok "${desc}"
  else
    printf '%sfail%s %s\n' "${RED}" "${RST}" "${desc}"
    FAIL=$(( FAIL + 1 ))
  fi
}

step "GitOps"
check "all Argo CD applications Synced" bash -c \
  '[[ -z "$(oc get applications -n openshift-gitops -o jsonpath="{range .items[*]}{.status.sync.status}{\"\n\"}{end}" | grep -v Synced)" ]]'
check "all Argo CD applications Healthy" bash -c \
  '[[ -z "$(oc get applications -n openshift-gitops -o jsonpath="{range .items[*]}{.status.health.status}{\"\n\"}{end}" | grep -v Healthy)" ]]'

step "Operators"
check "RHACM MultiClusterHub Running" bash -c \
  '[[ "$(oc get multiclusterhub multiclusterhub -n open-cluster-management -o jsonpath="{.status.phase}")" == "Running" ]]'
check "RHACS Central up"        oc rollout status deploy/central -n stackrox --timeout=5s
check "RHACS sensor up"         oc rollout status deploy/sensor -n stackrox --timeout=5s
check "RHACS admission ctrl up" oc rollout status deploy/admission-control -n stackrox --timeout=5s
check "RHOAI DataScienceCluster ready" bash -c \
  '[[ "$(oc get datasciencecluster default-dsc -o jsonpath="{.status.phase}")" == "Ready" ]]'
check "RHTAS Rekor up"          oc rollout status deploy/rekor-server -n trusted-artifact-signer --timeout=5s
check "Developer Hub up"        bash -c 'oc get deploy -n rhdh -o name | grep -q backstage'
check "Gitea up"                oc rollout status deploy/gitea -n gitea --timeout=5s
check "Tekton controller up"    oc rollout status deploy/tekton-pipelines-controller -n openshift-pipelines --timeout=5s

step "The demo's own pieces"
check "scanner deployment up"   oc rollout status deploy/ai-code-review -n lightwell-inward --timeout=5s
check "cosign-keypair secret"   oc get secret cosign-keypair -n lightwell-inward
check "acs-api-token secret"    oc get secret acs-api-token -n lightwell-inward
check "EventListener up"        oc rollout status deploy/el-lightwell-inward -n lightwell-inward --timeout=5s
check "pipeline present"        oc get pipeline lightwell-inward -n lightwell-inward
check "all 8 tasks present"     bash -c \
  '[[ "$(oc get tasks -n lightwell-inward --no-headers | wc -l | tr -d " ")" -ge 8 ]]'
check "RHACM policy compliant"  bash -c \
  '[[ "$(oc get policy lightwell-inward-gate -n open-cluster-management -o jsonpath="{.status.compliant}")" == "Compliant" ]]'

step "Scanner behaviour"
SCANNER="https://$(oc get route ai-code-review -n lightwell-inward -o jsonpath='{.spec.host}' 2>/dev/null || true)"
if [[ -n "${SCANNER}" ]]; then
  READY="$(curl -sk --max-time 10 "${SCANNER}/readyz" || true)"
  if grep -q '"status":"ok"' <<< "${READY}"; then
    ok "scanner /readyz ok: ${READY}"
  else
    printf '%sfail%s scanner not ready: %s\n' "${RED}" "${RST}" "${READY:-<no response>}"
    FAIL=$(( FAIL + 1 ))
  fi

  BACKEND="$(oc get deploy ai-code-review -n lightwell-inward \
    -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="SCANNER_BACKEND")].value}' 2>/dev/null)"
  BACKEND="${BACKEND:-$(oc get cm ai-code-review-config -n lightwell-inward -o jsonpath='{.data.SCANNER_BACKEND}')}"
  info "backend: ${BACKEND}"
  if [[ "${BACKEND}" == "replay" ]]; then
    PROV="$(oc exec -n lightwell-inward deploy/ai-code-review -- \
      sh -c 'grep -ho "\"provenance\": \"[a-z-]*\"" /app/replay/*.json | sort -u' 2>/dev/null || true)"
    info "replay provenance: ${PROV:-unknown}"
    grep -q 'hand-authored-seed' <<< "${PROV}" && \
      warn "replay cache still contains hand-authored seeds. Run scripts/capture-replay.sh, or be explicit on stage that the finding shown is the expected answer rather than a live capture."
  fi
else
  printf '%sfail%s no scanner route\n' "${RED}" "${RST}"
  FAIL=$(( FAIL + 1 ))
fi

step "Model serving"
for item in "foundation-sec-reasoning:Stage B (reasoning)" \
            "antares:Stage A (localizer)"; do
  IFS=: read -r isvc label <<< "${item}"
  if oc get inferenceservice "${isvc}" -n lightwell-inward >/dev/null 2>&1; then
    R="$(oc get inferenceservice "${isvc}" -n lightwell-inward \
      -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)"
    if [[ "${R}" == "True" ]]; then
      ok "${label}: ${isvc} Ready"
    else
      warn "${label}: ${isvc} not Ready (Stage B is fine on replay; Stage A is advisory)"
    fi
  else
    warn "${label}: no InferenceService ${isvc}"
  fi
done

step "Antares CLI"
if oc get pvc antares-cli -n lightwell-inward >/dev/null 2>&1; then
  # The CLI arrives inside the gated HF download, so its absence is the most
  # likely Stage A failure and it is silent - the task just skips.
  if oc get job antares-fetch -n lightwell-inward \
      -o jsonpath='{.status.succeeded}' 2>/dev/null | grep -q 1; then
    ok "antares-fetch completed - CLI should be on the PVC"
  else
    warn "antares-fetch has not succeeded. Stage A will skip (advisory, so the"
    warn "demo still runs). Check: oc logs job/antares-fetch -n lightwell-inward"
  fi
else
  warn "no antares-cli PVC - is the rhoai Application synced?"
fi

step "Git repo"
check "local clone present" test -d "$(repo_root)/.local/accounts-api"
if [[ -d "$(repo_root)/.local/accounts-api" ]]; then
  HEAD_MSG="$(git -C "$(repo_root)/.local/accounts-api" log -1 --pretty='%s' 2>/dev/null || true)"
  info "main is at: ${HEAD_MSG}"
  if [[ "${HEAD_MSG}" == Initial* ]]; then
    ok "repo is at the clean base - ready for demo-push-vulnerable.sh"
  else
    warn "repo is NOT at the base commit. Run ./scripts/demo-reset.sh before the demo."
  fi
fi

step "Routes you will open on stage"
for item in "openshift-gitops:openshift-gitops-server:Argo CD" \
            "rhdh:backstage-rhdh:Developer Hub" \
            "gitea:gitea:Gitea" \
            "stackrox:central:RHACS" \
            "lightwell-inward:ai-code-review:Scanner UI" \
            "trusted-artifact-signer:rekor-search-ui:Rekor Search" \
            "redhat-ods-applications:rhods-dashboard:RHOAI"; do
  IFS=: read -r ns route label <<< "${item}"
  HOST="$(oc get route "${route}" -n "${ns}" -o jsonpath='{.spec.host}' 2>/dev/null || true)"
  if [[ -n "${HOST}" ]]; then
    printf '     %-18s https://%s\n' "${label}" "${HOST}"
  else
    printf '     %-18s %s(no route)%s\n' "${label}" "${YLW}" "${RST}"
  fi
done

echo
if (( FAIL )); then
  die "${FAIL} check(s) failed - see above"
fi
ok "everything the demo needs is up"
