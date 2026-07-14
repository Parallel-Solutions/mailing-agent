from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.bitrix_board.config import BoardConfig, WebhookConfig
from src.bitrix_board.worktree import branch_name, ensure_worktree, worktree_name, worktree_path


def _config_for(repo_root: Path, worktrees_dir: Path) -> BoardConfig:
    return BoardConfig(
        webhook=WebhookConfig(
            base_url="https://example.test/rest/1/token",
            user_id=1,
            origin="https://example.test",
            token="token",
        ),
        poll_interval_seconds=60,
        db_path=repo_root / ".bitrix-board" / "state.db",
        worktrees_dir=worktrees_dir,
        repo_root=repo_root,
        max_review_cycles=3,
        agent_bin="agent",
        default_group_id=None,
        repo_group_map={},
        dispatcher_pid_path=repo_root / ".bitrix-board" / "dispatcher.pid",
    )


class WorktreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name) / "repo"
        self.worktrees_dir = Path(self.tmp.name) / "worktrees"
        self.repo_root.mkdir(parents=True)
        subprocess.run(["git", "init"], cwd=self.repo_root, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.repo_root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=self.repo_root,
            check=True,
            capture_output=True,
        )
        (self.repo_root / "README.md").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo_root, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=self.repo_root,
            check=True,
            capture_output=True,
        )
        self.config = _config_for(self.repo_root, self.worktrees_dir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_worktree_paths(self) -> None:
        self.assertEqual(worktree_name(123), "bitrix-123")
        self.assertEqual(branch_name(123), "ai/bitrix-123")
        self.assertEqual(worktree_path(self.config, 123), self.worktrees_dir / "bitrix-123")

    def test_ensure_worktree_creates_isolated_directory(self) -> None:
        (self.repo_root / ".cursor").mkdir()
        (self.repo_root / ".cursor" / "mcp.json").write_text("{}", encoding="utf-8")

        path, branch = ensure_worktree(self.config, 55)
        self.assertTrue(path.exists())
        self.assertEqual(branch, "ai/bitrix-55")
        self.assertTrue((path / "README.md").exists())
        self.assertTrue((path / ".cursor" / "mcp.json").exists())

        path2, branch2 = ensure_worktree(self.config, 55)
        self.assertEqual(path, path2)
        self.assertEqual(branch, branch2)


if __name__ == "__main__":
    unittest.main()
