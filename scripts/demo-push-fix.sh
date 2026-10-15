#!/usr/bin/env bash
# Act 2. The developer reads the finding and adds the missing return.
source "$(dirname "$0")/lib.sh"
ROOT="$(repo_root)"
CLONE="${ROOT}/.local/accounts-api"
[[ -d "${CLONE}" ]] || die "no local clone - run ./scripts/04-seed-gitea.sh first"

cd "${CLONE}"
grep -q "statements(" src/main/java/com/redhat/demo/accounts/AccountResource.java \
  || die "the statements endpoint is not present - run ./scripts/demo-push-vulnerable.sh first"

cp "${ROOT}/components/vuln-demo-app/.demo/AccountResource.fixed.java" \
   src/main/java/com/redhat/demo/accounts/AccountResource.java

git add -A
git commit -q -m "RHONE-4412 return 403 when the caller does not own the account

The ownership check was logging the mismatch and then falling through to
serve the statements anyway - the early return was lost in review.

Also stops echoing the date parser's exception message back to the client.

Found by the Lightwell Inward code review gate (CWE-639)."

SHA="$(git rev-parse --short HEAD)"
git -c http.sslVerify=false push -q origin main

cat <<MSG

$(ok "pushed ${SHA} to main")

  $(git log -1 --pretty='%s')

  This run should pass, build, sign, and attest the review verdict.

Watch it:
  tkn pipelinerun logs -f -n lightwell-inward --last
MSG
