#!/usr/bin/env bash
# Compile and behaviour-test the Argo CD resource health checks.
#
# These are Lua embedded in YAML, which is the least verifiable-looking thing
# in this repo, and they are load-bearing: if the Subscription check is broken
# or absent, a Subscription reads Healthy the instant it exists, sync wave -5
# finishes before any CRD is registered, and wave 0 fails on missing kinds.
# A typo here costs an hour of cluster debugging and looks like an operator bug.
#
# Extracts each check from the parsed YAML - so it tests exactly what the
# cluster receives - then compiles it and asserts behaviour for the states that
# matter, including a channel that does not exist on the cluster.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FILE="${ROOT}/gitops/bootstrap/04-argocd-subscription-health.yaml"
WORK="$(mktemp -d)"; trap 'rm -rf "${WORK}"' EXIT

# Override with PYTHON=... if your default python3 lacks PyYAML.
PYTHON="${PYTHON:-python3}"

if ! command -v lua >/dev/null 2>&1; then
  echo "SKIP: no lua interpreter (brew install lua / apt-get install -y lua5.4)"
  echo "      the checks are then unverified - CI installs lua so it does run there"
  exit 0
fi
if ! "${PYTHON}" -c 'import yaml' 2>/dev/null; then
  echo "SKIP: ${PYTHON} has no PyYAML (pip install pyyaml, or set PYTHON=...)"
  echo "      the checks are then unverified - CI has it so it does run there"
  exit 0
fi

"${PYTHON}" - "$FILE" "$WORK" <<'PY'
import sys, yaml, pathlib
doc = yaml.safe_load(open(sys.argv[1]))
assert doc["kind"] == "ArgoCD", "patch target is no longer the ArgoCD CR"
checks = doc["spec"]["resourceHealthChecks"]
out = pathlib.Path(sys.argv[2])
for c in checks:
    (out / f"{c['kind']}.lua").write_text(c["check"])
expected = {"Subscription", "MultiClusterHub", "DataScienceCluster"}
got = {c["kind"] for c in checks}
missing = expected - got
assert not missing, f"health checks disappeared: {sorted(missing)}"
print(f"  extracted {len(checks)}: {', '.join(sorted(got))}")
PY

fail=0
for f in "${WORK}"/*.lua; do
  kind="$(basename "${f}" .lua)"
  if lua -e "local c,e = loadfile('${f}'); if not c then io.stderr:write(e) ; os.exit(1) end"; then
    echo "  ok      ${kind} compiles"
  else
    echo "  FAIL    ${kind} does not compile"; fail=1
  fi
done

cat > "${WORK}/run.lua" <<'LUA'
local path, cases = ...
local function run(file, obj)
  local src = io.open(file):read("a")
  local env = { obj = obj, ipairs = ipairs, pairs = pairs, string = string }
  return load(src, "check", "t", env)()
end

local T = {
  { "Subscription", "no status yet", {}, "Progressing" },
  { "Subscription", "channel absent from this cluster's catalog",
    { status = { conditions = { { type = "ResolutionFailed", status = "True",
      message = "no channel named release-2.15 in package advanced-cluster-management" } } } },
    "Degraded" },
  { "Subscription", "catalog flapping during bring-up",
    { status = { conditions = { { type = "CatalogSourcesUnhealthy", status = "True",
      message = "not ready" } } } }, "Progressing" },
  { "Subscription", "operator installed",
    { status = { installedCSV = "advanced-cluster-management.v2.17.0",
      state = "AtLatestKnown", conditions = {} } }, "Healthy" },
  { "Subscription", "still installing",
    { status = { currentCSV = "rhods-operator.v2.99.0", conditions = {} } }, "Progressing" },
  { "MultiClusterHub", "no status yet", {}, "Progressing" },
  { "MultiClusterHub", "hub running",
    { status = { phase = "Running", currentVersion = "2.17.0" } }, "Healthy" },
  { "MultiClusterHub", "hub installing",
    { status = { phase = "Installing" } }, "Progressing" },
  { "MultiClusterHub", "hub failed",
    { status = { phase = "Error" } }, "Degraded" },
  -- FAIL OPEN. A phase name this script does not know must not stall the
  -- bootstrap forever; the gate is an optimisation, a deadlock is a
  -- regression on the retry-based convergence it replaced.
  { "MultiClusterHub", "unknown phase -> fails open",
    { status = { phase = "Reconciling" } }, "Healthy" },
  { "MultiClusterHub", "status but no phase -> fails open",
    { status = { currentVersion = "2.17.0" } }, "Healthy" },

  { "DataScienceCluster", "no status yet", {}, "Progressing" },
  { "DataScienceCluster", "phase Ready",
    { status = { phase = "Ready" } }, "Healthy" },
  { "DataScienceCluster", "still reconciling",
    { status = { phase = "Progressing", conditions = {} } }, "Progressing" },
  { "DataScienceCluster", "phase Error",
    { status = { phase = "Error" } }, "Degraded" },
  { "DataScienceCluster", "ReconcileComplete=False under an odd phase",
    { status = { phase = "Whatever", conditions = {
      { type = "ReconcileComplete", status = "False", message = "kserve failed" } } } },
    "Degraded" },
  -- The component conditions are named individually and vary by RHOAI
  -- version, so an unfamiliar shape must fail open rather than hang.
  { "DataScienceCluster", "only component conditions -> fails open",
    { status = { conditions = {
      { type = "ModelControllerReady", status = "True" },
      { type = "KserveReady", status = "True" } } } }, "Healthy" },
}

local bad = 0
for _, t in ipairs(T) do
  local kind, name, obj, want = t[1], t[2], t[3], t[4]
  local hs = run(path .. "/" .. kind .. ".lua", obj)
  local ok = hs.status == want
  if not ok then bad = bad + 1 end
  print(string.format("  %-7s %-18s %-42s -> %-11s %s",
    ok and "ok" or "FAIL", kind, name, hs.status, hs.message or ""))
end
os.exit(bad == 0 and 0 or 1)
LUA

echo
lua "${WORK}/run.lua" "${WORK}" || fail=1
echo
if (( fail )); then
  echo "FAIL: the health checks do not behave as documented"
  exit 1
fi
echo "all health checks compile and behave as documented"
