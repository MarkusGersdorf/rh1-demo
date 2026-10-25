#!/usr/bin/env bash
# Replace the CLUSTER_DOMAIN and repo-URL placeholders throughout the repo.
#
# Run this once, review the diff, commit, push. Argo CD reconciles from your
# fork, so the placeholders must be resolved in git - not just locally.
#
#   ./scripts/01-set-cluster-domain.sh                      # autodetect domain
#   ./scripts/01-set-cluster-domain.sh apps.my.example.com  # or pass it
source "$(dirname "$0")/lib.sh"
ROOT="$(repo_root)"
cd "${ROOT}"

DOMAIN="${1:-}"
if [[ -z "${DOMAIN}" ]]; then
  require_cluster
  DOMAIN="$(cluster_domain)"
  [[ -n "${DOMAIN}" ]] || die "could not detect the cluster domain - pass it as an argument"
fi
DOMAIN="${DOMAIN#apps.}"

REPO_URL="${REPO_URL:-}"
if [[ -z "${REPO_URL}" ]]; then
  REPO_URL="$(git remote get-url origin 2>/dev/null || true)"
  [[ -n "${REPO_URL}" ]] || die "no git remote 'origin' - set REPO_URL=https://github.com/you/rh1-demo.git"
fi
# Normalise ssh remotes to https, which is what Argo CD and RHDH will fetch.
REPO_URL="$(printf '%s' "${REPO_URL}" | sed -E 's|^git@([^:]+):|https://\1/|')"
REPO_URL="${REPO_URL%.git}"

# owner/repo. One substitution of CHANGEME/rh1-demo -> this slug resolves every
# form the repo uses: the Argo CD repoURL (with .git), the RHDH catalog's
# raw.githubusercontent URLs, the BuildConfig source, and the CI badges in
# README.md. Matching on whole URLs instead meant a new URL shape silently
# went unpatched.
REPO_SLUG="$(printf '%s' "${REPO_URL}" | sed -E 's|^https?://[^/]+/||')"
[[ "${REPO_SLUG}" == */* ]] \
  || die "could not read owner/repo from '${REPO_URL}' - set REPO_URL=https://github.com/you/rh1-demo.git"

info "cluster domain : apps.${DOMAIN}"
info "repo slug      : ${REPO_SLUG}"
if [[ "${REPO_URL}" != https://github.com/* ]]; then
  warn "origin is not github.com. The RHDH catalog uses raw.githubusercontent"
  warn "URLs and README.md uses GitHub Actions badges; both will need adjusting"
  warn "by hand. See gitops/components/rhdh/README.md."
fi

# Only files something actually consumes: the manifests Argo CD applies, and
# README.md for the CI badge URLs. Prose under docs/ and the README.md files
# inside gitops/ *describe* these placeholders, so rewriting them corrupts the
# documentation - an earlier version of this script turned "replaces
# apps.CLUSTER_DOMAIN" into "replaces apps.your.cluster.com".
# Match the exact strings that get substituted, not the bare tokens. Comments
# like "CLUSTER_DOMAIN is a placeholder" are documentation and must not count
# as unresolved work.
PATTERNS=(-e 'apps\.CLUSTER_DOMAIN' -e 'CHANGEME/rh1-demo')

# "no match" is the expected answer once a file is resolved, but grep says so
# with exit 1 and errexit takes that out of the command substitution as a
# silent abort. Swallow it: emptiness is handled by the caller.
placeholder_files() {
  {
    grep -rl "${PATTERNS[@]}" --include='*.yaml' gitops 2>/dev/null || true
    grep -l  "${PATTERNS[@]}" README.md 2>/dev/null || true
  } | sort -u
}

FILES="$(placeholder_files)"
[[ -n "${FILES}" ]] || { ok "no placeholders left to replace"; exit 0; }

while IFS= read -r f; do
  perl -pi -e "s{CHANGEME/rh1-demo}{${REPO_SLUG}}g" "$f"
  perl -pi -e "s{apps\.CLUSTER_DOMAIN}{apps.${DOMAIN}}g" "$f"
  echo "   patched $f"
done <<< "${FILES}"

step "Remaining placeholders (should be none)"
# Same narrow set, so documentation that merely mentions the placeholders does
# not read as a failure.
REMAINING="$(placeholder_files)"
if [[ -n "${REMAINING}" ]]; then
  warn "placeholders remain in:"
  printf '       %s\n' ${REMAINING}
else
  ok "all placeholders resolved"
fi

cat <<MSG

Next:
  git diff --stat
  git commit -am "Point demo at apps.${DOMAIN}"
  git push
  ./scripts/bootstrap.sh
MSG
