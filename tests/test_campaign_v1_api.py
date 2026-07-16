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

        launched = self.client.post(f"/api/v1/campaigns/{campaign_id}/launch?force_now=true")
        self.assertEqual(launched.status_code, 200, launched.text)
        self.assertGreaterEqual(len(launched.json()["result"]["batches"]), 1)

        paused = self.client.post(f"/api/v1/campaigns/{campaign_id}/pause")
        self.assertEqual(paused.status_code, 200)
        self.assertEqual(paused.json()["result"]["status"], "paused")

    def test_replace_recipients_twice_does_not_500(self) -> None:
        created = self.client.post("/api/v1/campaigns", json={"name": "Reimport Campaign"})
        self.assertEqual(created.status_code, 200)
        campaign_id = created.json()["result"]["id"]
        payload = {
            "recipients": [
                {"company": "A", "contact_name": "A", "email": "a@example.com"},
                {"company": "B", "contact_name": "B", "email": "b@example.com"},
            ]
        }

        first = self.client.put(f"/api/v1/campaigns/{campaign_id}/recipients", json=payload)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["result"]["total"], 2)

        second = self.client.put(f"/api/v1/campaigns/{campaign_id}/recipients", json=payload)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["result"]["total"], 2)

        csv_body = "company,contact_name,email\nC,C,c@example.com\nD,D,d@example.com\n"
        imported = self.client.post(
            f"/api/v1/campaigns/{campaign_id}/recipients/import",
            files={"file": ("recipients.csv", csv_body.encode("utf-8"), "text/csv")},
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(imported.json()["result"]["import"]["total"], 2)

        reimported = self.client.post(
            f"/api/v1/campaigns/{campaign_id}/recipients/import",
            files={"file": ("recipients.csv", csv_body.encode("utf-8"), "text/csv")},
        )
        self.assertEqual(reimported.status_code, 200, reimported.text)
        self.assertEqual(reimported.json()["result"]["import"]["total"], 2)

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
                data={"template_type": "document", "name": "Own document"},
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
        self.assertEqual(template["template_type"], "document")
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

        legacy = self.client.post(
            "/api/v1/templates/upload",
            data={"template_type": "kp", "name": "Legacy alias"},
            files={
                "file": (
                    "legacy.docx",
                    source_payload.getvalue(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        self.assertEqual(legacy.status_code, 200, legacy.text)
        self.assertEqual(legacy.json()["result"]["template_type"], "document")

        replacement_pdf = PdfWriter()
        replacement_pdf.add_blank_page(width=595, height=842)
        replacement = BytesIO()
        replacement_pdf.write(replacement)
        updated = self.client.post(
            "/api/v1/templates/upload",
            data={"template_type": "document", "template_id": template["id"]},
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
        self.assertEqual(contract_template["template_type"], "document")
        self.assertEqual(contract_template["version"]["filename"], "contract.docx")
        self.assertIsNone(contract_template["version"]["rendered_pdf_filename"])

    def test_template_starters_and_models(self) -> None:
        models = self.client.get("/api/v1/templates/models")
        self.assertEqual(models.status_code, 200, models.text)
        model_ids = {item["id"] for item in models.json()["result"]}
        self.assertIn("gpt-4o-mini", model_ids)

        starters = self.client.get("/api/v1/templates/starters", params={"template_type": "email"})
        self.assertEqual(starters.status_code, 200, starters.text)
        email_starters = starters.json()["result"]
        self.assertGreaterEqual(len(email_starters), 3)
        used = self.client.post(f"/api/v1/templates/starters/{email_starters[0]['id']}/use")
        self.assertEqual(used.status_code, 200, used.text)
        self.assertEqual(used.json()["result"]["template_type"], "email")

        doc_starters = self.client.get("/api/v1/templates/starters", params={"template_type": "document"})
        self.assertEqual(doc_starters.status_code, 200, doc_starters.text)
        doc_used = self.client.post(f"/api/v1/templates/starters/{doc_starters.json()['result'][0]['id']}/use")
        self.assertEqual(doc_used.status_code, 200, doc_used.text)
        self.assertEqual(doc_used.json()["result"]["template_type"], "document")

    def test_generate_template_files_only_and_ai(self) -> None:
        html = b"<p>Hello {{contact_name}} from {{company}}</p>"
        created = self.client.post(
            "/api/v1/templates/generate",
            data={"template_type": "email", "prompt": ""},
            files={"files": ("mail.html", html, "text/html")},
        )
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(created.json()["result"]["template_type"], "email")
        self.assertIn("{{company}}", created.json()["result"]["version"]["body_html"])

        with patch("src.campaigns.template_ai._call_llm") as mock_llm:
            mock_llm.return_value = {
                "name": "AI Letter",
                "subject": "Hi {{company}}",
                "body_html": "<p>Hello {{contact_name}}</p>",
            }
            ai = self.client.post(
                "/api/v1/templates/generate",
                data={
                    "template_type": "email",
                    "prompt": "Сделай короткое письмо",
                    "model": "gpt-4o-mini",
                },
            )
        self.assertEqual(ai.status_code, 200, ai.text)
        self.assertEqual(ai.json()["result"]["name"], "AI Letter")
        mock_llm.assert_called_once()

    def test_generate_template_llm_failure_returns_503(self) -> None:
        with patch("src.campaigns.template_ai._build_client") as mock_client:
            mock_client.return_value.chat.completions.create.side_effect = RuntimeError(
                "Error code: 400 - no healthy deployments for this model"
            )
            response = self.client.post(
                "/api/v1/templates/generate",
                data={
                    "template_type": "email",
                    "prompt": "Сделай короткое письмо",
                    "model": "gpt-4o-mini",
                },
            )
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("недоступна", response.json()["detail"].lower())

if __name__ == "__main__":
    unittest.main()
