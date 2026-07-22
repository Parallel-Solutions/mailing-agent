"""
test_chat.py — локальная проверка парсера в терминале, без интерфейса.

Запуск (из корня проекта, в окне где заданы ключи $env):
    python test_chat.py

Проверяет две вещи:
  1) discovery напрямую (путь B) — работает БЕЗ обращения к LLM;
  2) chat(message) — как реально ходит веб-слой /api/parser/chat;
     если запрос распознан как коммерческий, соберёт без модели,
     иначе уйдёт в LLM-агента (и упрётся в LLM-ключ, если он не настроен).

Можно передать свой запрос:
    python test_chat.py "Найди аптеки в регионе: Татарстан"
"""
from __future__ import annotations

import sys


def sep(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def test_discovery_direct() -> None:
    """Прямой вызов ядра — гарантированно без LLM."""
    sep("1) DISCOVERY НАПРЯМУЮ (без LLM)")
    from src.parser_new.tools.discovery_tool import discover_companies

    r = discover_companies("строительные компании", "Московская область", limit=5)
    if not r["success"]:
        print("НЕ УДАЛОСЬ:", r.get("error"))
        return
    print(f"Собрано: {len(r['rows'])} организаций\n")
    for i, x in enumerate(r["rows"], 1):
        print(f"{i}. {x['company'][:55]}")
        print(f"   ИНН {x['inn']} | {x['status']} | {x['industry']}")
        print(f"   Адрес: {x['address'][:60]}")
        print(f"   Тел: {x['phone'] or '—'} | Email: {x['email'] or '—'} | Рук.: {x['contact_name'] or '—'}")
        print()


def test_chat(message: str) -> None:
    """Имитация запроса из веб-модалки — через тот же chat(), что и /api/parser/chat."""
    sep(f"2) ЧЕРЕЗ chat() — имитация веб-запроса\n   Запрос: {message!r}")
    try:
        from src.parser.agent import chat
    except Exception as e:
        print("Не удалось импортировать chat():", e)
        return

    try:
        result = chat(message, job_id=None)
    except Exception as e:
        print("chat() упал с ошибкой:")
        print(" ", e)
        print("\n(Если это ошибка про LLM/401/token — значит запрос ушёл в агента,")
        print(" а LLM-ключ на этом окружении не настроен. Путь B сюда не сработал.)")
        return

    print("Ответ агента:", (result.get("reply") or "")[:300])
    print("Успех:", result.get("success"))
    print("Файл результата:", result.get("result_file") or "— (файл не создан)")


def main() -> None:
    # Запрос по умолчанию сформулирован как в модалке (buildPrompt):
    # "Найди <что> в регионе: <где>."
    default_msg = "Найди строительные компании в регионе: Московская область."
    message = sys.argv[1] if len(sys.argv) > 1 else default_msg

    test_discovery_direct()
    test_chat(message)

    sep("ИТОГ")
    print("Если блок 1 отработал — твой сбор (discovery) полностью рабочий.")
    print("Если блок 2 тоже дал файл — путь B (без LLM) ловит веб-запрос корректно.")
    print("Если блок 2 упал на LLM/401 — это ключ модели на окружении, не твой парсер.")


if __name__ == "__main__":
    main()