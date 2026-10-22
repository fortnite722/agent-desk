#!/usr/bin/env bash
# Commit bot output (state/, docs/, lab_out/, cloud config) and push, retrying around other bots' pushes.
set -u
cd "$(dirname "$0")/.."
git config user.name "desk-bot"
git config user.email "desk-bot@users.noreply.github.com"
git add -A state docs lab_out cloud/config_crypto.json 2>/dev/null
git diff --cached --quiet && exit 0
git commit -q -m "${1:-bot update}"
for i in 1 2 3 4 5 6; do
  git pull -q --rebase -X theirs origin main && git push -q origin HEAD:main && exit 0
  sleep $((i * 5))
done
echo "push failed after retries" >&2
exit 1
