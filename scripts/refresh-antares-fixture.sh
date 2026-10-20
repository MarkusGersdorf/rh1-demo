#!/usr/bin/env bash
# Refresh the pinned Antares test fixtures from upstream.
#
# The fixtures are a deliberately frozen copy of real recorded output from
# sroecker/supreme-couscous (MIT). They are NOT a submodule: the parser tracks
# a format we do not control, and we want to choose when to absorb a change
# rather than have an unrelated upstream commit break CI here.
#
# Run this when you want to pick up a format change, then run the tests and
# read the failures.
source "$(dirname "$0")/lib.sh"
need git
need curl

ROOT="$(repo_root)"
FIXTURES="${ROOT}/components/ai-code-review/tests/fixtures/antares"
UPSTREAM="${ANTARES_UPSTREAM:-https://github.com/sroecker/supreme-couscous.git}"
UPSTREAM_PATH="reports/cpu-sweep"

step "Fetching upstream"
TMP="$(mktemp -d)"; trap 'rm -rf "${TMP}"' EXIT
git clone --quiet --depth 1 --filter=blob:none --sparse "${UPSTREAM}" "${TMP}/repo" \
  || die "could not clone ${UPSTREAM}"
git -C "${TMP}/repo" sparse-checkout set "${UPSTREAM_PATH}" >/dev/null

SHA="$(git -C "${TMP}/repo" rev-parse HEAD)"
# UTC, not local time. A commit at 22:22 UTC is "tomorrow" in Berlin, so a
# local-time date makes two people refreshing flip this field back and forth.
DATE="$(TZ=UTC0 git -C "${TMP}/repo" log -1 --format=%cd --date=format-local:%F)"
SUBJECT="$(git -C "${TMP}/repo" log -1 --format=%s)"
info "upstream ${SHA:0:12} (${DATE}) - ${SUBJECT}"

SRC="${TMP}/repo/${UPSTREAM_PATH}"
[[ -d "${SRC}" ]] || die "upstream no longer has ${UPSTREAM_PATH} - the fixture needs rethinking, not refreshing"

step "Updating fixtures"
CHANGED=0
for f in report.json report.sarif report.md; do
  if [[ ! -f "${SRC}/${f}" ]]; then
    warn "${f} is gone upstream"
    continue
  fi
  if ! cmp -s "${SRC}/${f}" "${FIXTURES}/cpu-sweep/${f}"; then
    cp "${SRC}/${f}" "${FIXTURES}/cpu-sweep/${f}"
    echo "   updated ${f}"
    CHANGED=1
  else
    echo "   unchanged ${f}"
  fi
done

step "Updating PROVENANCE.md"
perl -pi -e "s{^\\| Upstream commit \\| \`[0-9a-f]+\` \\|\$}{| Upstream commit | \`${SHA}\` |}" "${FIXTURES}/PROVENANCE.md"
perl -pi -e "s{^\\| Commit date \\| .* \\|\$}{| Commit date | ${DATE} |}" "${FIXTURES}/PROVENANCE.md"
perl -pi -e "s{^\\| Copied \\| .* \\|\$}{| Copied | $(date -u +%F) |}" "${FIXTURES}/PROVENANCE.md"
ok "provenance updated to ${SHA:0:12}"

if (( CHANGED )); then
  cat <<'MSG'

     Fixture content changed. Now run the tests and read the failures - that is
     the point of pinning:

       cd components/ai-code-review && pytest tests/test_localizer.py

     If the report format moved, app/localizer.py needs updating, and the
     numbers quoted in PROVENANCE.md and docs/SETUP.md may need refreshing too.
MSG
else
  ok "no content change - only the provenance stamp moved"
fi
