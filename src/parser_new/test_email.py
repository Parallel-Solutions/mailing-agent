"""
diag_rayon.py — как classinform раскладывает ветку «Муниципальные районы»:
контейнер районов → район → его дети (поселения / под-контейнеры).
Запуск:
    python -m src.parser_new.diag_rayon
"""
from __future__ import annotations

from src.parser_new.tools.oktmo_tool import _children

# контейнер «Муниципальные районы Краснодарского края»
RAYON_CONTAINER = "https://classinform.ru/oktmo/03600000000.html"


def main() -> None:
    print("=== Дети контейнера районов ===")
    rayons = _children(RAYON_CONTAINER)
    print(f"районов: {len(rayons)} (первые 6):")
    for r in rayons[:6]:
        print(f"  {r['oktmo']}  {r['name']}  | центр: {r['center']}")

    if not rayons:
        print("Пусто — проверь URL контейнера.")
        return

    first = rayons[0]
    print(f"\n=== Дети первого района: «{first['name']}» ===")
    print(f"    URL: {first['url']}")
    kids = _children(first["url"])
    print(f"    детей: {len(kids)}")
    for k in kids:
        print(f"      {k['oktmo']}  {k['name']}  | центр: {k['center']}")

    # если у района есть под-контейнеры (поселения) — заглянем ещё на уровень в первый
    sub = [k for k in kids if "поселени" in k["name"].lower()
           or "сельсовет" in k["name"].lower()]
    if sub and sub[0]["url"] != first["url"]:
        s = sub[0]
        print(f"\n=== Дети под-узла «{s['name']}» ===")
        for x in _children(s["url"]):
            print(f"      {x['oktmo']}  {x['name']}  | центр: {x['center']}")


if __name__ == "__main__":
    main()