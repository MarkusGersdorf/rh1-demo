# Antares fixture provenance

These are **real recorded outputs** from the Antares CLI, not hand-written
test data. The parser in `app/localizer.py` tracks a report format this
project does not control, so the fixtures have to be genuine upstream output
or the tests prove nothing.

| | |
|---|---|
| Source | [sroecker/supreme-couscous](https://github.com/sroecker/supreme-couscous) — *Antares local POC* by Steffen Röcker |
| Licence | MIT, Copyright (c) 2026 Steffen Röcker |
| Upstream path | `reports/cpu-sweep/` |
| Upstream commit | `0b6b7a0dce3ed46c9838ebfe58cf7fd5ddf079e0` |
| Commit date | 2026-09-14 |
| Commit subject | Fix retained scan freshness and F16 CPU startup for 0.4.7 |
| Copied | 2026-10-20 |

## What this run was

A `cwe_sweep` of a single-file Python demo, on CPU, with `antares-1b`:

- 3 findings, all in `app.py` — CWE-20, CWE-78, CWE-79
- **167.6 s**, 47 tool calls, 7 of them failed
- 3 of 944 CWE classes examined (`truncated: true`)
- `temperature: 0.3`, `remote_provider_may_vary: true`

Those numbers are load-bearing in the tests: they are why Stage A is advisory,
why its coverage is recorded in the attestation, and why the predicate must not
claim it is reproducible.

## Deliberately frozen

This is a pinned copy, **not** a submodule or subtree. Fixtures should change
when we decide to absorb a format change, not when upstream commits something
unrelated — otherwise an upstream push breaks CI here for no reason we chose.

Refresh it on purpose:

```sh
./scripts/refresh-antares-fixture.sh
```

That rewrites these files from upstream `main`, updates the commit fields
above, and then you run `pytest tests/test_localizer.py` to see what the format
change actually broke. A failure at that point is the fixture doing its job.
