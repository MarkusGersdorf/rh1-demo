#!/usr/bin/env bash
# Build the scanner image in-cluster and wait for it to come up.
source "$(dirname "$0")/lib.sh"
require_cluster
NS=lightwell-inward

step "Starting the build"
oc get buildconfig ai-code-review -n "${NS}" >/dev/null 2>&1 \
  || die "no ai-code-review BuildConfig - is the ai-code-review Application synced?"

oc start-build ai-code-review -n "${NS}" --follow --wait \
  || die "build failed - see the log above"

step "Waiting for the scanner"
wait_for "ai-code-review rollout" 300 \
  oc rollout status deploy/ai-code-review -n "${NS}" --timeout=10s

URL="https://$(oc get route ai-code-review -n "${NS}" -o jsonpath='{.spec.host}')"
ok "scanner up at ${URL}"
curl -sk "${URL}/readyz" | sed 's/^/     /'
echo
