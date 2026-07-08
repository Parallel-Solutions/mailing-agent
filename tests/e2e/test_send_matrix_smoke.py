from __future__ import annotations

import os
import unittest

from tests.e2e.run_send_matrix import run_matrix


@unittest.skipUnless(os.environ.get("RUN_REAL_E2E") == "1", "Set RUN_REAL_E2E=1 to run real E2E send matrix")
class SendMatrixSmokeTest(unittest.TestCase):
    def test_full_send_matrix(self) -> None:
        exit_code = run_matrix()
        self.assertEqual(exit_code, 0, "E2E send matrix finished with failures; see tests/e2e/out/e2e_report.json")


if __name__ == "__main__":
    unittest.main()
