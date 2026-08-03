from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.campaigns import (
    audience_service,
    chain_service,
    company_service,
    connection_service,
    service as campaign_service,
    template_service,
)
from src.generator.delivery.smtp_mailboxes import (
    create_mailbox,
    resolve_smtp_credentials,
)
from src.security.auth import Principal
from src.security.company_access import (
    can_manage_company,
    can_view_owned_resource,
    connection_owner_usernames,
    visible_owner_usernames,
)
from src.security.user_store import create_user, get_user_record
from src.utils.config import settings
from src.web.companies_router import create_companies_router
from src.web.v1_router import create_v1_router
from tests.bootstrap import bootstrap_test_runtime


class CompanyAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        self.admin = "admin"
        self.company_admin = f"ca{uuid.uuid4().hex[:6]}"
        self.member = f"mb{uuid.uuid4().hex[:6]}"
        self.outsider = f"out{uuid.uuid4().hex[:6]}"
        create_user(self.admin, "Pass12345!", role="admin")
        create_user(self.company_admin, "Pass12345!")
        create_user(self.member, "Pass12345!")
        create_user(self.outsider, "Pass12345!")

        self.company = company_service.create_company(
            name="Test Org",
            phone="+7 900 000-00-00",
            contact_person_name="Ivan Admin",
        )
        company_service.add_member(
            self.company["id"],
            self.company_admin,
            role="company_admin",
        )
        company_service.add_member(self.company["id"], self.member, role="member")

        self.admin_app = FastAPI()
        self.admin_app.include_router(
            create_companies_router(
                check_auth=lambda: Principal(self.admin, "root", "admin")
            )
        )
        self.admin_app.include_router(
            create_v1_router(check_auth=lambda: Principal(self.admin, "root", "admin"))
        )
        self.admin_client = TestClient(self.admin_app)

        self.ca_record = get_user_record(self.company_admin)
        self.member_record = get_user_record(self.member)
        assert self.ca_record and self.member_record

        self.ca_principal = Principal(
            self.ca_record.username,
            self.ca_record.tenant_id,
            self.ca_record.role,
            company_id=self.ca_record.company_id,
            company_role=self.ca_record.company_role,
        )
        self.member_principal = Principal(
            self.member_record.username,
            self.member_record.tenant_id,
            self.member_record.role,
            company_id=self.member_record.company_id,
            company_role=self.member_record.company_role,
        )

        self.ca_app = FastAPI()
        self.ca_app.include_router(
            create_companies_router(check_auth=lambda: self.ca_principal)
        )
        self.ca_app.include_router(
            create_v1_router(check_auth=lambda: self.ca_principal)
        )
        self.ca_client = TestClient(self.ca_app)

        self.member_app = FastAPI()
        self.member_app.include_router(
            create_companies_router(check_auth=lambda: self.member_principal)
        )
        self.member_app.include_router(
            create_v1_router(check_auth=lambda: self.member_principal)
        )
        self.member_client = TestClient(self.member_app)

    def test_app_admin_lists_and_updates_companies(self) -> None:
        listed = self.admin_client.get("/api/v1/companies")
        self.assertEqual(listed.status_code, 200)
        self.assertGreaterEqual(listed.json()["result"]["total"], 1)

        updated = self.admin_client.patch(
            f"/api/v1/companies/{self.company['id']}",
            json={"phone": "+7 900 111-11-11"},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["result"]["phone"], "+7 900 111-11-11")

    def test_company_admin_manages_members_but_cannot_view_other_company(self) -> None:
        added = self.ca_client.post(
            f"/api/v1/companies/{self.company['id']}/members",
            json={
                "username": self.outsider,
                "password": "Pass12345!",
                "role": "member",
            },
        )
        self.assertEqual(added.status_code, 200)

        members = self.ca_client.get(f"/api/v1/companies/{self.company['id']}/members")
        self.assertEqual(members.status_code, 200)
        usernames = {item["username"] for item in members.json()["result"]}
        self.assertIn(self.outsider, usernames)

        other = company_service.create_company(name="Other Org")
        visible = self.ca_client.get(f"/api/v1/companies/{other['id']}")
        self.assertEqual(visible.status_code, 403)

    def test_campaign_visibility_by_role(self) -> None:
        member_campaign = campaign_service.create_campaign(
            self.member, {"name": "Member campaign"}
        )
        admin_campaign = campaign_service.create_campaign(
            self.company_admin, {"name": "Admin campaign"}
        )

        member_list = self.member_client.get("/api/v1/campaigns")
        self.assertEqual(member_list.status_code, 200)
        member_ids = {item["id"] for item in member_list.json()["result"]["items"]}
        self.assertIn(member_campaign["id"], member_ids)
        self.assertNotIn(admin_campaign["id"], member_ids)

        ca_list = self.ca_client.get("/api/v1/campaigns")
        self.assertEqual(ca_list.status_code, 200)
        ca_ids = {item["id"] for item in ca_list.json()["result"]["items"]}
        self.assertIn(member_campaign["id"], ca_ids)
        self.assertIn(admin_campaign["id"], ca_ids)

        admin_list = self.admin_client.get("/api/v1/campaigns")
        self.assertEqual(admin_list.status_code, 200)
        admin_ids = {item["id"] for item in admin_list.json()["result"]["items"]}
        self.assertIn(member_campaign["id"], admin_ids)

    def test_core_resources_are_isolated_for_regular_members(self) -> None:
        resources = (
            ("campaigns", {"name": "Foreign campaign"}, {"name": "Own campaign"}),
            ("chains", {"name": "Foreign chain"}, {"name": "Own chain"}),
            (
                "templates",
                {"name": "Foreign template", "template_type": "email"},
                {"name": "Own template", "template_type": "email"},
            ),
            ("audiences", {"name": "Foreign audience"}, {"name": "Own audience"}),
        )
        foreign: dict[str, dict[str, object]] = {}
        own: dict[str, dict[str, object]] = {}

        for path, foreign_payload, own_payload in resources:
            foreign_response = self.ca_client.post(
                f"/api/v1/{path}", json=foreign_payload
            )
            own_response = self.member_client.post(f"/api/v1/{path}", json=own_payload)
            self.assertEqual(foreign_response.status_code, 200, foreign_response.text)
            self.assertEqual(own_response.status_code, 200, own_response.text)
            foreign[path] = foreign_response.json()["result"]
            own[path] = own_response.json()["result"]

        def listed_ids(client: TestClient, path: str) -> set[str]:
            response = client.get(f"/api/v1/{path}")
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()["result"]
            items = result.get("items", []) if isinstance(result, dict) else result
            return {str(item["id"]) for item in items}

        for path, _, _ in resources:
            foreign_id = str(foreign[path]["id"])
            own_id = str(own[path]["id"])
            member_ids = listed_ids(self.member_client, path)
            self.assertIn(own_id, member_ids)
            self.assertNotIn(foreign_id, member_ids)

            hidden = self.member_client.get(f"/api/v1/{path}/{foreign_id}")
            self.assertEqual(hidden.status_code, 404, hidden.text)
            forbidden_update = self.member_client.patch(
                f"/api/v1/{path}/{foreign_id}",
                json={"name": "Forbidden update"},
            )
            self.assertEqual(forbidden_update.status_code, 404, forbidden_update.text)

            company_admin_ids = listed_ids(self.ca_client, path)
            self.assertIn(own_id, company_admin_ids)
            self.assertIn(foreign_id, company_admin_ids)

            app_admin_ids = listed_ids(self.admin_client, path)
            self.assertIn(own_id, app_admin_ids)
            self.assertIn(foreign_id, app_admin_ids)

        self.assertIsNone(
            campaign_service.get_campaign(str(foreign["campaigns"]["id"]), self.member)
        )
        with self.assertRaises(ValueError):
            chain_service.load_chain(str(foreign["chains"]["id"]), self.member)
        self.assertIsNone(
            template_service.get_template(str(foreign["templates"]["id"]), self.member)
        )
        self.assertIsNone(
            audience_service.get_audience(str(foreign["audiences"]["id"]), self.member)
        )

    def test_can_view_owned_resource_matrix(self) -> None:
        self.assertTrue(can_view_owned_resource(self.ca_principal, self.member))
        self.assertTrue(can_view_owned_resource(self.ca_principal, self.company_admin))
        self.assertFalse(
            can_view_owned_resource(self.member_principal, self.company_admin)
        )
        self.assertTrue(
            can_view_owned_resource(Principal(self.admin, "root", "admin"), self.member)
        )
        self.assertFalse(can_manage_company(self.member_principal, self.company["id"]))

    def test_visible_owner_usernames(self) -> None:
        self.assertIsNone(
            visible_owner_usernames(Principal(self.admin, "root", "admin"))
        )
        self.assertEqual(
            visible_owner_usernames(self.ca_principal),
            frozenset({self.company_admin, self.member}),
        )
        self.assertEqual(
            visible_owner_usernames(self.member_principal), frozenset({self.member})
        )

    def test_member_cannot_list_companies(self) -> None:
        outsider_app = FastAPI()
        outsider_app.include_router(
            create_companies_router(
                check_auth=lambda: Principal(self.outsider, self.outsider, "user")
            )
        )
        client = TestClient(outsider_app)
        response = client.get("/api/v1/companies")
        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_user_cannot_list_companies(self) -> None:
        def reject_auth() -> None:
            raise HTTPException(status_code=401, detail="Authentication required")

        app = FastAPI()
        app.include_router(create_companies_router(check_auth=reject_auth))
        response = TestClient(app).get("/api/v1/companies")
        self.assertEqual(response.status_code, 401)

    def test_connection_owner_usernames(self) -> None:
        self.assertIsNone(
            connection_owner_usernames(Principal(self.admin, "root", "admin"))
        )
        self.assertEqual(
            connection_owner_usernames(self.ca_principal),
            frozenset({self.company_admin}),
        )
        self.assertEqual(
            connection_owner_usernames(self.member_principal), frozenset({self.member})
        )

    def test_delivery_connections_are_isolated_between_accounts(self) -> None:
        key = Fernet.generate_key().decode("ascii")
        with patch.object(settings, "smtp_credentials_key", key):
            company_admin_mailbox = create_mailbox(
                owner_username=self.company_admin,
                provider="custom",
                email="company-admin@example.test",
                password="company-admin-password",
                host="mailpit",
                port=1025,
                use_ssl=False,
                use_starttls=False,
            )
            member_mailbox = create_mailbox(
                owner_username=self.member,
                provider="custom",
                email="member@example.test",
                password="member-password",
                host="mailpit",
                port=1025,
                use_ssl=False,
                use_starttls=False,
            )

            ca_ids = {
                item["id"]
                for item in self.ca_client.get("/api/v1/connections").json()["result"]
            }
            member_ids = {
                item["id"]
                for item in self.member_client.get("/api/v1/connections").json()[
                    "result"
                ]
            }
            admin_ids = {
                item["id"]
                for item in self.admin_client.get("/api/v1/connections").json()[
                    "result"
                ]
            }

            self.assertEqual(ca_ids, {company_admin_mailbox["id"]})
            self.assertEqual(member_ids, {member_mailbox["id"]})
            self.assertIn(company_admin_mailbox["id"], admin_ids)
            self.assertIn(member_mailbox["id"], admin_ids)
            admin_patch = self.admin_client.patch(
                f"/api/v1/connections/{company_admin_mailbox['id']}",
                json={"sender_name": "Managed by app admin"},
            )
            self.assertEqual(admin_patch.status_code, 200)

            with self.assertRaises(LookupError):
                resolve_smtp_credentials(
                    mailbox_id=company_admin_mailbox["id"],
                    owner_username=self.member,
                )
            with self.assertRaises(LookupError):
                connection_service.resolve_connection(
                    company_admin_mailbox["id"],
                    self.member,
                )

            foreign_patch = self.member_client.patch(
                f"/api/v1/connections/{company_admin_mailbox['id']}",
                json={"sender_name": "Forbidden"},
            )
            foreign_delete = self.member_client.delete(
                f"/api/v1/connections/{company_admin_mailbox['id']}"
            )
            foreign_warmup = self.member_client.get(
                f"/api/v1/connections/{company_admin_mailbox['id']}/sender-warmup"
            )
            self.assertEqual(foreign_patch.status_code, 404)
            self.assertEqual(foreign_delete.status_code, 404)
            self.assertEqual(foreign_warmup.status_code, 404)

    def test_only_app_admin_can_delete_company(self) -> None:
        other = company_service.create_company(name="Disposable Org")
        company_service.add_member(
            other["id"],
            self.outsider,
            role="member",
        )

        forbidden = self.ca_client.delete(f"/api/v1/companies/{other['id']}")
        self.assertEqual(forbidden.status_code, 403)

        deleted = self.admin_client.delete(f"/api/v1/companies/{other['id']}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json()["result"], {"removed": True})
        self.assertIsNone(company_service.get_company(other["id"]))

        surviving_user = get_user_record(self.outsider)
        self.assertIsNotNone(surviving_user)
        self.assertIsNone(surviving_user.company_id)

        missing = self.admin_client.delete(f"/api/v1/companies/{other['id']}")
        self.assertEqual(missing.status_code, 404)

    def test_company_work_types_crud_and_permissions(self) -> None:
        company_id = self.company["id"]
        base = f"/api/v1/companies/{company_id}/work-types"

        listed = self.admin_client.get(base)
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["result"], [])

        created = self.admin_client.post(base, json={"name": "Градостроительный аудит"})
        self.assertEqual(created.status_code, 200, created.text)
        item = created.json()["result"]
        self.assertTrue(item["id"])
        self.assertEqual(item["name"], "Градостроительный аудит")

        duplicate = self.admin_client.post(
            base, json={"name": "градостроительный аудит"}
        )
        self.assertEqual(duplicate.status_code, 400)

        updated = self.admin_client.patch(
            f"{base}/{item['id']}",
            json={"name": "Аудит градостроительной документации"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(
            updated.json()["result"]["name"], "Аудит градостроительной документации"
        )

        listed_after = self.admin_client.get(base)
        self.assertEqual(len(listed_after.json()["result"]), 1)

        deleted = self.admin_client.delete(f"{base}/{item['id']}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(self.admin_client.get(base).json()["result"], [])

        missing = self.admin_client.delete(f"{base}/{item['id']}")
        self.assertEqual(missing.status_code, 404)

        member_forbidden = self.member_client.post(base, json={"name": "СТП МО"})
        self.assertEqual(member_forbidden.status_code, 403)

        ca_created = self.ca_client.post(base, json={"name": "СТП МО"})
        self.assertEqual(ca_created.status_code, 200, ca_created.text)
        ca_item = ca_created.json()["result"]

        ca_updated = self.ca_client.patch(
            f"{base}/{ca_item['id']}",
            json={"name": "Схемы территориального планирования"},
        )
        self.assertEqual(ca_updated.status_code, 200, ca_updated.text)

        ca_deleted = self.ca_client.delete(f"{base}/{ca_item['id']}")
        self.assertEqual(ca_deleted.status_code, 200, ca_deleted.text)


if __name__ == "__main__":
    unittest.main()
