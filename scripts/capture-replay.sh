#!/usr/bin/env bash
# Record real model output into the replay cache.
#
# Temporarily switches the scanner to live inference, submits the two demo
# diffs, writes the results as replay entries with provenance=captured, then
# puts the scanner back on replay.
#
# Run this once the model is serving and before the event. Each capture is
# 30-90s on CPU, so budget a few minutes.
#
#   ./scripts/capture-replay.sh
source "$(dirname "$0")/lib.sh"
require_cluster
need curl
need jq
need python3

NS=lightwell-inward
ROOT="$(repo_root)"
REPLAY="${ROOT}/components/ai-code-review/replay"
BACKEND="${CAPTURE_BACKEND:-llamacpp}"

step "Checking the model is serving"
oc get inferenceservice foundation-sec-reasoning -n "${NS}" >/dev/null 2>&1 \
  || die "no InferenceService - is the rhoai Application synced?"
READY="$(oc get inferenceservice foundation-sec-reasoning -n "${NS}" \
  -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || true)"
[[ "${READY}" == "True" ]] || {
  warn "InferenceService is not Ready. On CPU the first load takes several"
  warn "minutes; check:  oc logs -n ${NS} -l serving.kserve.io/inferenceservice=foundation-sec-reasoning"
  die "model not ready"
}
ok "model is serving"

step "Switching the scanner to ${BACKEND}"
PREV="$(oc get deploy ai-code-review -n "${NS}" \
  -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="SCANNER_BACKEND")].value}' 2>/dev/null)"
oc set env deploy/ai-code-review -n "${NS}" SCANNER_BACKEND="${BACKEND}" >/dev/null
oc rollout status deploy/ai-code-review -n "${NS}" --timeout=180s >/dev/null
restore() {
  info "restoring SCANNER_BACKEND"
  if [[ -n "${PREV}" ]]; then
    oc set env deploy/ai-code-review -n "${NS}" SCANNER_BACKEND="${PREV}" >/dev/null
  else
    oc set env deploy/ai-code-review -n "${NS}" SCANNER_BACKEND- >/dev/null
  fi
  oc rollout status deploy/ai-code-review -n "${NS}" --timeout=180s >/dev/null || true
}
trap restore EXIT

SCANNER="https://$(oc get route ai-code-review -n "${NS}" -o jsonpath='{.spec.host}')"
info "scanner: ${SCANNER}"

capture() {
  local label="$1" diff_file="$2" source_file="$3" outfile="$4" priority="$5" marker="$6"

  step "Capturing: ${label}"
  info "this is live CPU inference - expect 30-90 seconds"

  PAYLOAD="$(python3 - "$diff_file" "$source_file" <<'PY'
import json, sys
diff = open(sys.argv[1]).read()
src  = open(sys.argv[2]).read()
print(json.dumps({
    "repository": "http://gitea.gitea.svc.cluster.local:3000/demo/accounts-api.git",
    "commit": "capture", "base_commit": "capture-base",
    "diff": diff,
    "files": [{"path": "src/main/java/com/redhat/demo/accounts/AccountResource.java",
               "content": src}],
}))
PY
)"

  RESULT="$(curl -sk --max-time 1200 -X POST "${SCANNER}/review" \
    -H 'Content-Type: application/json' -d "${PAYLOAD}")"

  VERDICT="$(jq -r '.verdict' <<< "${RESULT}")"
  if [[ "${VERDICT}" == "error" ]]; then
    warn "capture returned an error: $(jq -r '.error' <<< "${RESULT}")"
    return 1
  fi
  info "verdict=${VERDICT} findings=$(jq -r '.findings|length' <<< "${RESULT}") latency=$(jq -r '.latency_ms' <<< "${RESULT}")ms"

  python3 - "$diff_file" "$outfile" "$label" "$priority" "$marker" <<PY
import hashlib, json, sys

diff_file, outfile, label, priority, marker = sys.argv[1:6]
result = json.loads('''${RESULT}''')

def fingerprint(diff):
    lines = [l.rstrip() for l in diff.splitlines()
             if not l.startswith(("index ", "@@", "diff --git", "--- ", "+++ ")) and l.rstrip()]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()

entry = {
    "_comment": ["PROVENANCE: captured from the live model by scripts/capture-replay.sh."],
    "provenance": "captured",
    "label": label,
    "capturedModel": result.get("model"),
    "capturedBackend": result.get("backend"),
    "fingerprint": fingerprint(open(diff_file).read()),
    "match": {"priority": int(priority), "contains_all": [marker]},
    "latency_ms": result.get("latency_ms", 0),
    "usage": result.get("usage", {}),
    "response": {
        "summary": result.get("summary", ""),
        "findings": result.get("findings", []),
    },
}
json.dump(entry, open(outfile, "w"), indent=2)
print(f"     wrote {outfile}")
PY
}

capture "RHONE-4412 introduces the statements endpoint (vulnerable)" \
  "${ROOT}/components/vuln-demo-app/.demo/01-introduce-statements-endpoint.diff" \
  "${ROOT}/components/vuln-demo-app/src/main/java/com/redhat/demo/accounts/AccountResource.java" \
  "${REPLAY}/0001-statements-endpoint-vulnerable.json" 10 \
  'ownership mismatch: subject=%s account=%s owner=%s' || warn "capture 1 failed"

capture "fix/ownership-check (clean)" \
  "${ROOT}/components/vuln-demo-app/.demo/02-fix-ownership-check.diff" \
  "${ROOT}/components/vuln-demo-app/.demo/AccountResource.fixed.java" \
  "${REPLAY}/0002-ownership-check-fixed.json" 20 \
  '+                return Response.status(Response.Status.FORBIDDEN).build();' || warn "capture 2 failed"

step "Review the captures"
cat <<'MSG'
     The model is an 8B running quantised on CPU. Read what it actually said
     before you build a stage around it:

       jq -r '.response.findings[] | "\(.severity) \(.cwe) \(.title)"' \
         components/ai-code-review/replay/*.json

     If the critical finding is missing or wrong, that is useful information,
     not a bug to paper over. Options, in order of honesty:
       1. Raise MODEL_MAX_TOKENS - reasoning gets truncated and the model
          never reaches its conclusion. This is the usual cause.
       2. Use a less aggressive quant (Q5_K_M or Q8_0) if you have the RAM.
       3. Move to GPU and the bf16 model via the vLLM runtime.
       4. Keep the hand-authored seed and say on stage that the finding shown
          is the expected answer, with live inference demonstrated separately.

     Then commit the captures so the demo is reproducible:
       git add components/ai-code-review/replay && git commit -m "Capture replay entries"
       ./scripts/06-build-scanner.sh    # bake them into the image
MSG
