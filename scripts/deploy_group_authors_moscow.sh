#!/usr/bin/env bash
# Deploy group-authors Moscow release to /opt/bot3/parser-new-bot
set -euo pipefail

cd /opt/bot3/parser-new-bot

RELEASE_SHA="${RELEASE_SHA:-736e1d4e0dac73fd63b09f041aa11002d874a17d}"
ROLLBACK_SHA="$(git rev-parse HEAD)"

echo "=== precheck ==="
hostname -I || true
systemctl is-active parser-news-bot || true
echo "RELEASE_SHA=$RELEASE_SHA"
echo "ROLLBACK_SHA=$ROLLBACK_SHA"

git fetch origin
git cat-file -t "$RELEASE_SHA"

if [[ ! -f .env ]]; then
  echo "WARN: .env missing in cwd (may live elsewhere / systemd EnvironmentFile)"
else
  echo "env_present"
fi

echo "=== stop & checkout ==="
systemctl stop parser-news-bot
git checkout --force "$RELEASE_SHA"
CURRENT="$(git rev-parse HEAD)"
echo "$CURRENT" | tee /tmp/parsernews-deployed-sha.txt
if [[ "$CURRENT" != "$RELEASE_SHA" ]]; then
  echo "SHA_MISMATCH got=$CURRENT want=$RELEASE_SHA"
  git checkout --force "$ROLLBACK_SHA" || true
  systemctl start parser-news-bot || true
  exit 1
fi

echo "=== deps & tests ==="
# shellcheck disable=SC1091
source venv/bin/activate
pip install -r requirements.txt
if ! pytest tests/test_group_authors_contract.py tests/test_group_authors_collect.py \
  tests/test_channel_commenters_contract.py -q --tb=line; then
  echo "tests_failed_rollback"
  git checkout --force "$ROLLBACK_SHA"
  systemctl start parser-news-bot
  exit 1
fi

echo "=== env flags (idempotent) ==="
if [[ -f .env ]]; then
  if ! grep -q '^GROUP_AUTHORS_COLLECT_ENABLED=' .env; then
    echo 'GROUP_AUTHORS_COLLECT_ENABLED=true' >> .env
    echo "appended GROUP_AUTHORS_COLLECT_ENABLED=true"
  else
    echo "GROUP_AUTHORS_COLLECT_ENABLED already set"
  fi
fi

echo "=== start ==="
systemctl start parser-news-bot
sleep 2
systemctl is-active parser-news-bot
systemctl status parser-news-bot --no-pager | head -n 20
git rev-parse HEAD
echo "ROLLBACK_SHA was $ROLLBACK_SHA (save for emergency)"
echo "=== deploy done ==="
