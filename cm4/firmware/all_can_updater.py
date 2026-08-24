#!/usr/bin/env python3
"""全CANノードを先に安全な更新状態へ移し、イメージ単位で更新・一括再起動する。"""

from __future__ import annotations

import argparse
import secrets
import struct
import time
from dataclasses import dataclass
from pathlib import Path

from sub_can_updater_v2 import (
    CHUNK_SIZE,
    MSG_BEGIN,
    MSG_CHUNK,
    MSG_FINALIZE,
    MSG_REBOOT,
    Gateway,
    GatewayResponseError,
    UpdateError,
    crc32c,
)


@dataclass(frozen=True)
class TargetImage:
    name: str
    path: Path
    node_can1: int
    node_can2: int


def transfer(gateway: Gateway, target: TargetImage, session: int) -> tuple[int, int]:
    image = target.path.read_bytes()
    image_crc = crc32c(image)
    gateway.select_targets(session, target.node_can1, target.node_can2)
    gateway.request(MSG_BEGIN, bytes([session, 0, 0, 0]) + struct.pack("<II", len(image), image_crc), timeout=12.0)
    for offset in range(0, len(image), CHUNK_SIZE):
        chunk = image[offset : offset + CHUNK_SIZE]
        payload = struct.pack("<IIHB", offset, crc32c(chunk), len(chunk), 0) + chunk
        for attempt in range(3):
            try:
                response = gateway.request(MSG_CHUNK, payload, timeout=0.6)
                break
            except GatewayResponseError as error:
                if error.gateway_status not in (2, 3) or attempt == 2:
                    raise
        else:
            raise UpdateError(f"{target.name}: chunk retry exhausted at {offset}")
        committed = struct.unpack_from("<I", response, 4)[0]
        if committed != offset + len(chunk):
            raise UpdateError(f"{target.name}: commit mismatch {committed} != {offset + len(chunk)}")
        if committed % 7168 < CHUNK_SIZE or committed == len(image):
            print(f"target={target.name} progress={committed}/{len(image)}", flush=True)
    response = gateway.request(MSG_FINALIZE, timeout=8.0)
    if struct.unpack_from("<I", response, 4)[0] != len(image):
        raise UpdateError(f"{target.name}: FINALIZE size mismatch")
    print(f"target={target.name} confirmed size={len(image)} crc32c=0x{image_crc:08X}", flush=True)
    return len(image), image_crc


def update_all(port: str, targets: list[TargetImage]) -> None:
    session = secrets.randbelow(255) + 1
    gateway = Gateway(port)
    started = time.monotonic()
    try:
        gateway.enter_gateway(session)
        # 全ノードを先にbootloaderへ入れ、通常制御へ戻るノードを残さない。
        for target in targets:
            print(f"stage=enter target={target.name}", flush=True)
            gateway.select_targets(session, target.node_can1, target.node_can2)
        for target in targets:
            print(f"stage=transfer target={target.name}", flush=True)
            transfer(gateway, target, session)
        # 全image確定後にまとめて再起動する。
        for target in targets:
            gateway.select_targets(session, target.node_can1, target.node_can2)
            gateway.request(MSG_REBOOT, timeout=3.0)
    finally:
        gateway.close()
    print(f"ALL_CAN_UPDATE_OK targets={len(targets)} elapsed={time.monotonic() - started:.3f}s", flush=True)


def node_id(value: str) -> int:
    parsed = int(value, 0)
    if not 0 <= parsed <= 0xFF:
        raise argparse.ArgumentTypeError("node ID must be 0..255")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sub", required=True, type=Path)
    parser.add_argument("--bldc", required=True, type=Path)
    parser.add_argument("--power", required=True, type=Path)
    parser.add_argument("--port", default="/dev/ttyS0")
    parser.add_argument("--sub-can1", type=node_id, default=4)
    parser.add_argument("--power-can1", type=node_id, default=100)
    parser.add_argument("--bldc-can1", type=node_id, default=16)
    parser.add_argument("--bldc-can2", type=node_id, default=17)
    args = parser.parse_args()
    targets = [
        TargetImage("sub", args.sub, args.sub_can1, 0xFF),
        TargetImage("bldc", args.bldc, args.bldc_can1, args.bldc_can2),
        TargetImage("power", args.power, args.power_can1, 0xFF),
    ]
    missing = [str(target.path) for target in targets if not target.path.is_file()]
    if missing:
        parser.error("image not found: " + ", ".join(missing))
    update_all(args.port, targets)


if __name__ == "__main__":
    main()
