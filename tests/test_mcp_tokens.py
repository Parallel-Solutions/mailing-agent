from __future__ import annotations

import unittest

from src.security.mcp_tokens import (
    extract_bearer_token,
    parse_mcp_tokens,
    resolve_mcp_token_username,
)


class McpTokensTests(unittest.TestCase):
    def test_parse_mcp_tokens(self) -> None:
        self.assertEqual(parse_mcp_tokens('{"tok-a":"alice","tok-b":"bob"}'), {"tok-a": "alice", "tok-b": "bob"})
        self.assertEqual(parse_mcp_tokens(""), {})
        self.assertEqual(parse_mcp_tokens("not-json"), {})
        self.assertEqual(parse_mcp_tokens('["x"]'), {})

    def test_extract_bearer_token(self) -> None:
        self.assertEqual(extract_bearer_token("Bearer abc123"), "abc123")
        self.assertEqual(extract_bearer_token("bearer abc123"), "abc123")
        self.assertIsNone(extract_bearer_token("Basic abc123"))
        self.assertIsNone(extract_bearer_token(""))
        self.assertIsNone(extract_bearer_token("Bearer "))

    def test_resolve_mcp_token_username(self) -> None:
        mapping = {"secret-token": "demo"}
        self.assertEqual(resolve_mcp_token_username("secret-token", mapping), "demo")
        self.assertIsNone(resolve_mcp_token_username("wrong", mapping))
        self.assertIsNone(resolve_mcp_token_username("", mapping))


if __name__ == "__main__":
    unittest.main()
