from __future__ import annotations

import unittest
import uuid
from io import BytesIO

from cryptography.fernet import Fernet
from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from unittest.mock import patch

from src.security.auth import Principal
from src.security.user_store import create_user
from src.web.v1_router import create_v1_router
from tests.bootstrap import bootstrap_test_runtime


class CampaignV1ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)
        from src.utils.config import settings

        self._key = patch.object(settings, "smtp_credentials_key", Fernet.generate_key().decode("ascii"))
        self._key.start()
        self.addCleanup(self._key.stop)
        self.username = f"c{uuid.uuid4().hex[:8]}"
        create_user(self.username, "Pass12345!")
        app = FastAPI()
        app.include_router(create_v1_router(check_auth=lambda: Principal(self.username, "t1", "user")))
        self.client = TestClient(app)

    def test_create_update_schedule_launch_pause(self) -> None:
        created = self.client.post("/api/v1/campaigns", json={"name": "API Campaign", "mail_subject": "Hello"})
        self.assertEqual(created.status_code, 200)
        campaign_id = created.json()["result"]["id"]

        patched = self.client.patch(
            f"/api/v1/campaigns/{campaign_id}",
            json={"mail_subject": "Hello 2", "document_mode": "kp"},
        )
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.json()["result"]["mail_subject"], "Hello 2")

        recipients = self.client.put(
            f"/api/v1/campaigns/{campaign_id}/recipients",
            json={
                "recipients": [
                    {"company": "A", "contact_name": "A", "email": "a@example.com"},
                    {"company": "B", "contact_name": "B", "email": "b@example.com"},
                ]
            },
        )
        self.assertEqual(recipients.status_code, 200)
        self.assertEqual(recipients.json()["result"]["total"], 2)

        from src.generator.delivery.smtp_mailboxes import create_mailbox

        mailbox = create_mailbox(
            owner_username=self.username,
            provider="custom",
            email="sender@mailpit.local",
            password="x",
            host="mailpit",
            port=1025,
            use_ssl=False,
            use_starttls=False,
            make_default=True,
        )
        self.client.patch(
            f"/api/v1/campaigns/{campaign_id}",
            json={"smtp_mailbox_id": mailbox["id"], "transport": "smtp"},
        )

        schedule = self.client.put(
            f"/api/v1/campaigns/{campaign_id}/schedule",
            json={"send_immediately": True, "batch_size": 1, "interval_seconds": 60},
        )
        self.assertEqual(schedule.status_code, 200)
        self.assertGreaterEqual(schedule.json()["result"]["preview"]["batch_count"], 1)

        blocked_without_template = self.client.post(
            f"/api/v1/campaigns/{campaign_id}/launch?force_now=true"
        )
        self.assertEqual(blocked_without_template.status_code, 400)
        self.assertIn("Выберите шаблон КП", blocked_without_template.text)

        pdf_buffer = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.write(pdf_buffer)
        uploaded = self.client.post(
            "/api/v1/templates/upload",
            data={"template_type": "kp", "name": "KP"},
            files={"file": ("kp.pdf", pdf_buffer.getvalue(), "application/pdf")},
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        kp_template_id = uploaded.json()["result"]["id"]
        self.client.patch(
            f"/api/v1/campaigns/{campaign_id}",
            json={"kp_template_id": kp_template_id},
        )

        prepared = self.client.post(f"/api/v1/campaigns/{campaign_id}/generation/prepare")
        self.assertEqual(prepared.status_code, 200, prepared.text)
        self.assertTrue(prepared.json()["result"]["prepared"])
        self.assertEqual(prepared.json()["result"]["manifest"]["recipient_count"], 2)

        blocked_without_generation = self.client.post(
            f"/api/v1/campaigns/{campaign_id}/launch?force_now=true"
        )
        self.assertEqual(blocked_without_generation.status_code, 400)
        self.assertIn("Сначала сформируйте документы", blocked_without_generation.text)

        with patch(
            "src.campaigns.generation_service.generation_status",
            return_value={"ready": True, "stale": False},
        ):
            launched = self.client.post(f"/api/v1/campaigns/{campaign_id}/launch?force_now=true")
        self.assertEqual(launched.status_code, 200, launched.text)
        self.assertGreaterEqual(len(launched.json()["result"]["batches"]), 1)

        paused = self.client.post(f"/api/v1/campaigns/{campaign_id}/pause")
        self.assertEqual(paused.status_code, 200)
        self.assertEqual(paused.json()["result"]["status"], "paused")


    def test_create_list_and_test_provider_connections(self) -> None:
        rusender = self.client.post(
            "/api/v1/connections",
            json={
                "transport": "rusender",
                "email": "verified@example.com",
                "sender_name": "Sales",
                "api_token": "rs_ck_secret",
            },
        )
        self.assertEqual(rusender.status_code, 200, rusender.text)
        rusender_item = rusender.json()["result"]
        self.assertEqual(rusender_item["transport"], "rusender")
        self.assertNotIn("api_token", rusender_item)
        self.assertNotIn("password_encrypted", rusender_item)

        mailopost = self.client.post(
            "/api/v1/connections",
            json={
                "transport": "mailopost",
                "email": "mailopost@example.com",
                "api_token": "mailopost-secret",
            },
        )
        self.assertEqual(mailopost.status_code, 200, mailopost.text)
        self.assertEqual(mailopost.json()["result"]["transport"], "mailopost")

        listed = self.client.get("/api/v1/connections")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual({item["transport"] for item in listed.json()["result"]}, {"rusender", "mailopost"})
        self.assertTrue(all("api_token" not in item for item in listed.json()["result"]))

        updated = self.client.patch(
            f"/api/v1/connections/{rusender_item['id']}",
            json={
                "transport": "rusender",
                "email": "updated@example.com",
                "sender_name": "Updated Sales",
                "api_token": "",
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["result"]["email"], "updated@example.com")
        self.assertEqual(updated.json()["result"]["sender_name"], "Updated Sales")

        with patch(
            "src.generator.delivery.sender_agent._send_via_rusender",
            return_value={"message_id": "message-1"},
        ) as sender:
            checked = self.client.post(f"/api/v1/connections/{rusender_item['id']}/test")
        self.assertEqual(checked.status_code, 200, checked.text)
        self.assertIn("тестовое письмо", checked.json()["result"]["message"])
        self.assertEqual(sender.call_args.kwargs["credential_api_key"], "rs_ck_secret")
        self.assertEqual(sender.call_args.kwargs["sender_email"], "updated@example.com")

        deleted = self.client.delete(f"/api/v1/connections/{mailopost.json()['result']['id']}")
        self.assertEqual(deleted.status_code, 200, deleted.text)


    def test_mailru_connection_is_verified_and_uses_fixed_settings(self) -> None:
        with patch("src.campaigns.connection_service.verify_smtp_credentials") as verify:
            created = self.client.post(
                "/api/v1/connections",
                json={
                    "transport": "smtp",
                    "provider": "mailru",
                    "email": "Sender@Mail.ru",
                    "sender_name": "Sales",
                    "password": "external-app-password",
                    "host": "untrusted.example",
                    "port": 25,
                },
            )

        self.assertEqual(created.status_code, 200, created.text)
        item = created.json()["result"]
        self.assertEqual(item["provider"], "mailru")
        self.assertEqual(item["email"], "sender@mail.ru")
        self.assertEqual(item["host"], "smtp.mail.ru")
        self.assertEqual(item["port"], 465)
        self.assertTrue(item["use_ssl"])
        self.assertFalse(item["use_starttls"])
        credentials = verify.call_args.args[0]
        self.assertEqual(credentials.smtp_username, "sender@mail.ru")
        self.assertEqual(credentials.host, "smtp.mail.ru")
        self.assertEqual(credentials.port, 465)

        with patch("src.campaigns.connection_service.verify_smtp_credentials") as reverify:
            updated = self.client.patch(
                f"/api/v1/connections/{item['id']}",
                json={
                    "transport": "smtp",
                    "email": "updated@inbox.ru",
                    "password": "new-external-app-password",
                },
            )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["result"]["email"], "updated@inbox.ru")
        self.assertEqual(reverify.call_args.args[0].smtp_username, "updated@inbox.ru")

    def test_mailru_connection_accepts_custom_email_domain(self) -> None:
        with patch("src.campaigns.connection_service.verify_smtp_credentials") as verify:
            response = self.client.post(
                "/api/v1/connections",
                json={
                    "transport": "smtp",
                    "provider": "mailru",
                    "email": "personal.offer@parresh.ru",
                    "password": "external-app-password",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()["result"]
        self.assertEqual(item["email"], "personal.offer@parresh.ru")
        self.assertEqual(item["host"], "smtp.mail.ru")
        self.assertEqual(verify.call_args.args[0].smtp_username, "personal.offer@parresh.ru")

    def test_mailru_connection_does_not_save_invalid_credentials(self) -> None:
        import smtplib

        error = smtplib.SMTPAuthenticationError(535, b"Authentication failed")
        with patch("src.campaigns.connection_service.verify_smtp_credentials", side_effect=error):
            response = self.client.post(
                "/api/v1/connections",
                json={
                    "transport": "smtp",
                    "provider": "mailru",
                    "email": "sender@bk.ru",
                    "password": "wrong-password",
                },
            )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("пароль для внешнего приложения", response.json()["detail"])
        self.assertEqual(self.client.get("/api/v1/connections").json()["result"], [])

    def test_work_types_list_create_and_reject_duplicate(self) -> None:
        listed = self.client.get("/api/v1/work-types")
        self.assertEqual(listed.status_code, 200, listed.text)
        system_items = listed.json()["result"]
        self.assertGreaterEqual(len(system_items), 5)
        self.assertTrue(all(item["mail_subject"] for item in system_items))
        self.assertTrue(all(item["is_system"] for item in system_items))

        created = self.client.post(
            "/api/v1/work-types",
            json={
                "name": "Градостроительный аудит",
                "mail_subject": "Предложение по градостроительному аудиту",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        item = created.json()["result"]
        self.assertFalse(item["is_system"])
        self.assertTrue(item["key"].startswith("custom_"))

        repeated = self.client.get("/api/v1/work-types")
        self.assertIn(item, repeated.json()["result"])
        duplicate = self.client.post(
            "/api/v1/work-types",
            json={"name": "градостроительный аудит", "mail_subject": "Другая тема"},
        )
        self.assertEqual(duplicate.status_code, 400)

    def test_upload_file_template_and_download_active_version(self) -> None:
        source_docx = Document()
        source_docx.add_paragraph("Offer for {{company}} and {{contact_name}}")
        source_payload = BytesIO()
        source_docx.save(source_payload)
        delivery_pdf = b"%PDF-1.4 delivery copy"
        with patch(
            "src.campaigns.template_service._build_kp_pdf_artifact",
            return_value=(delivery_pdf, "offer.pdf"),
        ):
            created = self.client.post(
                "/api/v1/templates/upload",
                data={"template_type": "kp", "name": "Own KP"},
                files={
                    "file": (
                        "offer.docx",
                        source_payload.getvalue(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
            )
        self.assertEqual(created.status_code, 200, created.text)
        template = created.json()["result"]
        self.assertEqual(template["template_type"], "kp")
        self.assertEqual(template["version"]["filename"], "offer.docx")
        self.assertEqual(template["version"]["rendered_pdf_filename"], "offer.pdf")
        self.assertEqual(
            {item["name"] for item in template["version"]["variables"]},
            {"company", "contact_name"},
        )

        source_download = self.client.get(f"/api/v1/templates/{template['id']}/file")
        self.assertEqual(source_download.status_code, 200, source_download.text)
        self.assertEqual(source_download.content, source_payload.getvalue())
        delivery_download = self.client.get(f"/api/v1/templates/{template['id']}/delivery-file")
        self.assertEqual(delivery_download.status_code, 200, delivery_download.text)
        self.assertEqual(delivery_download.content, delivery_pdf)

        replacement_pdf = PdfWriter()
        replacement_pdf.add_blank_page(width=595, height=842)
        replacement = BytesIO()
        replacement_pdf.write(replacement)
        updated = self.client.post(
            "/api/v1/templates/upload",
            data={"template_type": "kp", "template_id": template["id"]},
            files={"file": ("offer-v2.pdf", replacement.getvalue(), "application/pdf")},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        active = updated.json()["result"]
        self.assertEqual(active["version"]["version_number"], 2)
        self.assertEqual(active["version"]["filename"], "offer-v2.pdf")
        self.assertEqual(active["version"]["rendered_pdf_filename"], "offer-v2.pdf")
        downloaded = self.client.get(f"/api/v1/templates/{template['id']}/file")
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        self.assertEqual(downloaded.content, replacement.getvalue())
        preview = self.client.get(f"/api/v1/templates/{template['id']}/preview-file")
        self.assertEqual(preview.content, replacement.getvalue())

        document = Document()
        document.add_paragraph("Contract for {{company}} and {{contact_name}}")
        document_payload = BytesIO()
        document.save(document_payload)
        contract = self.client.post(
            "/api/v1/templates/upload",
            data={"template_type": "contract", "name": "Own document"},
            files={
                "file": (
                    "contract.docx",
                    document_payload.getvalue(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        self.assertEqual(contract.status_code, 200, contract.text)
        contract_template = contract.json()["result"]
        self.assertEqual(contract_template["version"]["filename"], "contract.docx")
        self.assertIsNone(contract_template["version"]["rendered_pdf_filename"])

if __name__ == "__main__":
    unittest.main()
