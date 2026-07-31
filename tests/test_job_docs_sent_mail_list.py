from __future__ import annotations

import time
import unittest

from src.infra.db import session_scope
from src.infra.models import Campaign
from src.jobs.job_docs import append_event, list_job_ids_with_sent_mail
from tests.bootstrap import bootstrap_test_runtime


class ListJobIdsWithSentMailTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap_test_runtime(reset_db=True)

    def test_returns_only_jobs_with_sent_mail(self) -> None:
        append_event("job-alpha", "sent_mail_log", {"recipient": "a@example.com"})
        append_event("job-beta", "sent_mail_log", {"recipient": "b@example.com"})
        # A job without sent_mail_log events must not show up.
        append_event("job-gamma", "sender_manager_actions", {"action_type": "call"})

        job_ids = list_job_ids_with_sent_mail()

        self.assertIn("job-alpha", job_ids)
        self.assertIn("job-beta", job_ids)
        self.assertNotIn("job-gamma", job_ids)

    def test_archived_campaign_is_hidden(self) -> None:
        with session_scope() as session:
            session.add_all(
                [
                    Campaign(
                        id="campaign-archived",
                        owner_username="owner",
                        job_id="job-archived",
                        name="Archived",
                        archived=True,
                    ),
                    Campaign(
                        id="campaign-visible",
                        owner_username="owner",
                        job_id="job-visible",
                        name="Visible",
                        archived=False,
                    ),
                ]
            )
        append_event("job-archived", "sent_mail_log", {"recipient": "old@example.com"})
        append_event("job-visible", "sent_mail_log", {"recipient": "new@example.com"})

        job_ids = list_job_ids_with_sent_mail()

        self.assertNotIn("job-archived", job_ids)
        self.assertIn("job-visible", job_ids)

    def test_ordered_by_recent_activity(self) -> None:
        append_event("job-old", "sent_mail_log", {"recipient": "old@example.com"})
        time.sleep(0.01)
        append_event("job-new", "sent_mail_log", {"recipient": "new@example.com"})

        job_ids = list_job_ids_with_sent_mail()

        # job-new was appended last → most recent activity first.
        self.assertLess(job_ids.index("job-new"), job_ids.index("job-old"))


if __name__ == "__main__":
    unittest.main()
