#!/usr/bin/env bash
# Daily local AI review (run in WSL):  bash scripts/daily_audit.sh  [--limit N] [--dry-run] [--all]
#
#   1. pulls the latest jobs the GitHub bot found
#   2. starts Qwen3.8-27B with HyperQwen (if it isn't already running) and waits until it's ready
#   3. reviews every new job against your resumes → data/ai_review.json   (python -m tracker.audit)
#   4. commits + pushes only data/ai_review.json, so the dashboard shows the AI column
#   5. stops Qwen again to free your GPU (set KEEP_QWEN=1 to leave it running)
#
# Settings (optional) go in .env.local next to this repo's README (never committed):
#   HYPERQWEN_DIR=$HOME/HyperQwen      where you cloned github.com/syv-ai/HyperQwen
#   VLLM_API_KEY=...                   same key as in HyperQwen's .env (if you set one)
#   TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... DASHBOARD_URL=...   for the "best matches" message
#   PYTHON=python                      which python to use (e.g. your conda env's)
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
if [ -f .env.local ]; then set -a; . ./.env.local; set +a; fi
HYPERQWEN_DIR="${HYPERQWEN_DIR:-$HOME/HyperQwen}"
PY="${PYTHON:-python}"
PORT="${PORT:-18020}"

echo "── 1/5 pulling the latest jobs"
git pull --rebase --autostash -q

started=0
if curl -sf "http://localhost:${PORT}/health" >/dev/null 2>&1; then
  echo "── 2/5 Qwen is already running on :${PORT}"
else
  echo "── 2/5 starting Qwen (HyperQwen batch mode) from ${HYPERQWEN_DIR}"
  if [ ! -f "${HYPERQWEN_DIR}/docker-compose.yml" ]; then
    echo "   HyperQwen not found at ${HYPERQWEN_DIR}. Clone it first (see README → Local AI review)."; exit 1
  fi
  (cd "$HYPERQWEN_DIR" && docker compose --profile batch up -d)
  started=1
  echo "   waiting for the model to load (first start ever: 15-30 min while it downloads and converts the model)…"
  for i in $(seq 1 360); do
    curl -sf "http://localhost:${PORT}/health" >/dev/null 2>&1 && break
    sleep 10
    if [ "$i" = 360 ]; then echo "   Qwen didn't come up in 60 min; check: cd $HYPERQWEN_DIR && docker compose logs --tail 50"; exit 1; fi
  done
fi

stop_qwen() {
  if [ "$started" = 1 ] && [ -z "${KEEP_QWEN:-}" ]; then
    echo "── 5/5 stopping Qwen (frees the GPU)"
    (cd "$HYPERQWEN_DIR" && docker compose --profile batch down) >/dev/null 2>&1 || true
  fi
}
trap stop_qwen EXIT

echo "── 3/5 reviewing new jobs"
"$PY" -m tracker.audit "$@"

case " $* " in *" --dry-run "*) echo "(dry run: nothing committed)"; exit 0;; esac
echo "── 4/5 saving the reviews to GitHub"
git add data/ai_review.json
if git diff --staged --quiet; then
  echo "   no new reviews"
else
  git commit -q -m "ai review: $(date -u +'%Y-%m-%d %H:%M UTC')"
  for i in 1 2 3; do git pull --rebase --autostash -q && git push -q && break; sleep 5; done
  echo "   pushed data/ai_review.json"
fi
