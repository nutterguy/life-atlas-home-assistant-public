from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ADDON = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON))


def load_adapter():
    spec = importlib.util.spec_from_file_location("adapter_under_test", ADDON / "adapter.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "secrets").mkdir(parents=True)
        (self.root / "secrets" / "connector-api-key").write_text("k" * 43, encoding="ascii")
        self.adapter = load_adapter()

        class Stub:
            connector_key_file = self.root / "secrets" / "connector-api-key"
            pairing_file = self.root / "secrets" / "pairing.json"

        Stub.pairing_record = self.adapter.Service.pairing_record
        Stub.claim_connector_key = self.adapter.Service.claim_connector_key
        Stub.forget_pairing = self.adapter.Service.forget_pairing
        self.service = Stub()

    def test_the_first_caller_is_given_the_key(self):
        self.assertEqual(self.service.claim_connector_key("life-atlas"), "k" * 43)

    def test_a_second_caller_is_refused(self):
        self.service.claim_connector_key("life-atlas")
        with self.assertRaises(PermissionError):
            self.service.claim_connector_key("someone-else")

    def test_the_claim_is_recorded_so_it_is_visible_not_silent(self):
        self.service.claim_connector_key("life-atlas")
        record = self.service.pairing_record()
        self.assertEqual(record["peer"], "life-atlas")
        self.assertTrue(record["claimed_at"])

    def test_forgetting_a_pairing_allows_a_deliberate_re_pair(self):
        self.service.claim_connector_key("life-atlas")
        self.service.forget_pairing()
        self.assertEqual(self.service.claim_connector_key("life-atlas"), "k" * 43)

    def test_pairing_does_not_hand_over_a_key_that_does_not_exist_yet(self):
        (self.root / "secrets" / "connector-api-key").write_text("", encoding="ascii")
        with self.assertRaises(RuntimeError):
            self.service.claim_connector_key("life-atlas")
        self.assertEqual(self.service.pairing_record(), {})


if __name__ == "__main__":
    unittest.main()
