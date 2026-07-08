from __future__ import annotations

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


def main() -> int:
    bootstrap_test_runtime()
    loader = unittest.TestLoader()
    suite = _build_suite(loader)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
