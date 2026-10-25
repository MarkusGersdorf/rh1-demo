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

step "GitHub token"
# Read back from the secret bootstrap.sh wrote; it only prompts if that is
# missing. Re-running bootstrap.sh and then this script is the rotation path.
GH_TOKEN="$(github_token)"
github_token_works "${GH_TOKEN}" \
  || die "the stored token can no longer read $(repo_slug) - re-run scripts/bootstrap.sh with a fresh one"
ok "token reads $(repo_slug)"

# Also refreshed here so a rotation does not need a separate step. bootstrap.sh
# creates this one first, because the BuildConfig's initial build fires during
# the very first sync.
oc apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: github-source
  namespace: ${NS}
type: kubernetes.io/basic-auth
stringData:
  username: $(yaml_str "$(repo_owner)")
  password: $(yaml_str "${GH_TOKEN}")
EOF
ok "github-source refreshed in ${NS}"

step "RHDH secrets"
# RHDH loads its catalog from raw.githubusercontent.com, which the repo being
# private makes unreadable without GITHUB_TOKEN - app-config.yaml's
# integrations.github resolves it from here.
#
# Written whole rather than patched, so a token rotation is just a re-run.
# Existing values win: a fresh BACKEND_SECRET invalidates every RHDH session,
# and a fresh Gitea password would drift from 04-seed-gitea.sh.
rhdh_value() {
  oc get secret rhdh-secrets -n rhdh -o jsonpath="{.data.$1}" 2>/dev/null \
    | base64 -d 2>/dev/null || true
}
BACKEND="$(rhdh_value BACKEND_SECRET)"; BACKEND="${BACKEND:-$(openssl rand -base64 32)}"
GT_USER="$(rhdh_value GITEA_USERNAME)"; GT_USER="${GT_USER:-demo}"
GT_PASS="$(rhdh_value GITEA_PASSWORD)"; GT_PASS="${GT_PASS:-${GITEA_PASSWORD:-demo-password-change-me}}"

oc apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: rhdh-secrets
  namespace: rhdh
type: Opaque
stringData:
  BACKEND_SECRET: $(yaml_str "${BACKEND}")
  GITEA_USERNAME: $(yaml_str "${GT_USER}")
  GITEA_PASSWORD: $(yaml_str "${GT_PASS}")
  GITHUB_TOKEN: $(yaml_str "${GH_TOKEN}")
EOF
ok "rhdh-secrets written (BACKEND_SECRET, GITEA_USERNAME, GITEA_PASSWORD, GITHUB_TOKEN)"
if [[ "${GT_PASS}" == "demo-password-change-me" ]]; then
  warn "GITEA_PASSWORD defaulted - keep it in step with 04-seed-gitea.sh"
fi

# extraEnvs are resolved at pod start, so a running RHDH keeps the old values.
# Harmless to redo: the catalog is refetched and the database is in-memory.
if oc get deploy backstage-rhdh -n rhdh >/dev/null 2>&1; then
  oc rollout restart deploy/backstage-rhdh -n rhdh >/dev/null
  ok "restarted RHDH so it picks the secret up"
else
  info "RHDH not deployed yet - it will read the secret on first start"
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
