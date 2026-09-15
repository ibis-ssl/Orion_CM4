// このファイルは crane から位置制御の調整値を稼働中に変更するための
// 設定パケット (UDP 20 バイト) の形式・検証・適用を定義する責務を持つ。
//
// 【715 バイトの指令パケットに相乗りさせない理由】
// 64 バイトのコマンドレイアウトは crane / G474 / framework / CM4 の 4 者一致が
// 不変条件で、robot_packet_layout_test.cpp が全オフセットを static_assert で
// 固定している。しかも crane は使用スロットの byte 28..31 / 38..63 をゼロ初期化
// しておらず「予約領域＝0」が成り立たない。別ポートの小さなデータグラムにすれば
// G474 と framework の変更が一切要らず、レイアウト検査の前提も動かない。
//
// 【変更できるのが 3 つだけである理由】
// ここに載るのは追従性能の調整ノブだけである。command_timeout_ms /
// feedback_timeout_ms は安全停止の閾値、vision_age_limit_ms は G474 の定数と
// 一致させるための値 (position_controller.h の該当コメント) で、いずれも
// 現地から遠隔で動かせるようにしてはならない。
//
// 【実機と sim が同じ経路を通ること】
// 受信・検証・適用・ログを drainConfigSocket() に閉じ込め、実機の
// forward_ai_cmd_v2.cpp とシミュレータの cm4_sim.cpp の両方がそれを呼ぶ。
// 片方だけ検証が緩むと、sim で確かめたゲインが実機では黙って拒否される。

#ifndef ORION_CM4__BRIDGE__CONFIG_PACKET_H_
#define ORION_CM4__BRIDGE__CONFIG_PACKET_H_

#include <arpa/inet.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cmath>
#include <cstddef>

#include "position_controller.h"

namespace orion
{

// 20 バイト固定。crane 側の送信実装と揃えること (doc/control_packet.md)。
//
//   0..3   magic 'O','C','4','C'
//   4      version (= 1)
//   5      robot_id (0xFF = 全機宛)
//   6..7   予約 (0)
//   8..11  position_gain       float32 little endian
//   12..15 deceleration        float32 little endian
//   16..19 position_tolerance  float32 little endian
//
// 指令パケットのような 2 バイト固定小数ではなく素の float32 にしてある。
// 毎周期 11 台ぶんを運ぶわけではないので圧縮する理由が無く、量子化を挟むと
// crane の表示値と CM4 の実効値が微妙に食い違う。エンディアンは CM4 も開発 PC も
// little endian なので変換しない (AGENTS.md: 可搬性は考慮しない)。
constexpr size_t kConfigPacketSize = 20;
constexpr uint8_t kConfigPacketVersion = 1;
constexpr uint8_t kConfigPacketBroadcastId = 0xFF;
constexpr int kDefaultConfigPort = 12350;

// 受理する範囲。「効くかどうか」ではなく「危険でないこと」だけを見る。
// 範囲外は黙ってクランプせずデータグラムごと捨てて理由をログに出す。
// クランプすると crane 側の表示と実機の実効値が食い違ったまま気付けない。
constexpr float kConfigPositionGainMax = 20.0f;
constexpr float kConfigDecelerationMax = 20.0f;      // [m/s^2]
constexpr float kConfigPositionToleranceMax = 1.0f;  // [m]

enum class ConfigPacketStatus {
  Applied,
  NotForThisRobot,
  WrongSize,
  BadMagic,
  UnsupportedVersion,
  OutOfRange,
};

inline const char * toString(ConfigPacketStatus status)
{
  switch (status) {
    case ConfigPacketStatus::Applied: return "Applied";
    case ConfigPacketStatus::NotForThisRobot: return "NotForThisRobot";
    case ConfigPacketStatus::WrongSize: return "WrongSize";
    case ConfigPacketStatus::BadMagic: return "BadMagic";
    case ConfigPacketStatus::UnsupportedVersion: return "UnsupportedVersion";
    case ConfigPacketStatus::OutOfRange: return "OutOfRange";
  }
  return "Unknown";
}

inline float readFloatLe(const uint8_t * p)
{
  float v = 0.f;
  memcpy(&v, p, sizeof(v));
  return v;
}

// 負値も弾く。kp < 0 は目標から遠ざかる向きへ加速し、decel < 0 は制動エンベロープの
// 平方根の中身を負にする。tolerance < 0 は停止判定が永久に成立しなくなる。
inline bool configValueInRange(float v, float max_value) { return std::isfinite(v) && v >= 0.f && v <= max_value; }

inline bool configIsForMe(uint8_t dst_id, const int * robot_ids, size_t robot_count)
{
  if (dst_id == kConfigPacketBroadcastId) return true;
  for (size_t i = 0; i < robot_count; ++i) {
    if (robot_ids[i] == static_cast<int>(dst_id)) return true;
  }
  return false;
}

// 成功したときだけ config の 3 フィールドを書き換える。失敗時は config を触らない。
inline ConfigPacketStatus decodeConfigPacket(
  const uint8_t * buf, size_t len, const int * robot_ids, size_t robot_count, PositionControllerConfig * config)
{
  if (len != kConfigPacketSize) return ConfigPacketStatus::WrongSize;
  if (buf[0] != 'O' || buf[1] != 'C' || buf[2] != '4' || buf[3] != 'C') return ConfigPacketStatus::BadMagic;
  if (buf[4] != kConfigPacketVersion) return ConfigPacketStatus::UnsupportedVersion;
  if (!configIsForMe(buf[5], robot_ids, robot_count)) return ConfigPacketStatus::NotForThisRobot;

  const float position_gain = readFloatLe(&buf[8]);
  const float deceleration = readFloatLe(&buf[12]);
  const float position_tolerance = readFloatLe(&buf[16]);
  if (!configValueInRange(position_gain, kConfigPositionGainMax) || !configValueInRange(deceleration, kConfigDecelerationMax) ||
    !configValueInRange(position_tolerance, kConfigPositionToleranceMax)) {
    return ConfigPacketStatus::OutOfRange;
  }

  config->position_gain = position_gain;
  config->deceleration = deceleration;
  config->position_tolerance = position_tolerance;
  return ConfigPacketStatus::Applied;
}

// 「変わったか」だけを見るので誤差許容は入れない。イプシロン比較にすると、
// 現地で 0.001 刻みに詰めたときにログが出なくなり、設定が届いたのか
// 落ちたのかが分からなくなる。
inline bool sameConfigTunables(const PositionControllerConfig & a, const PositionControllerConfig & b)
{
  return a.position_gain == b.position_gain && a.deceleration == b.deceleration && a.position_tolerance == b.position_tolerance;
}

struct ConfigReceiver
{
  uint64_t applied_count = 0;
  uint64_t rejected_count = 0;
  // 直近に出した拒否ログの理由。同じ理由の連続はログを抑制する。
  ConfigPacketStatus last_rejected = ConfigPacketStatus::Applied;
};

// 設定ポートを bind した非ブロッキング UDP ソケットを返す。失敗なら -1。
//
// crane は broadcast で送るので INADDR_ANY に bind する。127.0.0.1 では届かない。
// SO_REUSEADDR は付けない。同じポートを 2 プロセスが bind できてしまうと、
// 設定パケットが両者に振り分けられて「たまに効かない」状態になる。
inline int openConfigSocket(int port)
{
  const int sock = socket(AF_INET, SOCK_DGRAM, 0);
  if (sock < 0) {
    perror("socket(config)");
    return -1;
  }
  struct sockaddr_in addr;
  memset(&addr, 0, sizeof(addr));
  addr.sin_family = AF_INET;
  addr.sin_port = htons(static_cast<uint16_t>(port));
  addr.sin_addr.s_addr = INADDR_ANY;
  if (bind(sock, reinterpret_cast<struct sockaddr *>(&addr), sizeof(addr)) != 0) {
    fprintf(stderr, "bind(config 0.0.0.0:%d) に失敗しました\n", port);
    close(sock);
    return -1;
  }
  int nonblock = 1;
  ioctl(sock, FIONBIO, &nonblock);
  return sock;
}

// 設定ソケットを空になるまで読み、有効なパケットを順に適用する。
//
// ログは「値が変わったとき」と「拒否理由が変わったとき」だけ出す。crane は
// 同じ値を定期送信するので、毎回出すと現地で本当に読みたいログが流れてしまう。
inline void drainConfigSocket(int sock, const int * robot_ids, size_t robot_count, PositionControllerConfig * config, ConfigReceiver * state)
{
  // 20 バイトより大きいデータグラムを WrongSize として数えるために余裕を持たせる。
  uint8_t buf[64];
  while (true) {
    const ssize_t n = recv(sock, buf, sizeof(buf), MSG_DONTWAIT | MSG_TRUNC);
    if (n < 0) break;  // EAGAIN: 受信キューが空

    PositionControllerConfig candidate = *config;
    const ConfigPacketStatus status = decodeConfigPacket(buf, static_cast<size_t>(n), robot_ids, robot_count, &candidate);
    // 他機宛は異常ではないので数えもログもしない (crane は broadcast で送る)。
    if (status == ConfigPacketStatus::NotForThisRobot) continue;

    if (status != ConfigPacketStatus::Applied) {
      state->rejected_count++;
      if (state->last_rejected != status) {
        state->last_rejected = status;
        fprintf(stderr, "位置制御の設定パケットを拒否しました: %s\n", toString(status));
      }
      continue;
    }

    state->applied_count++;
    if (!sameConfigTunables(candidate, *config)) {
      printf(
        "位置制御の設定を更新: kp %.3f -> %.3f / decel %.3f -> %.3f / tol %.4f -> %.4f\n", static_cast<double>(config->position_gain),
        static_cast<double>(candidate.position_gain), static_cast<double>(config->deceleration), static_cast<double>(candidate.deceleration),
        static_cast<double>(config->position_tolerance), static_cast<double>(candidate.position_tolerance));
    }
    *config = candidate;
    // 次に拒否が起きたら 1 行出す。
    state->last_rejected = ConfigPacketStatus::Applied;
  }
}

}  // namespace orion

#endif  // ORION_CM4__BRIDGE__CONFIG_PACKET_H_
