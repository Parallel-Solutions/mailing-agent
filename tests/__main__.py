from __future__ import annotations

import os

from tests.bootstrap import PROJECT_ROOT, bootstrap_test_runtime


def _run_tests(*, workers: int | None = None) -> int:
    """Run every internal test with pytest.

    The suite stays serial by default because many integration tests share a
    Postgres database and reset it in ``setUp``. External provider tests are a
    separate, explicitly enabled suite because they can send real email.
    """
    runtime = bootstrap_test_runtime(reset_db=workers is None)
    import pytest

    tests_dir = PROJECT_ROOT / "tests"
    args = [
        str(tests_dir),
        "-q",
        "--tb=short",
        f"--ignore={tests_dir / 'external'}",
        f"--junitxml={runtime / 'backend-junit.xml'}",
    ]
    if workers is not None:
        args.extend((f"-n={workers}", "--dist=loadfile"))
    return pytest.main(args)


def main() -> int:
    raw_workers = os.environ.get("MAILING_AGENT_TEST_WORKERS", "").strip()
    if raw_workers and raw_workers not in {"0", "1"}:
        try:
            workers = max(2, int(raw_workers))
        except ValueError:
            workers = 0
        if workers >= 2:
            return _run_tests(workers=workers)
    return _run_tests()


if __name__ == "__main__":
    raise SystemExit(main())
