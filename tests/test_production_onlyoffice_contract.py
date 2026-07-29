from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


class ProductionOnlyOfficeContractTests(unittest.TestCase):
    def test_production_overlay_uses_https_jwt_and_no_direct_port(self) -> None:
        overlay = _read("docker-compose.prod.yml")

        self.assertIn(
            "ONLYOFFICE_EDITOR_PUBLIC_URL: https://offer.parresh.ru/onlyoffice",
            overlay,
        )
        self.assertIn(
            "ONLYOFFICE_JWT_SECRET: ${ONLYOFFICE_JWT_SECRET:?"
            "Set ONLYOFFICE_JWT_SECRET in .env.docker}",
            overlay,
        )
        self.assertIn(
            "ghcr.io/parallel-solutions/mailing-agent:onlyoffice-9.4.0.1",
            overlay,
        )
        self.assertIn('JWT_ENABLED: "true"', overlay)
        self.assertIn("ports: !reset []", overlay)

    def test_base_compose_pins_onlyoffice_and_persists_its_state(self) -> None:
        compose = _read("docker-compose.yml")

        self.assertIn("onlyoffice/documentserver:9.4.0.1", compose)
        self.assertNotIn("onlyoffice/documentserver:latest", compose)
        self.assertIn("onlyoffice-data:/var/www/onlyoffice/Data", compose)
        self.assertIn("onlyoffice-db:/var/lib/postgresql", compose)
        self.assertIn("ONLYOFFICE_JWT_SECRET: ${ONLYOFFICE_JWT_SECRET:-}", compose)
        self.assertIn("stop_grace_period: 75s", compose)

    def test_deploy_enables_onlyoffice_and_checks_public_assets(self) -> None:
        deploy = _read("scripts/deploy.sh")

        self.assertIn("--env-file .env.docker", deploy)
        self.assertIn("--profile onlyoffice", deploy)
        self.assertRegex(
            deploy,
            re.compile(r'up -d postgres minio redis gotenberg onlyoffice'),
        )
        self.assertIn(
            '$PUBLIC_BASE_URL/onlyoffice/web-apps/apps/api/documents/api.js',
            deploy,
        )
        self.assertIn('ONLYOFFICE_IMAGE="$ONLYOFFICE_IMAGE"', deploy)
        self.assertIn(
            'wait_for_container_health "mailing-agent-worker-1" "worker"',
            deploy,
        )
        self.assertLess(
            deploy.index('echo "=== Production audit ==="'),
            deploy.index(
                'prune_old_repo_images "$EXPECTED_IMAGE_ID"',
                deploy.index('echo "=== Production audit ==="'),
            ),
        )

    def test_audit_requires_onlyoffice_and_keeps_it_off_host_ports(self) -> None:
        audit = _read("scripts/prod-audit.sh")

        self.assertIn("mailing-agent-onlyoffice-1", audit)
        self.assertIn('$svc is not healthy (health=$health)', audit)
        self.assertIn("OnlyOffice is not healthy", audit)
        self.assertIn("OnlyOffice port 80 must not be published directly", audit)
        self.assertIn("App and OnlyOffice JWT secrets do not match", audit)
        self.assertIn("gotenberg|onlyoffice", audit)
        forbidden_block = audit.split("forbidden_patterns=(", 1)[1].split(")", 1)[0]
        self.assertNotIn("onlyoffice", forbidden_block)

    def test_ci_mirrors_pinned_image_before_production_deploy(self) -> None:
        workflow = _read(".github/workflows/ci.yml")

        self.assertIn("pg_isready -U mailing -d mailing_test", workflow)
        self.assertIn("mirror-onlyoffice:", workflow)
        self.assertIn(
            "docker.io/onlyoffice/documentserver:9.4.0.1",
            workflow,
        )
        self.assertIn(
            "ghcr.io/parallel-solutions/mailing-agent:onlyoffice-9.4.0.1",
            workflow,
        )
        self.assertIn(
            "needs: [unit, e2e-smoke, build-image, mirror-onlyoffice]",
            workflow,
        )

    def test_root_rollback_uses_the_production_compose_contract(self) -> None:
        wrapper = _read("scripts/mailing-agent-deploy-root")

        self.assertIn("--env-file .env.docker --profile onlyoffice", wrapper)
        self.assertIn(
            "-f docker-compose.yml -f docker-compose.prod.yml",
            wrapper,
        )
        self.assertIn('docker pull "$previous_image"', wrapper)
        self.assertIn('previous_head="$deployed_sha"', wrapper)
        self.assertIn(
            'printf \'%s\\n\' "$requested_sha" > "$STATE_DIR/current_sha"',
            wrapper,
        )


if __name__ == "__main__":
    unittest.main()
