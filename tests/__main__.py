from __future__ import annotations

import os
import unittest

from tests.bootstrap import PROJECT_ROOT, bootstrap_test_runtime
from tests.test_api_security_suite import SECURITY_TEST_MODULES

# Loaded only via test_api_security_suite.load_tests — skip in default discover.
_SECURITY_ONLY_MODULES = frozenset(SECURITY_TEST_MODULES)


def _build_suite(loader: unittest.TestLoader) -> unittest.TestSuite:
    suite = unittest.TestSuite()
    tests_dir = PROJECT_ROOT / "tests"
    for path in sorted(tests_dir.glob("test_*.py")):
        module_name = f"tests.{path.stem}"
        if module_name in _SECURITY_ONLY_MODULES:
            continue
        suite.addTests(loader.loadTestsFromName(module_name))
    return suite


def _run_serial() -> int:
    bootstrap_test_runtime()
    loader = unittest.TestLoader()
    suite = _build_suite(loader)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


def _run_parallel(workers: int) -> int:
    """Run tests in parallel by module file (pytest-xdist loadfile).

    Shared Postgres + TRUNCATE in setUp can flake under load; default is serial.
    """
    bootstrap_test_runtime(reset_db=False)
    import pytest

    return pytest.main(
        [
            str(PROJECT_ROOT / "tests"),
            "-q",
            "--tb=short",
            f"-n={workers}",
            "--dist=loadfile",
        ]
    )


def main() -> int:
    raw_workers = os.environ.get("MAILING_AGENT_TEST_WORKERS", "").strip()
    if raw_workers and raw_workers not in {"0", "1"}:
        try:
            workers = max(2, int(raw_workers))
        except ValueError:
            workers = 0
        if workers >= 2:
            return _run_parallel(workers)
    return _run_serial()


if __name__ == "__main__":
    raise SystemExit(main())
