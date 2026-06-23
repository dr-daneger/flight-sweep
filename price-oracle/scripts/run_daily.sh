#!/usr/bin/env bash
# buy-signal -- daily price-oracle run + commit/push.
#
# Invoked by Windows Task Scheduler via Git bash. Generates today's BUY/WAIT/WATCH
# snapshot from the live spine (Samsung + Abt + Slickdeals + eBay-when-keyed) and
# commits price-oracle/data + price-oracle/docs. Skip-if-clean so a no-change day
# does not spam history. All output is logged under %LOCALAPPDATA%\buy-signal\logs.
#
# Runs from the existing dev clone, so it is GUARDED: if the clone is checked out
# on another branch it no-ops, rather than switch branches under your feet.
set -uo pipefail

REPO="/c/Users/Dane/Documents/Local Repo/flight-sweep"
VENV_PY="/c/Users/Dane/AppData/Local/buy-signal/venv/Scripts/python.exe"
KEYS="/c/Users/Dane/AppData/Local/buy-signal/keys.env"   # optional EBAY_CLIENT_ID/SECRET
LOGDIR="/c/Users/Dane/AppData/Local/buy-signal/logs"
BRANCH="claude/flight-sweep-pricing-engine-wl2cyt"

mkdir -p "$LOGDIR"
TS="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
exec >>"$LOGDIR/run-$TS.log" 2>&1
echo "=== buy-signal daily run $TS ==="

export PYTHONUTF8=1                       # clean UTF-8 logs (avoid cp1252 mangling)
if [ -f "$KEYS" ]; then set -a; . "$KEYS"; set +a; echo "loaded keys.env"; fi

cd "$REPO" || { echo "FATAL: repo missing"; exit 1; }
CUR="$(git rev-parse --abbrev-ref HEAD)"
if [ "$CUR" != "$BRANCH" ]; then
  echo "SKIP: clone is on '$CUR', not '$BRANCH' -- left it alone."
  exit 0
fi

cd "$REPO/price-oracle" || exit 1
"$VENV_PY" -m oracle.run --source auto
echo "oracle.run rc=$?"

cd "$REPO" || exit 1
git add price-oracle/data price-oracle/docs
if git diff --cached --quiet; then
  echo "no changes; skip commit"
else
  git commit -m "price-oracle: $(date -u +%F) BUY/WAIT/WATCH snapshot"
  if git push origin "$BRANCH"; then echo "pushed"; else echo "PUSH FAILED"; fi
fi
echo "=== done $(date -u +%Y-%m-%dT%H-%M-%SZ) ==="
