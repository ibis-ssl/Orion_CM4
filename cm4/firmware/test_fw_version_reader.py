"""FW version応答とバイナリdescriptor読出しのホスト単体試験を行う。"""

import struct
import tempfile
import unittest
from pathlib import Path

from fw_version_reader import FW_VERSION_MAGIC, crc32c, image_identity, request_packet


class FirmwareVersionReaderTest(unittest.TestCase):
    def test_request_checksum(self) -> None:
        packet = request_packet()
        self.assertEqual(len(packet), 72)
        self.assertEqual(packet[:5], b"\xFEFWVR")
        self.assertEqual(packet[-1], sum(packet[:-1]) & 0xFF)

    def test_image_identity(self) -> None:
        image = bytearray(0x500)
        struct.pack_into("<II", image, 0x400, FW_VERSION_MAGIC, 123456789)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.bin"
            path.write_bytes(image)
            self.assertEqual(image_identity(path), (123456789, crc32c(image)))


if __name__ == "__main__":
    unittest.main()
