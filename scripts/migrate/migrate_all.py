from __future__ import annotations

import json

from scripts.migrate.migrate_auth import migrate_auth
from scripts.migrate.migrate_jobs import migrate_jobs
from scripts.migrate.migrate_parser_memory import migrate_parser_memory


def migrate_all() -> dict:
    return {
        "auth": migrate_auth(),
        "parser_memory": migrate_parser_memory(),
        "jobs": migrate_jobs(),
    }


if __name__ == "__main__":
    print(json.dumps(migrate_all(), ensure_ascii=False, indent=2))
