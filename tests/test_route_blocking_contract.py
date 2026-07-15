from __future__ import annotations

import ast
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALLOWED_ASYNC_FUNCTIONS = {
    ("parser_router.py", "parser_chat"),
    ("sender_router.py", "read_webhook_json"),
    ("sender_router.py", "unisender_go_webhook_tokenized"),
    ("sender_router.py", "rusender_webhook_tokenized"),
    ("sender_router.py", "mailopost_webhook_tokenized"),
}


class RouteBlockingContractTests(unittest.TestCase):
    def test_only_streaming_routes_remain_async(self) -> None:
        found: set[tuple[str, str]] = set()
        for path in (PROJECT_ROOT / "src" / "web").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.AsyncFunctionDef):
                    found.add((path.name, node.name))

        self.assertEqual(found, ALLOWED_ASYNC_FUNCTIONS)


if __name__ == "__main__":
    unittest.main()
