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

# ---------------------------------------------------------------------------
# Reading this repo from the cluster.
#
# Three things fetch it over https and none of them can do that anonymously
# while it is private: Argo CD (every Application's repoURL), the scanner's
# BuildConfig, and RHDH (the catalog lives on raw.githubusercontent.com).
# They all take the same token; these helpers resolve it once so it cannot
# drift between the three.
# ---------------------------------------------------------------------------

# Where bootstrap.sh parks the token. Everything else reads it back from here
# rather than asking again.
REPO_SECRET="${REPO_SECRET:-lightwell-inward-repo}"

# origin as https with .git, which is the exact string the manifests use.
# A credential registered against any other spelling is silently ignored -
# Argo CD matches repository secrets by URL.
repo_https_url() {
  local url="${REPO_URL:-}"
  [[ -n "${url}" ]] || url="$(git -C "$(repo_root)" remote get-url origin 2>/dev/null || true)"
  [[ -n "${url}" ]] || die "no git remote 'origin' - set REPO_URL=https://github.com/you/rh1-demo.git"
  url="$(printf '%s' "${url}" | sed -E 's|^git@([^:]+):|https://\1/|')"
  printf '%s' "${url%.git}.git"
}

repo_slug()  { printf '%s' "$(repo_https_url)" | sed -E 's|^https?://[^/]+/||; s|\.git$||'; }
repo_owner() { local s; s="$(repo_slug)"; printf '%s' "${s%%/*}"; }

# GitHub answers a private repo with 404 rather than 403, so "does not exist"
# and "you have no credentials" arrive as the same signal. Treat anything but
# a clean 200 as needing a token.
repo_is_public() {
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 \
          "$(repo_https_url)/info/refs?service=git-upload-pack" 2>/dev/null || echo 000)"
  [[ "${code}" == "200" ]]
}

# $GITHUB_TOKEN, else the one bootstrap.sh already stored, else ask. Export
# GITHUB_TOKEN to skip the prompt in an unattended run.
github_token() {
  local t="${GITHUB_TOKEN:-}"
  if [[ -z "${t}" ]]; then
    t="$(oc get secret "${REPO_SECRET}" -n openshift-gitops \
         -o jsonpath='{.data.password}' 2>/dev/null | base64 -d 2>/dev/null || true)"
  fi
  if [[ -z "${t}" ]]; then
    [[ -r /dev/tty ]] || die "no GITHUB_TOKEN and no terminal to prompt on - export GITHUB_TOKEN=github_pat_..."
    read -rsp "     GitHub token with read access to $(repo_slug) (hidden): " t < /dev/tty
    printf '\n' >&2
  fi
  [[ -n "${t}" ]] || die "empty GitHub token"
  printf '%s' "${t}"
}

# A YAML single-quoted scalar, for interpolating generated or user-supplied
# values into the heredocs below. Only ' needs escaping, by doubling it.
yaml_str() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/''/g")"; }

# Checked before anything is written, because a bad token surfaces otherwise
# as Argo CD's "Repository not found" an hour into the sync.
github_token_works() {
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 \
          -H "Authorization: Bearer $1" \
          "https://api.github.com/repos/$(repo_slug)" 2>/dev/null || echo 000)"
  [[ "${code}" == "200" ]]
}
