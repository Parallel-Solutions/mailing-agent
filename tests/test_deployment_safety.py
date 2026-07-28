from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.migration_guard import (
    MigrationCompatibilityError,
    assert_database_revision_is_upgradeable,
    load_revision_graph,
    migration_heads,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSIONS_DIR = PROJECT_ROOT / "migrations" / "versions"


class MigrationGuardTests(unittest.TestCase):
    def test_current_database_head_is_accepted(self) -> None:
        graph = load_revision_graph(VERSIONS_DIR)
        heads = migration_heads(graph)
        self.assertEqual(len(heads), 1)
        self.assertEqual(
            assert_database_revision_is_upgradeable(
                heads[0],
                versions_dir=VERSIONS_DIR,
            ),
            heads,
        )

    def test_older_ancestor_is_accepted_for_forward_migration(self) -> None:
        heads = assert_database_revision_is_upgradeable(
            "0001_initial",
            versions_dir=VERSIONS_DIR,
        )
        self.assertEqual(heads, ("0030_template_source_text_cache",))

    def test_unknown_or_ahead_database_revision_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            MigrationCompatibilityError,
            "not present in this checkout",
        ):
            assert_database_revision_is_upgradeable(
                "9999_future_revision",
                versions_dir=VERSIONS_DIR,
            )

    def test_divergent_database_revision_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            (root / "0001.py").write_text(
                'revision = "0001"\ndown_revision = None\n',
                encoding="utf-8",
            )
            (root / "0002.py").write_text(
                'revision = "0002"\ndown_revision = "0001"\n',
                encoding="utf-8",
            )
            (root / "orphan.py").write_text(
                'revision = "orphan"\ndown_revision = "missing"\n',
                encoding="utf-8",
            )
            with self.assertRaises(MigrationCompatibilityError):
                assert_database_revision_is_upgradeable(
                    "orphan",
                    versions_dir=root,
                )


class ProductionDeploySafetyContractTests(unittest.TestCase):
    def test_deploy_backs_up_before_recreating_application(self) -> None:
        deploy = (PROJECT_ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
        backup_position = deploy.index("bash ./scripts/backup-prod-data.sh")
        infra_position = deploy.index('"${COMPOSE[@]}" up -d postgres minio')
        recreate_position = deploy.index("up -d --no-build --force-recreate")
        self.assertLess(backup_position, infra_position)
        self.assertLess(backup_position, recreate_position)
        self.assertIn("scripts/migration_guard.py", deploy)
        self.assertIn("-p mailing-agent", deploy)

    def test_backup_is_verified_and_bound_to_production_volume(self) -> None:
        backup = (PROJECT_ROOT / "scripts" / "backup-prod-data.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("to_regclass", backup)
        self.assertIn('count="missing"', backup)
        self.assertIn("minio_config_image", backup)
        self.assertIn("minio_image_id", backup)
        self.assertIn("mailing-agent_pgdata", backup)
        self.assertIn("mailing-agent_minio-data", backup)
        self.assertIn('"$pg_volume" != *test*', backup)
        self.assertIn('"$minio_volume" != *test*', backup)
        self.assertIn("pg_dump", backup)
        self.assertIn("pg_restore --list", backup)
        self.assertIn("tar -tf", backup)
        self.assertIn("sha256sum", backup)

    def test_each_runtime_declares_its_database_identity(self) -> None:
        expectations = {
            "docker-compose.yml": (
                "APP_ENVIRONMENT: local",
                "DATABASE_EXPECTED_NAME: mailing",
            ),
            "docker-compose.dev.yml": (
                "APP_ENVIRONMENT: development",
                "DATABASE_EXPECTED_NAME: mailing",
            ),
            "docker-compose.test.yml": (
                "APP_ENVIRONMENT: test",
                "DATABASE_EXPECTED_NAME: mailing_test",
            ),
            "docker-compose.e2e.yml": (
                "APP_ENVIRONMENT: e2e",
                "DATABASE_EXPECTED_NAME: mailing_e2e",
                "POSTGRES_DB: mailing_e2e",
                "pg_isready -U mailing -d postgres",
                "postgres-e2e-init",
            ),
            "docker-compose.prod.yml": (
                "APP_ENVIRONMENT: production",
                "DATABASE_EXPECTED_NAME: mailing",
            ),
        }
        for filename, required in expectations.items():
            content = (PROJECT_ROOT / filename).read_text(encoding="utf-8")
            for marker in required:
                with self.subTest(filename=filename, marker=marker):
                    self.assertIn(marker, content)

    def test_minio_image_is_pinned_by_default(self) -> None:
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn(
            "minio/minio:RELEASE.2025-09-07T16-13-09Z",
            compose,
        )
        self.assertNotIn("minio/minio:latest", compose)

    def test_backup_restore_verifier_uses_disposable_volumes(self) -> None:
        verifier = (PROJECT_ROOT / "scripts" / "verify-backup-restore.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("sha256sum --check", verifier)
        self.assertIn("pg_restore", verifier)
        self.assertIn("mailing_restore", verifier)
        self.assertIn("MINIO_IMAGE_ID", verifier)
        self.assertIn('docker volume rm "$pg_volume" "$minio_volume"', verifier)
        self.assertNotIn("mailing-agent_pgdata", verifier)
        self.assertNotIn("mailing-agent_minio-data", verifier)

    def test_production_scripts_do_not_remove_volumes(self) -> None:
        for filename in (
            "scripts/deploy.sh",
            "scripts/mailing-agent-deploy-root",
            "scripts/prod-audit.sh",
        ):
            content = (PROJECT_ROOT / filename).read_text(encoding="utf-8")
            with self.subTest(filename=filename):
                self.assertNotIn("down -v", content)
                self.assertNotIn("down --volumes", content)


if __name__ == "__main__":
    unittest.main()
