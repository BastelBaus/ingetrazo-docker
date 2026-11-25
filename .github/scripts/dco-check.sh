#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Check that every commit in BASE..HEAD (merges aside) carries a DCO
# sign-off (see CONTRIBUTING.md):
#
#   .github/scripts/dco-check.sh <base> <head>
#
# A commit that is already in a shared branch and cannot be rewritten is
# signed off afterwards with a remediation commit in the same range: a
# commit, signed off itself, with one line per commit:
#
#   I, Jane Doe <jane@example.com>, hereby add my Signed-off-by to this commit: <sha>
#
# The line counts only if the remediation commit carries the sign-off of
# the same "Name <email>". <sha> is the full hash or at least 7 characters.
set -euo pipefail

base=${1:?base commit}
head=${2:?head commit}
signoff='^Signed-off-by: (.+ <[^>]+@[^>]+>)$'
remedy='^I, (.+ <[^>]+@[^>]+>), hereby add my Signed-off-by to this commit: ([0-9a-f]{7,40})[[:space:]]*$'

commits=$(git rev-list --no-merges "$base..$head")

# The commits a remediation commit in the range signs off.
declare -A remedied=()
for c in $commits; do
  body=$(git log -1 --format=%B "$c")
  signers=$(grep -E "$signoff" <<<"$body" | sed -E "s/$signoff/\1/" || true)
  [ -n "$signers" ] || continue
  while IFS= read -r line; do
    [[ $line =~ $remedy ]] || continue
    who=${BASH_REMATCH[1]} sha=${BASH_REMATCH[2]}
    if ! grep -qxF "$who" <<<"$signers"; then
      echo "::warning::$(git log -1 --format=%h "$c") remedies $sha for $who," \
           "but is not signed off by $who"
      continue
    fi
    if full=$(git rev-parse -q --verify "$sha^{commit}"); then
      remedied[$full]=$(git log -1 --format=%h "$c")
    else
      echo "::warning::$(git log -1 --format=%h "$c") remedies $sha: no such commit"
    fi
  done <<<"$body"
done

missing=0
for c in $commits; do
  if git log -1 --format=%B "$c" | grep -qE "$signoff"; then
    continue
  fi
  if [ -n "${remedied[$c]:-}" ]; then
    echo "$(git log -1 --format='%h %s' "$c"): signed off by remediation ${remedied[$c]}"
    continue
  fi
  echo "::error::Commit $(git log -1 --format='%h %s' "$c") has no Signed-off-by line"
  missing=1
done
if [ "$missing" -ne 0 ]; then
  echo "Sign off with 'git commit -s', or add a remediation commit (see CONTRIBUTING.md)."
  exit 1
fi
echo "All commits are signed off."
