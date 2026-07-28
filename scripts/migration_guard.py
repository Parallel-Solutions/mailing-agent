"""Reject deployments whose database migration is ahead of or outside the repo graph."""

from __future__ import annotations

import argparse
import ast
from collections import deque
from pathlib import Path


class MigrationCompatibilityError(RuntimeError):
    """Raised when a database revision cannot be upgraded by this checkout."""


def _literal_assignment(tree: ast.Module, name: str):
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        return ast.literal_eval(node.value)
    return None


def load_revision_graph(versions_dir: Path) -> dict[str, tuple[str, ...]]:
    graph: dict[str, tuple[str, ...]] = {}
    for path in sorted(versions_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        revision = _literal_assignment(tree, "revision")
        if not isinstance(revision, str) or not revision:
            continue
        raw_down_revision = _literal_assignment(tree, "down_revision")
        if raw_down_revision is None:
            parents: tuple[str, ...] = ()
        elif isinstance(raw_down_revision, str):
            parents = (raw_down_revision,)
        elif isinstance(raw_down_revision, (tuple, list)):
            parents = tuple(str(value) for value in raw_down_revision)
        else:
            raise MigrationCompatibilityError(
                f"Unsupported down_revision in {path}: {raw_down_revision!r}"
            )
        graph[revision] = parents
    if not graph:
        raise MigrationCompatibilityError(f"No Alembic revisions found in {versions_dir}.")
    return graph


def migration_heads(graph: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    referenced = {parent for parents in graph.values() for parent in parents}
    return tuple(sorted(revision for revision in graph if revision not in referenced))


def assert_database_revision_is_upgradeable(
    database_revision: str,
    *,
    versions_dir: Path,
) -> tuple[str, ...]:
    revision = str(database_revision or "").strip()
    if not revision:
        raise MigrationCompatibilityError("Database Alembic revision is empty.")

    graph = load_revision_graph(versions_dir)
    heads = migration_heads(graph)
    if len(heads) != 1:
        raise MigrationCompatibilityError(
            f"Expected one Alembic head, found {list(heads)}."
        )
    if revision not in graph:
        raise MigrationCompatibilityError(
            f"Database revision {revision!r} is not present in this checkout. "
            f"Refusing a potentially older or divergent deployment; repo head is {heads[0]!r}."
        )

    reachable: set[str] = set()
    queue = deque(heads)
    while queue:
        current = queue.popleft()
        if current in reachable:
            continue
        reachable.add(current)
        queue.extend(graph.get(current, ()))
    if revision not in reachable:
        raise MigrationCompatibilityError(
            f"Database revision {revision!r} is not an ancestor of repo head {heads[0]!r}."
        )
    return heads


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-revision", required=True)
    parser.add_argument(
        "--versions-dir",
        type=Path,
        default=Path("migrations/versions"),
    )
    args = parser.parse_args()
    try:
        heads = assert_database_revision_is_upgradeable(
            args.database_revision,
            versions_dir=args.versions_dir,
        )
    except MigrationCompatibilityError as exc:
        parser.error(str(exc))
    print(
        "Migration compatibility OK: "
        f"database={args.database_revision}, repo_head={heads[0]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
