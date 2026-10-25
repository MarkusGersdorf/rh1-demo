#!/usr/bin/env python3
"""Check that the Tekton manifests in gitops/components/pipelines wire up.

Argo CD will happily deploy a Pipeline whose taskRef points at nothing, whose
params do not match the Task's, or whose workspaces are unbound. Tekton only
complains when a PipelineRun starts - which, for this repo, means on stage.

Checks every Pipeline and its finally block: taskRefs resolve; required params
are supplied and no undeclared ones are passed; declared workspaces are bound
to workspaces the Pipeline has; runAfter names real tasks; every
$(tasks.X.results.Y) resolves to a result X declares; every $(params.X) is a
Pipeline param; and the TriggerTemplate's PipelineRun matches its Pipeline.

Run directly, or via CI's manifests job.
"""

import yaml, glob, re, sys, collections

docs = []
for f in sorted(glob.glob("gitops/components/pipelines/*.yaml")):
    for d in yaml.safe_load_all(open(f)):
        if d: docs.append((f.split("/")[-1], d))

tasks = {d["metadata"]["name"]: d for _, d in docs if d.get("kind") == "Task"}
pipes = [d for _, d in docs if d.get("kind") == "Pipeline"]
errs, warns = [], []

for pl in pipes:
    spec = pl["spec"]
    pname = pl["metadata"]["name"]
    pl_params = {p["name"] for p in spec.get("params", [])}
    pl_ws = {w["name"] for w in spec.get("workspaces", [])}
    all_tasks = spec.get("tasks", []) + spec.get("finally", [])
    names = {t["name"] for t in all_tasks}

    for t in all_tasks:
        ref = (t.get("taskRef") or {}).get("name")
        tn = t["name"]
        if ref is None:
            warns.append(f"{pname}/{tn}: inline taskSpec, skipped")
            continue
        if ref not in tasks:
            errs.append(f"{pname}/{tn}: taskRef '{ref}' is not defined in this folder")
            continue
        td = tasks[ref]["spec"]

        # params the Task requires (no default) must be supplied
        required = {p["name"] for p in td.get("params", []) if "default" not in p}
        supplied = {p["name"] for p in t.get("params", [])}
        for miss in sorted(required - supplied):
            errs.append(f"{pname}/{tn}: Task '{ref}' requires param '{miss}', not supplied")
        declared = {p["name"] for p in td.get("params", [])}
        for extra in sorted(supplied - declared):
            errs.append(f"{pname}/{tn}: passes param '{extra}' that Task '{ref}' does not declare")

        # workspaces the Task declares must be bound, and bound to a pipeline ws
        tws = {w["name"] for w in td.get("workspaces", [])}
        bound = {w["name"]: w.get("workspace") for w in t.get("workspaces", [])}
        for miss in sorted(tws - set(bound)):
            errs.append(f"{pname}/{tn}: Task '{ref}' declares workspace '{miss}', not bound")
        for extra in sorted(set(bound) - tws):
            errs.append(f"{pname}/{tn}: binds workspace '{extra}' that Task '{ref}' does not declare")
        for wname, wsrc in bound.items():
            if wsrc not in pl_ws:
                errs.append(f"{pname}/{tn}: workspace '{wname}' -> '{wsrc}' which the Pipeline does not declare")

        for ra in t.get("runAfter", []):
            if ra not in names:
                errs.append(f"{pname}/{tn}: runAfter '{ra}' is not a task in this Pipeline")

    # $(tasks.X.results.Y) must resolve
    blob = yaml.dump(spec)
    for m in re.finditer(r"\$\(tasks\.([a-z0-9-]+)\.results\.([a-z0-9-]+)\)", blob):
        tn, rn = m.group(1), m.group(2)
        src = next((x for x in all_tasks if x["name"] == tn), None)
        if src is None:
            errs.append(f"{pname}: references results of unknown task '{tn}'")
            continue
        ref = (src.get("taskRef") or {}).get("name")
        if ref not in tasks: continue
        declared = {r["name"] for r in tasks[ref]["spec"].get("results", [])}
        if rn not in declared:
            errs.append(f"{pname}: $(tasks.{tn}.results.{rn}) but Task '{ref}' declares {sorted(declared) or 'no results'}")

    # $(params.X) at pipeline level must be declared
    for m in re.finditer(r"\$\(params\.([a-z0-9-]+)\)", blob):
        if m.group(1) not in pl_params:
            errs.append(f"{pname}: $(params.{m.group(1)}) is not a Pipeline param")

# TriggerTemplate -> Pipeline params/workspaces
for _, d in docs:
    if d.get("kind") != "TriggerTemplate": continue
    tt_params = {p["name"] for p in d["spec"].get("params", [])}
    for rt in d["spec"].get("resourcetemplates", []):
        if rt.get("kind") != "PipelineRun": continue
        pr = rt["spec"]
        target = pr.get("pipelineRef", {}).get("name")
        pl = next((p for p in pipes if p["metadata"]["name"] == target), None)
        if pl is None:
            errs.append(f"TriggerTemplate: pipelineRef '{target}' not found"); continue
        pl_params = {p["name"] for p in pl["spec"].get("params", [])}
        pl_ws = {w["name"] for w in pl["spec"].get("workspaces", [])}
        for p in pr.get("params", []):
            if p["name"] not in pl_params:
                errs.append(f"TriggerTemplate: passes param '{p['name']}' not on Pipeline '{target}'")
        bound = {w["name"] for w in pr.get("workspaces", [])}
        for miss in sorted(pl_ws - bound):
            errs.append(f"TriggerTemplate: Pipeline '{target}' workspace '{miss}' not bound in the PipelineRun")
        for m in re.finditer(r"\$\(tt\.params\.([a-z0-9-]+)\)", yaml.dump(rt)):
            if m.group(1) not in tt_params:
                errs.append(f"TriggerTemplate: $(tt.params.{m.group(1)}) not declared")

print(f"checked {len(tasks)} Tasks, {len(pipes)} Pipeline(s), "
      f"{len([1 for _,d in docs if d.get('kind')=='TriggerTemplate'])} TriggerTemplate(s)\n")
for w in warns: print(f"  note  {w}")
if errs:
    print(f"\n  {len(errs)} PROBLEM(S):")
    for e in errs: print(f"    - {e}")
else:
    print("  no wiring errors: every taskRef, param, workspace, result and runAfter resolves")
sys.exit(1 if errs else 0)
