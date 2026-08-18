#!/usr/bin/env bash
# このスクリプトは cm4-fleet deploy から呼び出される軽量更新を担当する。
# cm4/setup.sh と異なり、apt/pip install は一切行わない
# (ロボット用 LAN はインターネットに到達できない前提のため)。
set -euo pipefail

CM4_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="control_server.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
BRIDGE_DIR="${CM4_DIR}/bridge"
BIN_DIR="${CM4_DIR}/bin"
CAMERA_DIR="${CM4_DIR}/camera"

REBUILD_CAMERA=0
for arg in "$@"; do
  case "${arg}" in
    --rebuild-camera) REBUILD_CAMERA=1 ;;
    *)
      echo "不明なオプションです: ${arg}" >&2
      exit 1
      ;;
  esac
done

log() {
  printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

run_sudo() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

build_cpp_binaries() {
  log "C++ ブリッジを再ビルドします"
  mkdir -p "${BIN_DIR}"
  g++ "${BRIDGE_DIR}/forward_robot_feedback.cpp" -pthread -o "${BIN_DIR}/robot_feedback.out"
  g++ "${BRIDGE_DIR}/forward_ai_cmd_v2.cpp" -pthread -o "${BIN_DIR}/ai_cmd_v2.out"
  chmod +x "${BIN_DIR}/robot_feedback.out" "${BIN_DIR}/ai_cmd_v2.out"
}

build_camera_server() {
  if [[ "${REBUILD_CAMERA}" != "1" ]]; then
    log "カメラサーバーの再ビルドをスキップします(--rebuild-camera 指定時のみ実行)"
    return
  fi

  log "カメラサーバーを PyInstaller でビルドします(ネットワーク到達が必要です。cm4-fleet deploy 経由の場合は http_proxy/https_proxy が自動設定されます)"
  python3 -m pip install --user --break-system-packages pyinstaller
  (
    cd "${CAMERA_DIR}"
    python3 -m PyInstaller --clean --distpath dist --workpath build cam_server_v3.spec
  )
  chmod +x "${CAMERA_DIR}/dist/cam_server_v3"
}

restart_service() {
  # lancher.py が subprocess.Popen で起動する制御プロセスは systemd の
  # デフォルト KillMode=control-group によりサービスの cgroup ごと回収される。
  log "systemd サービスを再配置・再起動します"
  run_sudo cp "${CM4_DIR}/${SERVICE_NAME}" "${SERVICE_PATH}"
  run_sudo systemctl daemon-reload
  run_sudo systemctl enable "${SERVICE_NAME}"
  run_sudo systemctl restart "${SERVICE_NAME}"
}

main() {
  if [[ "${EUID}" -eq 0 ]]; then
    echo "cm4/update.sh は sudo なしで実行してください。sudo が必要な処理はスクリプト内で個別に実行します。" >&2
    exit 1
  fi

  build_cpp_binaries
  build_camera_server
  restart_service

  log "更新が完了しました"
  run_sudo systemctl --no-pager status "${SERVICE_NAME}" || true
}

main "$@"
