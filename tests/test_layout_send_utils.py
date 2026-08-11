from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.campaigns.layout_send_utils import clear_kp_layout_failure


class LayoutSendUtilsTests(unittest.TestCase):
    def test_successful_retry_clears_previous_layout_error_code(self) -> None:
        recipient = SimpleNamespace(
            extra={"layout_error_code": "kp_font_compact", "other": "kept"}
        )

        clear_kp_layout_failure(recipient)

        self.assertEqual(recipient.extra, {"other": "kept"})


if __name__ == "__main__":
    unittest.main()
