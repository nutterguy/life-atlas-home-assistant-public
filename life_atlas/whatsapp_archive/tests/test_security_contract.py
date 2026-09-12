from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import fnmatch
from pathlib import Path

ADDON = Path(__file__).resolve().parents[1]
REPO = ADDON.parent


class SecurityContractTests(unittest.TestCase):
    def test_waha_is_loopback_only_and_not_published(self):
        config = (REPO / "config.yaml").read_text(encoding="utf-8")
        run = (REPO / "run.sh").read_text(encoding="utf-8")
        dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("host_network: false", config)
        self.assertNotIn("3000/tcp", config)
        self.assertNotIn("3001/tcp", config)
        self.assertNotIn("8097/tcp", config)
        self.assertIn("WHATSAPP_API_HOSTNAME=127.0.0.1", run)
        self.assertIn("WHATSAPP_API_PORT=3001", run)
        self.assertIn("LIFE_ATLAS_WHATSAPP_CONNECTOR_HOST=127.0.0.1", run)
        self.assertIn("LIFE_ATLAS_WHATSAPP_MANAGEMENT_HOST=127.0.0.1", run)
        self.assertIn("waha-api-key-hash", run)
        self.assertIn("unset WAHA_INTERNAL_API_KEY WHATSAPP_API_KEY", run)
        self.assertIn("patch_waha_bind.py /app/dist/main.js", dockerfile)
        self.assertIn("patch_waha_noweb_sharp.py", dockerfile)

    def test_noweb_sharp_patch_is_version_pinned_and_fails_closed(self):
        spec = importlib.util.spec_from_file_location(
            "patch_waha_noweb_sharp", ADDON / "patch_waha_noweb_sharp.py"
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            module_path = Path(directory) / "sharp.cjs"
            package_path = Path(directory) / "package.json"
            module_path.write_text(
                'if (!err.code.endsWith("MODULE_NOT_FOUND")) {}', encoding="utf-8"
            )
            package_path.write_text(
                '{"version":"0.35.3","main":"./dist/index.cjs",'
                '"exports":{".":{"require":{"default":"./dist/index.cjs"}}}}',
                encoding="utf-8",
            )
            module.patch(module_path, package_path)
            patched = (Path(directory) / "life-atlas-noweb.cjs").read_text(
                encoding="utf-8"
            )
            package = json.loads(package_path.read_text(encoding="utf-8"))
            self.assertIn("Sharp is disabled", patched)
            self.assertEqual(package["main"], "./dist/life-atlas-noweb.cjs")
            self.assertEqual(
                package["exports"]["."]["require"]["default"],
                "./dist/life-atlas-noweb.cjs",
            )

            package_path.write_text('{"version":"0.35.4"}', encoding="utf-8")
            with self.assertRaises(SystemExit):
                module.patch(module_path, package_path)

    def test_image_inputs_are_versioned_and_digest_pinned_for_both_architectures(self):
        dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("noweb-2026.8.1@sha256:", dockerfile)
        self.assertIn("noweb-arm-2026.8.1@sha256:", dockerfile)
        self.assertIn("FROM base-${BUILD_ARCH}", dockerfile)
        self.assertEqual(dockerfile.count("@sha256:"), 2)

    def test_build_patch_fails_closed_if_upstream_listener_changes(self):
        spec = importlib.util.spec_from_file_location("patch_waha_bind", ADDON / "patch_waha_bind.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "main.js"
            target.write_text("async function x(){await app.listen(config.port);}", encoding="utf-8")
            module.patch(target)
            self.assertIn("app.listen(config.port, '127.0.0.1')", target.read_text(encoding="utf-8"))
            target.write_text("async function changed(){}", encoding="utf-8")
            with self.assertRaises(SystemExit):
                module.patch(target)

    def test_home_assistant_privileges_are_disabled(self):
        config = (REPO / "config.yaml").read_text(encoding="utf-8")
        for setting in ("homeassistant_api: false", "host_network: false"):
            self.assertIn(setting, config)

    def test_steady_state_waha_key_explicitly_denies_send(self):
        client = (ADDON / "waha_client.py").read_text(encoding="utf-8")
        self.assertIn('"send": False', client)
        self.assertIn('self.api_key_file.unlink()', client)

    def test_waha_message_staging_is_excluded_from_cold_backups(self):
        config = (REPO / "config.yaml").read_text(encoding="utf-8")
        self.assertIn("backup: cold", config)
        self.assertIn('"whatsapp/waha/noweb/*/store.sqlite3*"', config)
        self.assertNotIn('"waha/noweb/waha.sqlite3"', config)
        pattern = "data/whatsapp/waha/noweb/*/store.sqlite3*"
        self.assertTrue(
            fnmatch.fnmatchcase("data/whatsapp/waha/noweb/life-atlas/store.sqlite3", pattern)
        )
        self.assertTrue(
            fnmatch.fnmatchcase("data/whatsapp/waha/noweb/life-atlas/store.sqlite3-wal", pattern)
        )
        self.assertFalse(fnmatch.fnmatchcase("data/whatsapp/waha/noweb/waha.sqlite3", pattern))
        self.assertFalse(
            fnmatch.fnmatchcase(
                "data/whatsapp/waha/noweb/life-atlas/.waha.session.config.json", pattern
            )
        )


if __name__ == "__main__":
    unittest.main()
