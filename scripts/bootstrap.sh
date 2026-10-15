#!/usr/bin/env bash
# Install OpenShift GitOps, then hand the cluster to Argo CD.
#
# Everything after this point is reconciled from git. If you find yourself
# running oc apply to fix something, fix it in git instead - that is the
# demo's own claim about itself.
source "$(dirname "$0")/lib.sh"
ROOT="$(repo_root)"
require_cluster

if grep -rq 'CHANGEME' "${ROOT}/gitops" 2>/dev/null; then
  die "gitops/ still contains CHANGEME - run ./scripts/01-set-cluster-domain.sh, commit and push first"
fi

step "Installing OpenShift GitOps operator"
oc apply -f "${ROOT}/gitops/bootstrap/01-openshift-gitops-operator.yaml"
wait_for "openshift-gitops namespace" 300 \
  oc get namespace openshift-gitops
wait_for "Argo CD application controller" 600 \
  oc rollout status statefulset/openshift-gitops-application-controller -n openshift-gitops --timeout=10s

step "Granting Argo CD the rights to install operators and cluster policy"
oc apply -f "${ROOT}/gitops/bootstrap/02-argocd-cluster-admin.yaml"

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
