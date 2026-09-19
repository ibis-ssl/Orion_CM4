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
# ビルド対象。all = 実機用も含めた全 5 本、sim = cm4_sim.out に必要なものだけ。
# Dockerfile は sim を指定する (イメージに入るのは cm4_sim.out 1 本だけなので、
# 実機ブリッジ 2 本を毎回ビルドして捨てるのは無駄。boost も要らなくなる)。
TARGETS=all
for arg in "$@"; do
  case "${arg}" in
    --no-tests) RUN_TESTS=0 ;;
    --targets=all | --targets=sim) TARGETS="${arg#--targets=}" ;;
    *)
      echo "不明なオプションです: ${arg}" >&2
      echo "使い方: build.sh [--no-tests] [--targets=all|sim]" >&2
      exit 1
      ;;
  esac
done

if [[ "${TARGETS}" == "sim" && "${RUN_TESTS}" == "1" ]]; then
  echo "--targets=sim ではテストを実行できません (実機ブリッジのバイナリを使うため)。--no-tests を付けてください" >&2
  exit 1
fi

log() {
  printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

build_binaries() {
  log "C++ バイナリをビルドします (${BIN_DIR})"
  mkdir -p "${BIN_DIR}"

  # position_controller.cpp は ai_cmd_v2 / cm4_sim / 単体テストの 3 つがリンクする。
  # 実機とシミュレータで制御コードのコピーを作らないことが挙動一致の保証なので、
  # オブジェクトも 1 つにする (3 回コンパイルしても同じ物ができるだけ)。
  local control_obj="${BIN_DIR}/position_controller.o"
  g++ "${CXXFLAGS[@]}" -I"${CONTROL_DIR}" -c "${CONTROL_DIR}/position_controller.cpp" -o "${control_obj}"

  # 以下は互いに独立なので並列に投げる。CM4 (4 コア) でもホストでも効く。
  local pids=()

  # シミュレータ用の CM4 相当プロセス。
  # boost に依存しないのでホスト PC (x86_64) でもそのままビルドできる。
  g++ "${CXXFLAGS[@]}" -I"${CONTROL_DIR}" "${BRIDGE_DIR}/cm4_sim.cpp" "${control_obj}" -o "${BIN_DIR}/cm4_sim.out" &
  pids+=($!)

  if [[ "${TARGETS}" == "all" ]]; then
    # 実機ブリッジ (boost::asio で UART を扱うため CM4 上でのみ意味を持つが、
    # ビルド自体はホストでも通る)
    g++ "${CXXFLAGS[@]}" "${BRIDGE_DIR}/forward_robot_feedback.cpp" -o "${BIN_DIR}/robot_feedback.out" &
    pids+=($!)
    g++ "${CXXFLAGS[@]}" -I"${CONTROL_DIR}" "${BRIDGE_DIR}/forward_ai_cmd_v2.cpp" "${control_obj}" -o "${BIN_DIR}/ai_cmd_v2.out" &
    pids+=($!)

    # パケットレイアウトのドリフト検査
    g++ "${CXXFLAGS[@]}" -I"${CONTROL_DIR}" "${BRIDGE_DIR}/robot_packet_layout_test.cpp" -o "${BIN_DIR}/robot_packet_layout_test.out" &
    pids+=($!)

    # 位置制御ライブラリの単体テスト。
    g++ "${CXXFLAGS[@]}" -I"${CONTROL_DIR}" "${CONTROL_DIR}/test_position_controller.cpp" "${control_obj}" -o "${BIN_DIR}/test_position_controller.out" &
    pids+=($!)
  fi

  local failed=0
  for pid in "${pids[@]}"; do
    wait "${pid}" || failed=1
  done
  if [[ "${failed}" != "0" ]]; then
    echo "ビルドに失敗しました" >&2
    exit 1
  fi

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

  # Python 側のオフセット表 (packet_codec.py) が C++ の正本とずれていないこと。
  # robot_packet_layout_test.out --dump-offsets の出力と突き合わせる。
  log "パケット定数の Python/C++ 一致検査を実行します"
  (cd "${BRIDGE_DIR}" && python3 -m unittest test_packet_codec)

  log "cm4_sim の結合スモークテストを実行します"
  (cd "${BRIDGE_DIR}" && python3 -m unittest test_cm4_sim)

  # lancher.py が ai_cmd_v2.out に渡す追加オプション (runtime/ai_cmd_v2_options.json) の読み込み。
  log "lancher の追加オプション読み込みの単体テストを実行します"
  (cd "${CM4_DIR}" && python3 -m unittest test_ai_cmd_options)

  # ai_cmd_v2.out を --debug + pty で動かし、G474 へ送るはずの 72 バイトを検査する。
  # 実機 UART も STM32 も要らない。
  log "ai_cmd_v2 の結合スモークテストを実行します"
  (cd "${BRIDGE_DIR}" && python3 -m unittest test_forward_ai_cmd_v2)
}

main() {
  build_binaries
  run_tests
  log "ビルドが完了しました"
}

main "$@"
