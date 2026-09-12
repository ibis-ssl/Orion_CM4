#!/usr/bin/env bash
# このスクリプトは C++ バイナリのビルドとテスト実行を担当する。
#
# cm4/setup.sh (初期セットアップ) と cm4/update.sh (cm4-fleet deploy 経由の更新)
# の両方から呼ばれる唯一のビルド定義。以前は 2 箇所に同じ g++ 行が重複しており、
# 片方だけ更新すると実機に反映されない事故があった。
#
# sudo も apt も使わないので、ホスト PC (x86_64) でもそのまま実行できる。
# cm4_sim はシミュレータ用でホスト上で動かすため、これが必須要件。
set -euo pipefail

CM4_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRIDGE_DIR="${CM4_DIR}/bridge"
CONTROL_DIR="${CM4_DIR}/control"
BIN_DIR="${CM4_DIR}/bin"

# 実機は BCM2711 固定なので移植性は考慮しない (AGENTS.md)。
# -O2 と警告は付けるが、-march などチップ固有の指定はホストビルドを壊すので入れない。
CXXFLAGS=(-std=gnu++17 -O2 -Wall -Wextra -pthread)

RUN_TESTS=1
for arg in "$@"; do
  case "${arg}" in
    --no-tests) RUN_TESTS=0 ;;
    *)
      echo "不明なオプションです: ${arg}" >&2
      exit 1
      ;;
  esac
done

log() {
  printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

build_binaries() {
  log "C++ バイナリをビルドします (${BIN_DIR})"
  mkdir -p "${BIN_DIR}"

  # 実機ブリッジ (boost::asio で UART を扱うため CM4 上でのみ意味を持つが、
  # ビルド自体はホストでも通る)
  g++ "${CXXFLAGS[@]}" "${BRIDGE_DIR}/forward_robot_feedback.cpp" -o "${BIN_DIR}/robot_feedback.out"
  g++ "${CXXFLAGS[@]}" "${BRIDGE_DIR}/forward_ai_cmd_v2.cpp" -o "${BIN_DIR}/ai_cmd_v2.out"

  # パケットレイアウトのドリフト検査
  g++ "${CXXFLAGS[@]}" "${BRIDGE_DIR}/robot_packet_layout_test.cpp" -o "${BIN_DIR}/robot_packet_layout_test.out"

  # シミュレータ用の CM4 相当プロセス。
  # position_controller.cpp を実機ブリッジと同一ソースとしてリンクする
  # (コピーを作らないことが実機と sim の挙動一致の保証)。
  # boost に依存しないのでホスト PC (x86_64) でもそのままビルドできる。
  g++ "${CXXFLAGS[@]}" -I"${CONTROL_DIR}" "${BRIDGE_DIR}/cm4_sim.cpp" "${CONTROL_DIR}/position_controller.cpp" -o "${BIN_DIR}/cm4_sim.out"

  # 位置制御ライブラリの単体テスト。
  # position_controller.cpp は実機ブリッジとシミュレータ用バイナリの両方が
  # 同一ソースとしてリンクする (コピーを作らない) ため、ここでの検証が両方に効く。
  g++ "${CXXFLAGS[@]}" -I"${CONTROL_DIR}" "${CONTROL_DIR}/test_position_controller.cpp" "${CONTROL_DIR}/position_controller.cpp" -o "${BIN_DIR}/test_position_controller.out"

  chmod +x "${BIN_DIR}"/*.out
}

run_tests() {
  if [[ "${RUN_TESTS}" != "1" ]]; then
    log "--no-tests 指定のためテストをスキップします"
    return
  fi

  log "パケットレイアウト検査を実行します"
  "${BIN_DIR}/robot_packet_layout_test.out"

  log "位置制御ライブラリの単体テストを実行します"
  "${BIN_DIR}/test_position_controller.out"

  log "cm4_sim の結合スモークテストを実行します"
  (cd "${BRIDGE_DIR}" && python3 -m unittest test_cm4_sim)
}

main() {
  build_binaries
  run_tests
  log "ビルドが完了しました"
}

main "$@"
