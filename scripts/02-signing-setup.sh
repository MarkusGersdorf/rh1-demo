#!/usr/bin/env bash
# Secrets that cannot live in git: the cosign key, and RHDH's env secrets.
source "$(dirname "$0")/lib.sh"
require_cluster
need cosign

NS=lightwell-inward

step "cosign key pair"
if oc get secret cosign-keypair -n "${NS}" >/dev/null 2>&1; then
  ok "cosign-keypair already exists (delete it to rotate)"
else
  TMP="$(mktemp -d)"; trap 'rm -rf "${TMP}"' EXIT
  PASS="$(openssl rand -hex 16)"

  # cosign's k8s:// key reference expects exactly these three keys.
  ( cd "${TMP}" && COSIGN_PASSWORD="${PASS}" cosign generate-key-pair >/dev/null )
  oc create secret generic cosign-keypair -n "${NS}" \
    --from-file=cosign.key="${TMP}/cosign.key" \
    --from-file=cosign.pub="${TMP}/cosign.pub" \
    --from-literal=password="${PASS}"
  ok "created cosign-keypair in ${NS}"

  mkdir -p "$(repo_root)/.local"
  cp "${TMP}/cosign.pub" "$(repo_root)/.local/cosign.pub"
  ok "public key copied to .local/cosign.pub (gitignored; needed by 05-acs-policy.sh)"
fi

step "RHDH secrets"
if oc get secret rhdh-secrets -n rhdh >/dev/null 2>&1; then
  ok "rhdh-secrets already exists"
else
  oc create secret generic rhdh-secrets -n rhdh \
    --from-literal=BACKEND_SECRET="$(openssl rand -base64 32)" \
    --from-literal=GITEA_USERNAME=demo \
    --from-literal=GITEA_PASSWORD="${GITEA_PASSWORD:-demo-password-change-me}"
  ok "created rhdh-secrets"
  warn "GITEA_PASSWORD defaulted - keep it in step with 04-seed-gitea.sh"
fi

step "RHTAS timestamp authority chain"
if oc get securesign securesign -n trusted-artifact-signer -o jsonpath='{.status.conditions}' 2>/dev/null \
   | grep -qi 'tsa-cert-chain'; then
  warn "Securesign is waiting on the tsa-cert-chain secret."
  warn "The demo does not use timestamping. Simplest fix: remove the"
  warn "timestampAuthority block from gitops/components/rhtas/securesign.yaml,"
  warn "commit, and let Argo CD reconcile."
else
  ok "no tsa-cert-chain complaint"
fi
