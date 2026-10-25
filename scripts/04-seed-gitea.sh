#!/usr/bin/env bash
# Create the Gitea admin user, the demo repo, its webhook, and a local clone
# with main sitting at the clean base commit.
#
# After this, the stage flow is two commands:
#   ./scripts/demo-push-vulnerable.sh   -> pipeline blocks
#   ./scripts/demo-push-fix.sh          -> pipeline passes, signs, attests
source "$(dirname "$0")/lib.sh"
require_cluster
need git
need curl
need jq

GITEA_USER=demo
GITEA_PASSWORD="${GITEA_PASSWORD:-demo-password-change-me}"
REPO=accounts-api
ROOT="$(repo_root)"
CLONE="${ROOT}/.local/${REPO}"

step "Waiting for Gitea"
wait_for "gitea deployment" 600 \
  oc rollout status deploy/gitea -n gitea --timeout=10s

GITEA_HOST="$(oc get route gitea -n gitea -o jsonpath='{.spec.host}')"
GITEA_URL="https://${GITEA_HOST}"
info "gitea: ${GITEA_URL}"

step "Admin user"
if oc exec -n gitea deploy/gitea -- \
     su git -c "gitea admin user list --admin" 2>/dev/null | grep -q "${GITEA_USER}"; then
  ok "user ${GITEA_USER} exists"
else
  oc exec -n gitea deploy/gitea -- su git -c \
    "gitea admin user create --username ${GITEA_USER} --password '${GITEA_PASSWORD}' \
     --email demo@redhat.com --admin --must-change-password=false" \
    || die "could not create the Gitea admin user"
  ok "created ${GITEA_USER}"
fi

api() {
  local method="$1" path="$2"; shift 2
  curl -sk -u "${GITEA_USER}:${GITEA_PASSWORD}" -X "${method}" \
    -H "Content-Type: application/json" "${GITEA_URL}/api/v1${path}" "$@"
}

step "Repository"
if api GET "/repos/${GITEA_USER}/${REPO}" | jq -e '.id' >/dev/null 2>&1; then
  ok "repo ${GITEA_USER}/${REPO} exists"
else
  api POST /user/repos -d "$(jq -n --arg n "${REPO}" \
    '{name:$n, description:"Account and statement API", private:false, auto_init:false, default_branch:"main"}')" \
    | jq -e '.id' >/dev/null || die "could not create the repo"
  ok "created ${GITEA_USER}/${REPO}"
fi

step "Building the git history"
# main = the clean base. The vulnerable change and its fix are applied on
# stage by demo-push-vulnerable.sh / demo-push-fix.sh, so the audience sees a
# push cause a pipeline rather than a pre-baked result.
rm -rf "${CLONE}"
mkdir -p "$(dirname "${CLONE}")"
cp -R "${ROOT}/components/vuln-demo-app" "${CLONE}"
rm -rf "${CLONE}/.demo"

# Base version of the resource: no statements endpoint yet.
cp "${ROOT}/components/vuln-demo-app/.demo/AccountResource.base.java" \
   "${CLONE}/src/main/java/com/redhat/demo/accounts/AccountResource.java"

# The tests exercise the statements endpoint, which does not exist at base.
# Keep the base commit green by holding them back with the feature.
mkdir -p "${CLONE}/.demo"
mv "${CLONE}/src/test/java/com/redhat/demo/accounts/AccountResourceTest.java" \
   "${CLONE}/.demo/AccountResourceTest.java"

cat > "${CLONE}/catalog-info.yaml" <<'YAML'
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: accounts-api
  description: Account and statement API.
  annotations:
    janus-idp.io/tekton: accounts-api
spec:
  type: service
  lifecycle: production
  owner: payments-team
  system: retail-banking
YAML

cat > "${CLONE}/.gitignore" <<'YAML'
target/
.demo/
YAML

cd "${CLONE}"
git init -q -b main
git config user.email "dev@redhat.com"
git config user.name "Demo Developer"
git add -A
git commit -q -m "Initial accounts API

Account lookup with owner authorisation. No statement history yet."

# Credentials go in a 0600 file read by git's store helper, NOT in the remote
# URL. Embedding them in the URL writes the password into .git/config, where
# `git remote -v` will happily print it - and this is a demo that gets
# projected. The file is gitignored; the remote stays clean.
CREDS="${ROOT}/.local/gitea-credentials"
install -m 0600 /dev/null "${CREDS}"
printf 'https://%s:%s@%s\n' "${GITEA_USER}" "${GITEA_PASSWORD}" "${GITEA_HOST}" > "${CREDS}"

git remote add origin "https://${GITEA_HOST}/${GITEA_USER}/${REPO}.git"
git config credential.helper "store --file=${CREDS}"
git config http.sslVerify false
git push -q --force -u origin main
ok "pushed base commit $(git rev-parse --short HEAD) to main"

step "Webhook"
EL_HOST="$(oc get route lightwell-inward-webhook -n lightwell-inward -o jsonpath='{.spec.host}' 2>/dev/null || true)"
if [[ -z "${EL_HOST}" ]]; then
  warn "EventListener route not found - is the pipelines Application synced?"
  warn "Create the webhook later by re-running this script."
else
  HOOK_URL="https://${EL_HOST}"
  EXISTING="$(api GET "/repos/${GITEA_USER}/${REPO}/hooks" | jq -r --arg u "${HOOK_URL}" \
    '.[]? | select(.config.url==$u) | .id')"
  if [[ -n "${EXISTING}" ]]; then
    ok "webhook already points at ${HOOK_URL}"
  else
    api POST "/repos/${GITEA_USER}/${REPO}/hooks" -d "$(jq -n --arg u "${HOOK_URL}" '{
      type:"gitea", active:true, events:["push"],
      config:{url:$u, content_type:"json", http_method:"post"}
    }')" | jq -e '.id' >/dev/null \
      && ok "webhook -> ${HOOK_URL}" \
      || warn "could not create the webhook; add it by hand in the Gitea UI"
  fi
fi

cat <<MSG

$(ok "Gitea seeded")

  repo       ${GITEA_URL}/${GITEA_USER}/${REPO}
  login      ${GITEA_USER}  (password in \${GITEA_PASSWORD}, or .local/gitea-credentials)
  local clone ${CLONE}  (main at the clean base commit)

Stage flow:
  ./scripts/demo-push-vulnerable.sh    the developer's change - pipeline blocks
  ./scripts/demo-push-fix.sh           the fix - pipeline signs and attests
MSG
