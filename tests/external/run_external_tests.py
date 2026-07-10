"""Runner for external statistics integration tests.

Discovers and runs all tests/external/test_ext_*.py modules,
then writes JSON + Markdown reports to tests/external/out/.

Usage:
    # Minimum: Level 1 (real provider send only)
    EXT_STATS_ENABLED=1 \\
    E2E_BASE_URL=http://localhost:9806 \\
    E2E_USERNAME=admin \\
    E2E_PASSWORD=... \\
    EXT_JOB_ID=job-... \\
    EXT_TRANSPORT=rusender \\
    RUSENDER_API_KEY=... \\
    python -m tests.external.run_external_tests

    # Full (Level 1-4 with webhook, mailbox, reconciliation):
    EXT_STATS_ENABLED=1 \\
    EXT_PUBLIC_BASE_URL=https://staging.example.com \\
    EXT_RUSENDER_WEBHOOK_TOKEN=... \\
    EXT_IMAP_HOST=imap.mail.ru \\
    EXT_IMAP_USER=test@example.com \\
    EXT_IMAP_PASSWORD=... \\
    ... \\
    python -m tests.external.run_external_tests

    # Run a specific test level only:
    python -m tests.external.run_external_tests --level send
    python -m tests.external.run_external_tests --level webhook
    python -m tests.external.run_external_tests --level mailbox
    python -m tests.external.run_external_tests --level bounce
    python -m tests.external.run_external_tests --level recon

    # Run via pytest (alternative):
    EXT_STATS_ENABLED=1 pytest tests/external/ -v --no-header

Exit codes:
    0  — all enabled tests passed (or were skipped)
    1  — one or more tests failed
    2  — runner error (missing required env, import error, etc.)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import unittest
from pathlib import Path


# ---------------------------------------------------------------------------
# Map --level argument to test module names
# ---------------------------------------------------------------------------

LEVEL_MODULES: dict[str, list[str]] = {
    "send":    ["tests.external.test_ext_send"],
    "webhook": ["tests.external.test_ext_webhook"],
    "mailbox": ["tests.external.test_ext_mailbox"],
    "bounce":  ["tests.external.test_ext_bounce"],
    "recon":   ["tests.external.test_ext_reconciliation"],
    "all": [
        "tests.external.test_ext_send",
        "tests.external.test_ext_webhook",
        "tests.external.test_ext_mailbox",
        "tests.external.test_ext_bounce",
        "tests.external.test_ext_reconciliation",
    ],
}


def _check_enabled() -> None:
    if os.environ.get("EXT_STATS_ENABLED", "").strip() != "1":
        print(
            "ERROR: External statistics tests are disabled.\n"
            "Set EXT_STATS_ENABLED=1 to run them.\n\n"
            "WARNING: These tests send real emails via real providers.\n"
            "Only use test email addresses that belong to you.",
            file=sys.stderr,
        )
        sys.exit(2)


def _load_suite(modules: list[str]) -> unittest.TestSuite:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for module_name in modules:
        try:
            module_suite = loader.loadTestsFromName(module_name)
            suite.addTests(module_suite)
        except Exception as exc:
            print(f"WARNING: Failed to load {module_name}: {exc}", file=sys.stderr)
    return suite


def _write_reports(result: unittest.TestResult, *, level: str, job_id: str, transport: str) -> None:
    """Write JSON + Markdown reports based on unittest result."""
    out_dir = Path(__file__).resolve().parent / "out"

    # Build a simple ExtReport from unittest result
    try:
        from tests.external.report import ExtReport, ExtTestResult, write_json, write_markdown, print_summary

        report = ExtReport(
            job_id=job_id,
            transport=transport,
            public_base_url=os.environ.get("EXT_PUBLIC_BASE_URL", ""),
        )

        for test, _traceback in result.failures:
            report.add(ExtTestResult(
                test_id=str(test),
                provider=transport,
                scenario=str(test._testMethodDoc or test._testMethodName).strip(),
                level=level.upper(),
                status="fail",
                detail=_traceback[-500:] if _traceback else "",
            ))

        for test, _traceback in result.errors:
            report.add(ExtTestResult(
                test_id=str(test),
                provider=transport,
                scenario=str(getattr(test, "_testMethodDoc", "") or "").strip(),
                level=level.upper(),
                status="fail",
                detail=f"ERROR: {_traceback[-300:]}",
            ))

        for test, reason in result.skipped:
            report.add(ExtTestResult(
                test_id=str(test),
                provider=transport,
                scenario=str(getattr(test, "_testMethodDoc", "") or "").strip(),
                level=level.upper(),
                status="skip",
                detail=reason[:200],
            ))

        # Add passed tests (we don't have the list directly in unittest, use total - failures - errors - skipped)
        passed_count = result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)
        if passed_count > 0:
            report.add(ExtTestResult(
                test_id="(passed tests)",
                provider=transport,
                scenario=f"{passed_count} test(s) passed",
                level=level.upper(),
                status="pass",
            ))

        json_path = write_json(report, out_dir)
        md_path = write_markdown(report, out_dir)
        print_summary(report)
        print(f"JSON report: {json_path}")
        print(f"Markdown report: {md_path}")
    except Exception as exc:
        print(f"WARNING: Could not write reports: {exc}", file=sys.stderr)


def run_external_tests(level: str = "all") -> int:
    """Run external tests for the given level. Returns exit code."""
    _check_enabled()

    if level not in LEVEL_MODULES:
        print(f"ERROR: Unknown level {level!r}. Choose from: {list(LEVEL_MODULES)}", file=sys.stderr)
        return 2

    modules = LEVEL_MODULES[level]
    print(f"\n{'='*60}")
    print(f"External Statistics Tests — Level: {level}")
    print(f"Modules: {', '.join(modules)}")
    print(f"{'='*60}\n")

    suite = _load_suite(modules)
    if suite.countTestCases() == 0:
        print("No test cases found. Check EXT_STATS_ENABLED and module names.", file=sys.stderr)
        return 2

    runner = unittest.TextTestRunner(verbosity=2, stream=sys.stdout)
    t0 = time.monotonic()
    result = runner.run(suite)
    elapsed = time.monotonic() - t0

    print(f"\nRan {result.testsRun} test(s) in {elapsed:.1f}s")
    print(f"Failures: {len(result.failures)}, Errors: {len(result.errors)}, Skipped: {len(result.skipped)}")

    job_id = os.environ.get("EXT_JOB_ID", "unknown")
    transport = os.environ.get("EXT_TRANSPORT", os.environ.get("SENDER_TRANSPORT", "rusender"))
    _write_reports(result, level=level, job_id=job_id, transport=transport)

    return 0 if result.wasSuccessful() else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run external statistics integration tests.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--level",
        choices=list(LEVEL_MODULES),
        default="all",
        help="Which test level to run (default: all)",
    )
    args = parser.parse_args()
    sys.exit(run_external_tests(args.level))


if __name__ == "__main__":
    main()
