#!/usr/bin/env bash
# Install OpenShift GitOps, then hand the cluster to Argo CD.
#
# Everything after this point is reconciled from git. If you find yourself
# running oc apply to fix something, fix it in git instead - that is the
# demo's own claim about itself.
source "$(dirname "$0")/lib.sh"
ROOT="$(repo_root)"
require_cluster

# Check the exact strings that get substituted, in manifests only. README
# files under gitops/ describe these placeholders and must not block a
# bootstrap - an earlier version of this guard died on its own documentation.
if grep -rq --include='*.yaml' -e 'apps\.CLUSTER_DOMAIN' -e 'CHANGEME/rh1-demo' \
     "${ROOT}/gitops" 2>/dev/null; then
  die "gitops manifests still contain placeholders - run ./scripts/01-set-cluster-domain.sh, commit and push first"
fi

step "Installing OpenShift GitOps operator"
oc apply -f "${ROOT}/gitops/bootstrap/01-openshift-gitops-operator.yaml"
wait_for "openshift-gitops namespace" 300 \
  oc get namespace openshift-gitops
wait_for "Argo CD application controller" 600 \
  oc rollout status statefulset/openshift-gitops-application-controller -n openshift-gitops --timeout=10s

step "Granting Argo CD the rights to install operators and cluster policy"
oc apply -f "${ROOT}/gitops/bootstrap/02-argocd-cluster-admin.yaml"

step "Repository credentials"
# Asked for unconditionally, even if the repo is public. Three things fetch it
# over https - Argo CD, the scanner BuildConfig, RHDH - and a conditional
# credential means three code paths that only one of them exercises. A
# read-only token also keeps RHDH's catalog refresh off GitHub's anonymous
# rate limit, which is a stage risk worth spending one prompt on.
if repo_is_public; then
  info "$(repo_slug) is readable anonymously; the token is still used for RHDH"
else
  info "$(repo_slug) is not readable anonymously (private repo, or no egress)"
fi
TOKEN="$(github_token)"
github_token_works "${TOKEN}" \
  || die "that token cannot read $(repo_slug) - a fine-grained token needs Contents: read-only on this repo"

# Written through a heredoc rather than --from-literal: the latter puts the
# token in the process list for anyone with `ps` on this machine.
oc apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: ${REPO_SECRET}
  namespace: openshift-gitops
  labels:
    argocd.argoproj.io/secret-type: repository
type: Opaque
stringData:
  type: git
  url: $(yaml_str "$(repo_https_url)")
  username: $(yaml_str "$(repo_owner)")
  password: $(yaml_str "${TOKEN}")
EOF
ok "Argo CD credential stored in openshift-gitops/${REPO_SECRET}"

# The BuildConfig lands at sync wave 10 with a ConfigChange trigger, so its
# first build fires during this sync - before 02-signing-setup.sh runs. Put its
# source secret in place now or that build fails on a clone it cannot
# authenticate. The namespace is in git at wave -10; creating it early just
# means Argo CD adopts it instead of creating it.
oc get namespace lightwell-inward >/dev/null 2>&1 \
  || oc create namespace lightwell-inward >/dev/null
oc apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: github-source
  namespace: lightwell-inward
type: kubernetes.io/basic-auth
stringData:
  username: $(yaml_str "$(repo_owner)")
  password: $(yaml_str "${TOKEN}")
EOF
ok "BuildConfig credential stored in lightwell-inward/github-source"
info "RHDH's copy is written by scripts/02-signing-setup.sh, from this same secret"

step "Applying the root application"
oc apply -f "${ROOT}/gitops/bootstrap/03-root-application.yaml"

cat <<MSG

$(ok "bootstrap applied")

Argo CD is now installing everything else. Expect 30-60 minutes, and expect
transient errors in the UI while CRDs appear - operator CRs are applied in the
same pass as the operators that define them, and Argo retries. That is normal
and it converges.

Watch it:
  oc get applications -n openshift-gitops -w

Argo CD UI:
  https://$(oc get route openshift-gitops-server -n openshift-gitops -o jsonpath='{.spec.host}' 2>/dev/null || echo '<pending>')
  admin / $(oc get secret openshift-gitops-cluster -n openshift-gitops -o jsonpath='{.data.admin\.password}' 2>/dev/null | base64 -d || echo '<pending>')

When the applications are Synced/Healthy, continue with:
  ./scripts/02-signing-setup.sh
  ./scripts/03-acs-init-bundle.sh
  ./scripts/04-seed-gitea.sh
  ./scripts/05-acs-policy.sh
  ./scripts/06-build-scanner.sh
  ./scripts/verify.sh
MSG
