# MCP-Сервер Mailing Agent

Это опциональная MCP-обертка над инструментами агента.

## Инструменты

- `get_agent_report(job_id)` возвращает единый человекочитаемый отчет агента.
- `get_agent_memory_candidates(job_id)` возвращает кандидаты для обучения памяти.
- `get_agent_quarantine(job_id)` возвращает рискованные решения, оставленные на
  проверку.
- `preview_inflection(row)` возвращает поля склонения и трассировку для одной
  строки.
- `approve_inflection_override(entity_type, source_value, target_case, result_value)`
  записывает доверенное исключение склонения.

## Запуск

Установить опциональный пакет MCP в окружение проекта и выполнить:

```bash
python -m src.generator.mcp_server
```

Основной FastAPI-сервис не зависит от этого сервера. Если пакет MCP не
установлен, остальная часть mailing-agent продолжит работать нормально.
