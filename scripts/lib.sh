#!/usr/bin/env bash
# Shared helpers. Sourced, not executed.

set -euo pipefail

RED=$'\033[31m'; GRN=$'\033[32m'; YLW=$'\033[33m'; CYN=$'\033[36m'; DIM=$'\033[2m'; RST=$'\033[0m'

info()  { printf '%s==>%s %s\n' "${CYN}" "${RST}" "$*"; }
ok()    { printf '%s  ok%s %s\n' "${GRN}" "${RST}" "$*"; }
warn()  { printf '%swarn%s %s\n' "${YLW}" "${RST}" "$*" >&2; }
die()   { printf '%sfail%s %s\n' "${RED}" "${RST}" "$*" >&2; exit 1; }
step()  { printf '\n%s%s%s\n' "${DIM}" "$(printf '─%.0s' {1..70})" "${RST}"; info "$*"; }

need() {
  command -v "$1" >/dev/null 2>&1 || die "$1 is required but not on PATH"
}

require_cluster() {
  need oc
  oc whoami >/dev/null 2>&1 || die "not logged in to a cluster (try: oc login ...)"
  oc auth can-i create namespace >/dev/null 2>&1 || \
    die "you need cluster-admin for this demo"
}

cluster_domain() {
  oc get ingresses.config.openshift.io cluster \
    -o jsonpath='{.spec.domain}' 2>/dev/null | sed 's/^apps\.//'
}

# wait_for <description> <timeout-seconds> <command...>
wait_for() {
  local desc="$1" timeout="$2"; shift 2
  local waited=0
  printf '     waiting for %s ' "${desc}"
  while ! "$@" >/dev/null 2>&1; do
    if (( waited >= timeout )); then
      printf ' %stimeout%s\n' "${RED}" "${RST}"
      return 1
    fi
    printf '.'
    sleep 10
    waited=$(( waited + 10 ))
  done
  printf ' %sok%s (%ds)\n' "${GRN}" "${RST}" "${waited}"
}

repo_root() {
  git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel
}
