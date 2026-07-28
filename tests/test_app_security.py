from __future__ import annotations

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.utils.config import (
    SecurityConfigurationError,
    require_configured_app_password,
    validate_runtime_database,
)


class AppSecurityConfigTests(unittest.TestCase):
    def test_app_password_must_be_configured(self) -> None:
        with self.assertRaises(SecurityConfigurationError):
            require_configured_app_password(SimpleNamespace(app_password=""))
        with self.assertRaises(SecurityConfigurationError):
            require_configured_app_password(SimpleNamespace(app_password="   "))

    def test_configured_app_password_is_accepted(self) -> None:
        require_configured_app_password(SimpleNamespace(app_password="strong-local-password"))

    def test_public_base_url_rejects_parrpesh_typo(self) -> None:
        from src.utils.config import validate_public_base_url

        with self.assertRaises(SecurityConfigurationError):
            validate_public_base_url(SimpleNamespace(public_base_url="https://offer.parrpesh.ru"))

    def test_public_base_url_allows_localhost(self) -> None:
        from src.utils.config import validate_public_base_url

        validate_public_base_url(SimpleNamespace(public_base_url="http://localhost:9806"))

    def test_public_base_url_rejects_empty(self) -> None:
        from src.utils.config import validate_public_base_url

        with self.assertRaises(SecurityConfigurationError):
            validate_public_base_url(SimpleNamespace(public_base_url=""))

    def test_runtime_database_accepts_each_isolated_environment(self) -> None:
        cases = (
            ("local", "mailing"),
            ("development", "mailing"),
            ("test", "mailing_test"),
            ("e2e", "mailing_e2e"),
            ("production", "mailing"),
        )
        for environment, database_name in cases:
            with self.subTest(environment=environment):
                validate_runtime_database(
                    SimpleNamespace(
                        app_environment=environment,
                        database_expected_name=database_name,
                        database_url=(
                            "postgresql+psycopg://mailing:mailing@postgres:5432/"
                            f"{database_name}"
                        ),
                    )
                )

    def test_test_runtime_rejects_working_database(self) -> None:
        with self.assertRaisesRegex(
            SecurityConfigurationError,
            "expected 'mailing_test'",
        ):
            validate_runtime_database(
                SimpleNamespace(
                    app_environment="test",
                    database_expected_name="mailing_test",
                    database_url=(
                        "postgresql+psycopg://mailing:mailing@postgres:5432/mailing"
                    ),
                )
            )

    def test_production_runtime_rejects_test_database(self) -> None:
        with self.assertRaises(SecurityConfigurationError):
            validate_runtime_database(
                SimpleNamespace(
                    app_environment="production",
                    database_expected_name="mailing",
                    database_url=(
                        "postgresql+psycopg://mailing:mailing@postgres:5432/"
                        "mailing_test"
                    ),
                )
            )

    def test_main_auth_dependency_uses_session_store(self) -> None:
        main_path = Path(__file__).resolve().parents[1] / "main.py"
        tree = ast.parse(main_path.read_text(encoding="utf-8"))
        imported_names = {
            alias.asname or alias.name
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "src.security.session_store"
            for alias in node.names
        }

        self.assertIn("get_session_username", imported_names)
        self.assertIn("SESSION_COOKIE_NAME", imported_names)


if __name__ == "__main__":
    unittest.main()
