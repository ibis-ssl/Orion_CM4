"""全CANノード更新の順序、左右BLDC並列指定、commit確認を実機なしで検証する。"""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import all_can_updater as updater


class FakeGateway:
    def __init__(self, port: str) -> None:
        self.events: list[tuple] = [("open", port)]
        self.expected_nodes: set[int] = set()
        self.image_size = 0

    def enter_gateway(self, session: int) -> None:
        self.events.append(("legacy", session))

    def select_targets(self, session: int, node_can1: int, node_can2: int) -> bytes:
        self.expected_nodes = {n for n in (node_can1, node_can2) if n != 0xFF}
        self.events.append(("select", node_can1, node_can2))
        return struct.pack("<BBBBI", 0, 0, min(self.expected_nodes), session, 0)

    def request(self, message_type: int, payload: bytes = b"", timeout: float = 0.0) -> bytes:
        del timeout
        self.events.append(("request", message_type))
        if message_type == updater.MSG_BEGIN:
            self.image_size = struct.unpack_from("<I", payload, 4)[0]
            value = 0
        elif message_type == updater.MSG_CHUNK:
            offset, _, length = struct.unpack_from("<IIH", payload)
            value = offset + length
        elif message_type == updater.MSG_FINALIZE:
            value = self.image_size
        else:
            value = self.image_size
        return struct.pack("<BBBBI", 0, 0, min(self.expected_nodes), 1, value)

    def close(self) -> None:
        self.events.append(("close",))


class UpdateAllTest(unittest.TestCase):
    def test_all_nodes_enter_before_first_begin_and_bldc_is_dual_bus(self) -> None:
        fake = FakeGateway("unused")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            targets = []
            for name, nodes in (("sub", (4, 0xFF)), ("bldc", (16, 17)), ("power", (100, 0xFF))):
                image = root / f"{name}.bin"
                image.write_bytes(bytes(range(256)) * 5)
                targets.append(updater.TargetImage(name, image, *nodes))
            with patch.object(updater, "Gateway", return_value=fake):
                updater.update_all("fake", targets)
        first_begin = fake.events.index(("request", updater.MSG_BEGIN))
        initial_selects = [event for event in fake.events[:first_begin] if event[0] == "select"]
        self.assertEqual(initial_selects[:3], [("select", 4, 0xFF), ("select", 16, 17), ("select", 100, 0xFF)])
        self.assertIn(("select", 16, 17), fake.events)
        self.assertEqual(sum(event == ("request", updater.MSG_FINALIZE) for event in fake.events), 3)
        self.assertEqual(sum(event == ("request", updater.MSG_REBOOT) for event in fake.events), 3)


if __name__ == "__main__":
    unittest.main()
