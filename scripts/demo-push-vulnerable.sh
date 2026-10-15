#!/usr/bin/env bash
# Act 1. The developer adds the statements endpoint, with the authorisation
# check whose result goes nowhere. Pushes to main; the webhook fires.
source "$(dirname "$0")/lib.sh"
ROOT="$(repo_root)"
CLONE="${ROOT}/.local/accounts-api"
[[ -d "${CLONE}" ]] || die "no local clone - run ./scripts/04-seed-gitea.sh first"

cd "${CLONE}"
cp "${ROOT}/components/vuln-demo-app/src/main/java/com/redhat/demo/accounts/AccountResource.java" \
   src/main/java/com/redhat/demo/accounts/AccountResource.java
mkdir -p src/test/java/com/redhat/demo/accounts
cp "${CLONE}/.demo/AccountResourceTest.java" \
   src/test/java/com/redhat/demo/accounts/AccountResourceTest.java

git add -A
git commit -q -m "RHONE-4412 add statement listing endpoint

Lets the mobile client page through account history with an optional
date range. Owner-only, same as the account lookup.

Tests: owner happy path, unknown account, date filtering."

SHA="$(git rev-parse --short HEAD)"
git -c http.sslVerify=false push -q origin main

cat <<MSG

$(ok "pushed ${SHA} to main")

  $(git log -1 --pretty='%s')

  Semgrep will pass this. The tests pass. The AI review will not.

Watch it:
  tkn pipelinerun logs -f -n lightwell-inward --last
MSG
