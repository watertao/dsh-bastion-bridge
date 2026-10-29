"""First-run configuration bootstrap tests."""

import json
import tempfile
import unittest
from pathlib import Path

import config


class ConfigBootstrapTests(unittest.TestCase):
    def test_missing_config_is_created_with_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "QizhiSSHBridge" / "config.json"
            loaded = config.load_config(str(path))

            self.assertTrue(path.is_file())
            written = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(config.DEFAULT_CONFIG["launch_mode"], written["launch_mode"])
            self.assertEqual("bridge", loaded["launch_mode"])
            self.assertEqual("putty-original.exe", written["original_client_path"])

    def test_existing_config_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"launch_mode": "putty"}), encoding="utf-8")

            loaded = config.load_config(str(path))

            self.assertEqual("putty", loaded["launch_mode"])
            written = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual({"launch_mode": "putty"}, written)


if __name__ == "__main__":
    unittest.main()
