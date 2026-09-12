from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ADDON = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON))

import secrets_init  # noqa: E402


class SecretInitialisationTests(unittest.TestCase):
    def test_bootstrap_plaintext_is_not_recreated_after_scoped_key_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            original_data, original_options, original_secrets = (
                secrets_init.DATA,
                secrets_init.OPTIONS_FILE,
                secrets_init.SECRETS,
            )
            try:
                root = Path(directory)
                secrets_init.DATA = root
                secrets_init.OPTIONS_FILE = root / "options.json"
                secrets_init.SECRETS = root / "secrets"
                (root / "options.json").write_text(
                    json.dumps({"whatsapp_session_name": "atlas_1", "whatsapp_reconcile_seconds": 90}),
                    encoding="utf-8",
                )
                secrets_init.main()
                plaintext = root / "secrets" / "waha-api-key"
                digest = root / "secrets" / "waha-api-key-hash"
                value = plaintext.read_text(encoding="ascii").strip()
                self.assertEqual(
                    digest.read_text(encoding="ascii"),
                    "sha512:" + hashlib.sha512(value.encode("ascii")).hexdigest(),
                )

                (root / "secrets" / "waha-scoped-api-key").write_text("key_" + "r" * 40, encoding="ascii")
                plaintext.unlink()
                secrets_init.main()
                self.assertFalse(plaintext.exists())
                config = json.loads((root / "runtime-config.json").read_text(encoding="utf-8"))
                self.assertEqual(config["session_name"], "atlas_1")
                self.assertEqual(config["reconcile_seconds"], 90)
            finally:
                secrets_init.DATA = original_data
                secrets_init.OPTIONS_FILE = original_options
                secrets_init.SECRETS = original_secrets


if __name__ == "__main__":
    unittest.main()
