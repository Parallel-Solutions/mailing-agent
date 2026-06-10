"""
diag_okrug.py — дамп дерева ОКТМО по региону (как его видит наш код, источник classifikators.ru).
Запуск:
    python -m src.parser_new.diag_okrug "Белгородская область"
"""
from __future__ import annotations

import sys

from src.parser_new.tools.oktmo_tool import (
    resolve_region, _fetch, _parse_children, _is_group_container,
)


def main() -> None:
    region = sys.argv[1] if len(sys.argv) > 1 else "Белгородская область"
    info = resolve_region(region)
    print(f"Регион: {info}\n")
    if not info:
        return

    children = [c for c in _parse_children(_fetch(info["url"])) if c["section"] == "1"]
    print(f"Узлов раздела 1 на странице региона: {len(children)}\n")

    okrug_count = 0
    for c in children:
        cont = _is_group_container(c["name"])
        tag = "КОНТЕЙНЕР" if cont else "МО       "
        print(f"[{tag}] {c['name']}  (ОКТМО {c['oktmo']})")
        if "округ" in c["name"].lower():
            okrug_count += 1
        if cont:
            try:
                sub = [x for x in _parse_children(_fetch(c["url"])) if x["section"] == "1"]
                for x in sub:
                    mark = "  ← округ" if "округ" in x["name"].lower() else ""
                    print(f"      - {x['name']}{mark}")
                    if "округ" in x["name"].lower():
                        okrug_count += 1
            except Exception as e:
                print(f"      (не загрузить: {e})")
        print()

    print(f"Итого узлов со словом «округ» (грубо): {okrug_count}")


if __name__ == "__main__":
    main()