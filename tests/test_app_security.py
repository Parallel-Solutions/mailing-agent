from __future__ import annotations

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.utils.config import SecurityConfigurationError, require_configured_app_password


class AppSecurityConfigTests(unittest.TestCase):
    def test_app_password_must_be_configured(self) -> None:
        with self.assertRaises(SecurityConfigurationError):
            require_configured_app_password(SimpleNamespace(app_password=""))
        with self.assertRaises(SecurityConfigurationError):
            require_configured_app_password(SimpleNamespace(app_password="   "))

    def test_configured_app_password_is_accepted(self) -> None:
        require_configured_app_password(SimpleNamespace(app_password="strong-local-password"))

    def test_main_auth_dependency_imports_basic_authenticator(self) -> None:
        main_path = Path(__file__).resolve().parents[1] / "main.py"
        tree = ast.parse(main_path.read_text(encoding="utf-8"))
        imported_names = {
            alias.asname or alias.name
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "src.security.auth"
            for alias in node.names
        }

        self.assertIn("authenticate_basic_user", imported_names)


if __name__ == "__main__":
    unittest.main()