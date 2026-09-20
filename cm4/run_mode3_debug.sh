#!/usr/bin/env bash
# このファイルはCM4内部の50 Hz停止指令生成と実UARTブリッジを監督する。
# 本番と別のUDPポートを使い、終了・子プロセス異常時は両方を停止する。
set -euo pipefail
CM4_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROBOT_ID="${1:-8}"
LOG_ROOT="${CM4_DIR}/runtime/mode3-debug"
bridge_pid=""
sender_pid=""
cleanup() {
  trap - EXIT INT TERM
  if [[ -n "${sender_pid}" ]]; then
    kill -TERM "${sender_pid}" 2>/dev/null || true
    wait "${sender_pid}" 2>/dev/null || true
  fi
  if [[ -n "${bridge_pid}" ]]; then
    kill -TERM "${bridge_pid}" 2>/dev/null || true
    wait "${bridge_pid}" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 0' INT TERM
if pgrep -x ai_cmd_v2.out >/dev/null; then
  echo 'ai_cmd_v2.outが稼働中です。通常制御を停止してから実行してください。' >&2
  exit 1
fi
mkdir -p "${LOG_ROOT}"
"${CM4_DIR}/bin/ai_cmd_v2.out" -s 1000000 --robot-id "${ROBOT_ID}" \
  --ai-cmd-port 12445 --local-cam-port 12446 --feedback-port 12447 --config-port 12448 &
bridge_pid=$!
sleep 1
kill -0 "${bridge_pid}"
while true; do
  run_dir="$(mktemp -d "${LOG_ROOT}/run-XXXXXXXX")"
  python3 -u "${CM4_DIR}/bridge/mode3_timing_probe.py" \
    --robot-id "${ROBOT_ID}" --port 12445 --rate-hz 50 --seconds 3600 \
    --output "${run_dir}/capture" &
  sender_pid=$!
  while kill -0 "${sender_pid}" 2>/dev/null; do
    if ! kill -0 "${bridge_pid}" 2>/dev/null; then
      echo 'ブリッジが終了したため診断を停止します。' >&2
      exit 1
    fi
    sleep 1
  done
  wait "${sender_pid}"
  sender_pid=""
done
