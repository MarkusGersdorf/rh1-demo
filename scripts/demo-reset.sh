#!/usr/bin/env bash
# Put the demo back to its starting state. Safe to run repeatedly, and worth
# running between rehearsals so the second run looks like the first.
source "$(dirname "$0")/lib.sh"
require_cluster
NS=lightwell-inward
ROOT="$(repo_root)"
CLONE="${ROOT}/.local/accounts-api"

step "Clearing pipeline runs"
oc delete pipelinerun -n "${NS}" --all --ignore-not-found >/dev/null 2>&1 || true
ok "pipeline runs deleted"

step "Removing any deployed demo workload"
oc delete deployment,service,route -n demo-accounts -l app=accounts-api --ignore-not-found >/dev/null 2>&1 || true
oc delete deployment accounts-api -n demo-accounts --ignore-not-found >/dev/null 2>&1 || true
ok "demo-accounts cleaned"

step "Resetting the git repo to the base commit"
if [[ -d "${CLONE}" ]]; then
  cd "${CLONE}"
  BASE="$(git rev-list --max-parents=0 HEAD | tail -1)"
  git reset -q --hard "${BASE}"
  git clean -qfd -e .demo
  git -c http.sslVerify=false push -q --force origin main
  ok "main reset to $(git rev-parse --short HEAD) ($(git log -1 --pretty='%s' | head -1))"
else
  warn "no local clone - run ./scripts/04-seed-gitea.sh"
fi

step "Clearing the scanner's review history"
# The history is in-memory, so a restart is the reset.
oc rollout restart deploy/ai-code-review -n "${NS}" >/dev/null 2>&1 || true
oc rollout status deploy/ai-code-review -n "${NS}" --timeout=120s >/dev/null 2>&1 || true
ok "scanner restarted with an empty history"

step "Removing images and attestations from the previous run"
oc delete imagestreamtag accounts-api:latest -n demo-accounts --ignore-not-found >/dev/null 2>&1 || true
ok "image stream tag cleared"

echo
ok "reset complete - run ./scripts/verify.sh, then you are ready"
