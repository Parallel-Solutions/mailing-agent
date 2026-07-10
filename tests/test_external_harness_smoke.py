"""Always-on smoke test for the external statistics harness.

The real external tests under ``tests/external/`` send live emails via real
providers and are therefore gated behind ``EXT_STATS_ENABLED`` and excluded
from the default suite. This smoke test runs in the default suite WITHOUT
sending anything: it only verifies the harness still imports, that the enable
guard behaves correctly, and that the config loads with safe defaults so the
harness cannot silently rot.
"""
from __future__ import annotations

import importlib
import os
import unittest
from unittest.mock import patch


EXTERNAL_MODULES = (
    "tests.external.config",
    "tests.external.report",
    "tests.external.reconciler",
    "tests.external.webhook_payloads",
    "tests.external.run_external_tests",
    "tests.external.adapters.app",
    "tests.external.adapters.mailbox",
    "tests.external.adapters.provider",
    "tests.external.test_ext_send",
    "tests.external.test_ext_webhook",
    "tests.external.test_ext_mailbox",
    "tests.external.test_ext_bounce",
    "tests.external.test_ext_reconciliation",
)


class ExternalHarnessSmokeTests(unittest.TestCase):
    def test_all_external_modules_import(self) -> None:
        for module_name in EXTERNAL_MODULES:
            with self.subTest(module=module_name):
                self.assertIsNotNone(importlib.import_module(module_name))

    def test_require_ext_enabled_skips_when_disabled(self) -> None:
        from tests.external.config import require_ext_enabled

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EXT_STATS_ENABLED", None)
            with self.assertRaises(unittest.SkipTest):
                require_ext_enabled()

    def test_require_ext_enabled_rejects_non_one_values(self) -> None:
        from tests.external.config import require_ext_enabled

        for value in ("0", "true", "yes", "  "):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"EXT_STATS_ENABLED": value}):
                    with self.assertRaises(unittest.SkipTest):
                        require_ext_enabled()

    def test_require_ext_enabled_passes_when_enabled(self) -> None:
        from tests.external.config import require_ext_enabled

        with patch.dict(os.environ, {"EXT_STATS_ENABLED": "1"}):
            # Must not raise SkipTest.
            require_ext_enabled()

    def test_load_config_uses_safe_defaults(self) -> None:
        from tests.external.config import load_config

        with patch.dict(os.environ, {}, clear=True):
            config = load_config()

        self.assertTrue(config.base_url)
        self.assertEqual(config.test_emails, [])
        # With no IMAP / provider API credentials, optional levels self-disable.
        self.assertTrue(config.skip_mailbox)
        self.assertTrue(config.skip_reconciliation)

    def test_run_external_tests_has_all_levels(self) -> None:
        from tests.external.run_external_tests import LEVEL_MODULES

        for level in ("send", "webhook", "mailbox", "bounce", "recon", "all"):
            self.assertIn(level, LEVEL_MODULES)


if __name__ == "__main__":
    unittest.main()
