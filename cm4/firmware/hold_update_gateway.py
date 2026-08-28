#!/usr/bin/env python3
"""Mainをリセットせず更新ゲートウェイ待機へ移し、通常CAN制御送信を停止する。"""

from __future__ import annotations

import argparse
import secrets

from sub_can_updater_v2 import Gateway


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="/dev/serial0")
    args = parser.parse_args()
    session = secrets.randbelow(255) + 1
    gateway = Gateway(args.port)
    try:
        gateway.enter_gateway(session)
    finally:
        gateway.close()
    print(f"MAIN_GATEWAY_HOLD session={session}", flush=True)


if __name__ == "__main__":
    main()
