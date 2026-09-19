# ai_cmd_options.py (lancher の追加オプション読み込み) の単体テスト。FastAPI・実機不要。
import json
import os
import tempfile
import unittest

import ai_cmd_options


class LoadAiCmdOptionsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self.logs = []

    def _write(self, text):
        with open(os.path.join(self.dir, ai_cmd_options.CONFIG_FILE_NAME), "w", encoding="utf-8") as f:
            f.write(text)

    def _load(self):
        return ai_cmd_options.load_ai_cmd_options(self.dir, log=self.logs.append)

    def test_missing_file_means_defaults(self):
        self.assertEqual(self._load(), [])
        self.assertEqual(self.logs, [])

    def test_valid_options_become_cli_args(self):
        self._write(json.dumps({"command_timeout_ms": 300, "g474_silence_ms": 0}))
        self.assertEqual(self._load(), ["--command-timeout-ms", "300", "--g474-silence-ms", "0"])

    def test_every_allowed_key_maps_to_a_real_option(self):
        for key, (option, minimum) in ai_cmd_options.ALLOWED_OPTIONS.items():
            self._write(json.dumps({key: minimum}))
            self.assertEqual(self._load(), [option, str(minimum)], key)

    def test_unknown_key_discards_whole_config(self):
        self._write(json.dumps({"command_timeout_ms": 300, "comand_timeout_ms": 1}))
        self.assertEqual(self._load(), [])
        self.assertTrue(any("comand_timeout_ms" in m for m in self.logs))

    def test_invalid_value_discards_whole_config(self):
        for bad in (0, -5, 1.5, "300", True, None):
            self.logs.clear()
            self._write(json.dumps({"feedback_timeout_ms": 200, "command_timeout_ms": bad}))
            self.assertEqual(self._load(), [], repr(bad))
            self.assertTrue(self.logs, repr(bad))

    def test_broken_or_non_object_json_means_defaults(self):
        for text in ("{not json", "[1, 2]", "300", ""):
            self.logs.clear()
            self._write(text)
            self.assertEqual(self._load(), [], repr(text))
            self.assertTrue(self.logs, repr(text))


if __name__ == "__main__":
    unittest.main()
