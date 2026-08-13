from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.__main__ import _run_tests
from tests.bootstrap import PROJECT_ROOT


class TestSuiteRunnerTests(unittest.TestCase):
    def test_serial_runner_uses_pytest_and_excludes_external_suite(self) -> None:
        runtime = PROJECT_ROOT / ".test-runtime"
        with (
            patch("tests.__main__.bootstrap_test_runtime", return_value=runtime) as bootstrap,
            patch("pytest.main", return_value=0) as pytest_main,
        ):
            result = _run_tests()

        self.assertEqual(result, 0)
        bootstrap.assert_called_once_with(reset_db=True)
        args = pytest_main.call_args.args[0]
        self.assertEqual(args[0], str(PROJECT_ROOT / "tests"))
        self.assertIn(f"--ignore={PROJECT_ROOT / 'tests' / 'external'}", args)
        self.assertIn(f"--junitxml={runtime / 'backend-junit.xml'}", args)
        self.assertFalse(any(argument.startswith("-n=") for argument in args))

    def test_parallel_runner_keeps_loadfile_distribution(self) -> None:
        runtime = PROJECT_ROOT / ".test-runtime"
        with (
            patch("tests.__main__.bootstrap_test_runtime", return_value=runtime) as bootstrap,
            patch("pytest.main", return_value=0) as pytest_main,
        ):
            result = _run_tests(workers=3)

        self.assertEqual(result, 0)
        bootstrap.assert_called_once_with(reset_db=False)
        args = pytest_main.call_args.args[0]
        self.assertIn("-n=3", args)
        self.assertIn("--dist=loadfile", args)


if __name__ == "__main__":
    unittest.main()
