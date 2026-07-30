from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from scripts.recover_object_store_catalog import (
    StoredObject,
    _recover_templates,
    _recoverable_document_sources,
)
from src.infra.models import TemplateVersion


class ObjectStoreRecoveryTests(unittest.TestCase):
    def test_future_legitimate_source_is_not_filtered_by_timestamp(self) -> None:
        source = StoredObject(
            key="template-library/template-1/version-1/source/proposal.pdf",
            size=2_000_000,
            last_modified=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )

        recovered, excluded = _recoverable_document_sources([source])

        self.assertEqual(recovered, [source])
        self.assertEqual(excluded, [])

    def test_existing_template_with_missing_version_is_planned_for_recovery(
        self,
    ) -> None:
        source = StoredObject(
            key="template-library/template-1/version-2/source/proposal.pdf",
            size=2_000_000,
            last_modified=datetime(2026, 7, 28, tzinfo=timezone.utc),
        )
        session = MagicMock()
        session.scalars.return_value.all.return_value = ["template-1"]
        session.execute.return_value.all.return_value = []

        @contextmanager
        def fake_session_scope():
            yield session

        with patch(
            "scripts.recover_object_store_catalog.session_scope",
            fake_session_scope,
        ):
            report = _recover_templates(
                [source],
                owner_username="admin",
                apply=False,
            )

        self.assertEqual(report["planned_template_ids"], [])
        self.assertEqual(report["planned_version_ids"], ["version-2"])
        self.assertEqual(report["recovered_versions"], 1)

    def test_existing_version_is_not_planned_twice(self) -> None:
        source = StoredObject(
            key="template-library/template-1/version-1/source/proposal.pdf",
            size=2_000_000,
            last_modified=datetime(2026, 7, 28, tzinfo=timezone.utc),
        )
        session = MagicMock()
        session.scalars.return_value.all.return_value = ["template-1"]
        session.execute.return_value.all.return_value = [("version-1", "template-1", 1)]

        @contextmanager
        def fake_session_scope():
            yield session

        with patch(
            "scripts.recover_object_store_catalog.session_scope", fake_session_scope
        ):
            report = _recover_templates([source], owner_username="admin", apply=False)
        self.assertEqual(report["planned_version_ids"], [])
        self.assertEqual(report["recovered_versions"], 0)

    def test_apply_adds_missing_version_and_repairs_active_version(self) -> None:
        source = StoredObject(
            key="template-library/template-1/version-2/source/proposal.pdf",
            size=2_000_000,
            last_modified=datetime(2026, 7, 28, tzinfo=timezone.utc),
        )
        template = SimpleNamespace(active_version_id=None)

        discovery_session = MagicMock()
        discovery_session.scalars.return_value.all.return_value = ["template-1"]
        discovery_session.execute.return_value.all.return_value = []
        write_session = MagicMock()
        write_session.get.return_value = template
        repair_session = MagicMock()
        repair_session.get.return_value = template
        repair_session.scalars.return_value.all.return_value = ["version-2"]

        @contextmanager
        def scope(session):
            yield session

        scopes = [
            scope(discovery_session),
            scope(write_session),
            scope(repair_session),
        ]
        with (
            patch(
                "scripts.recover_object_store_catalog.session_scope",
                side_effect=scopes,
            ),
            patch(
                "scripts.recover_object_store_catalog._get_bytes",
                return_value=b"%PDF-1.4",
            ),
            patch(
                "scripts.recover_object_store_catalog.template_service._file_text",
                return_value="proposal",
            ),
            patch(
                "scripts.recover_object_store_catalog.template_service._extract_variables",
                return_value=[],
            ),
        ):
            report = _recover_templates([source], owner_username="admin", apply=True)

        added_versions = [
            call.args[0]
            for call in write_session.add.call_args_list
            if isinstance(call.args[0], TemplateVersion)
        ]
        self.assertEqual([version.id for version in added_versions], ["version-2"])
        self.assertEqual(template.active_version_id, "version-2")
        self.assertEqual(report["repaired_active_template_ids"], ["template-1"])


if __name__ == "__main__":
    unittest.main()
