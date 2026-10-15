#!/usr/bin/env bash
# Start a pipeline run by hand, for when you want to re-run without pushing.
#   ./scripts/run-pipeline.sh [revision]
source "$(dirname "$0")/lib.sh"
require_cluster
NS=lightwell-inward
REV="${1:-main}"

GITEA_SVC="http://gitea.gitea.svc.cluster.local:3000/demo/accounts-api.git"

NAME="$(oc create -n "${NS}" -o name -f - <<YAML
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  generateName: lightwell-inward-
spec:
  pipelineRef:
    name: lightwell-inward
  taskRunTemplate:
    serviceAccountName: pipeline-runner
  timeouts:
    pipeline: 1h0m0s
  params:
    - name: repo-url
      value: ${GITEA_SVC}
    - name: revision
      value: ${REV}
    - name: image
      value: image-registry.openshift-image-registry.svc:5000/demo-accounts/accounts-api
  workspaces:
    - name: source
      volumeClaimTemplate:
        spec:
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 5Gi
YAML
)"
ok "started ${NAME}"
info "following logs (ctrl-c is safe, the run continues)"
command -v tkn >/dev/null 2>&1 \
  && tkn pipelinerun logs -f -n "${NS}" "${NAME#pipelinerun.tekton.dev/}" \
  || oc logs -f -n "${NS}" -l "tekton.dev/pipelineRun=${NAME#pipelinerun.tekton.dev/}" --all-containers --max-log-requests 10
