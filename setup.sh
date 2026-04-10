#!/usr/bin/env bash
# このスクリプトはCM4上のOrion_CM4初期セットアップを担当し、依存導入、ビルド、systemd登録をまとめて実行する。
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="control_server.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"

APT_PACKAGES=(
  git
  libboost-all-dev
  raspberrypi-kernel-headers
  dkms
  pkg-config
  rsync
  gtkterm
  build-essential
  bc
  python3
  python3-dev
  python3-pip
)

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

install_apt_packages() {
  log "APTパッケージを更新・導入します"
  run_sudo apt update
  if [[ "${SKIP_APT_UPGRADE:-0}" != "1" ]]; then
    run_sudo apt upgrade -y
  fi
  run_sudo apt install -y "${APT_PACKAGES[@]}"
  run_sudo apt autoremove -y
}

install_python_packages() {
  log "Python依存を導入します"
  python3 -m pip install --user --break-system-packages -e "${REPO_DIR}"
}

build_cpp_binaries() {
  log "C++バイナリをビルドします"
  g++ "${REPO_DIR}/forward_robot_feedback.cpp" -pthread -o "${REPO_DIR}/robot_feedback.out"
  g++ "${REPO_DIR}/forward_ai_cmd_v2.cpp" -pthread -o "${REPO_DIR}/ai_cmd_v2.out"
  chmod +x "${REPO_DIR}/robot_feedback.out" "${REPO_DIR}/ai_cmd_v2.out"
}

build_camera_server() {
  if [[ "${SKIP_CAMERA_BUILD:-0}" == "1" ]]; then
    log "SKIP_CAMERA_BUILD=1 のためカメラサーバーのビルドをスキップします"
    chmod +x "${REPO_DIR}/cm4_cam/dist/cam_server_v3"
    return
  fi

  log "カメラサーバーをPyInstallerでビルドします"
  python3 -m pip install --user --break-system-packages pyinstaller
  (
    cd "${REPO_DIR}/cm4_cam"
    python3 -m PyInstaller --clean --distpath dist --workpath build cam_server_v3.spec
  )
  chmod +x "${REPO_DIR}/cm4_cam/dist/cam_server_v3"
}

install_systemd_service() {
  log "systemdサービスを登録します"
  run_sudo cp "${REPO_DIR}/${SERVICE_NAME}" "${SERVICE_PATH}"
  run_sudo systemctl daemon-reload
  run_sudo systemctl enable "${SERVICE_NAME}"
  run_sudo systemctl restart "${SERVICE_NAME}"
}

main() {
  if [[ "${EUID}" -eq 0 ]]; then
    echo "setup.sh は sudo なしで実行してください。sudo が必要な処理はスクリプト内で実行します。" >&2
    exit 1
  fi

  if [[ "$(id -un)" != "ibis" ]]; then
    echo "警告: control_server.service は User=ibis を前提にしています。現在のユーザーは $(id -un) です。" >&2
  fi

  cd "${REPO_DIR}"

  install_apt_packages
  install_python_packages
  build_cpp_binaries
  build_camera_server
  install_systemd_service

  log "セットアップが完了しました"
  run_sudo systemctl --no-pager status "${SERVICE_NAME}" || true
}

main "$@"
