# API агента по документам

Встраиваемое Django-приложение поверх текущего генератора документов.

## Где что лежит

- Маршруты Django: `src/kp_document_bot/urls.py`
- Представления Django: `src/kp_document_bot/views.py`
- Сервисный слой и агентная логика: `src/kp_document_bot/services.py`
- Telegram-бот: `src/kp_document_bot/telegram_bot.py`
- Генерация документов: `src/document_builder.py`
- Проверка документа: `src/document_review_agent.py`
- AI-проверка ФИО и логики МО: `src/ai_case_agent.py`

## Что умеет модуль

- генерировать один комплект документов по одной строке
- генерировать пакет по диапазону строк Excel
- проверять уже сгенерированные документы
- проверять загруженный документ
- проверять произвольный текст
- работать как чатовый агент поверх тех же функций

## Подключение в Django

1. Добавить путь `src` в `PYTHONPATH` или скопировать папку `kp_document_bot` в ваш Django-проект.
2. Добавить приложение в `INSTALLED_APPS`:

```python
INSTALLED_APPS = [
    ...,
    "kp_document_bot",
]
```

3. Подключить маршруты:

```python
from django.urls import include, path

urlpatterns = [
    path("api/doc-bot/", include("kp_document_bot.urls")),
]
```

После этого базовый префикс будет:

- `/api/doc-bot/`

## Все эндпоинты

### 1. Проверка доступности

- Метод: `GET`
- Путь: `/api/doc-bot/health/`
- Представление: `src/kp_document_bot/views.py -> health_view`

Ответ:

```json
{
  "status": "ok",
  "service": "kp_document_bot"
}
```

### 2. Генерация одного комплекта по одной строке

- Метод: `POST`
- Путь: `/api/doc-bot/generate/`
- Представление: `src/kp_document_bot/views.py -> generate_view`
- Сервис: `src/kp_document_bot/services.py -> generate_document_package`

Назначение:

- генерирует один комплект документов по одной строке `row`

Тело запроса:

```json
{
  "row": {
    "ID": 1,
    "MUN_NAME": "Городское поселение Энем",
    "MUN_R_NAME": "Тахтамукайского муниципального района",
    "SUB_RF": "Республика Адыгея",
    "ADM_NAME": "АДМИНИСТРАЦИЯ МУНИЦИПАЛЬНОГО ОБРАЗОВАНИЯ \"ЭНЕМСКОЕ ГОРОДСКОЕ ПОСЕЛЕНИЕ\"",
    "HEAD_FIO": "Лаюк Алий Байзетович"
  },
  "outgoing_number": 101,
  "generate_pdf": true,
  "review_final_text": false,
  "review_model": "gpt-4o"
}
```

### 3. Генерация пакета по диапазону строк

- Метод: `POST`
- Путь: `/api/doc-bot/generate-batch/`
- Представление: `src/kp_document_bot/views.py -> generate_batch_view`
- Сервис: `src/kp_document_bot/services.py -> generate_documents_batch`

Назначение:

- генерирует диапазон строк из `data.xlsx`

Тело запроса:

```json
{
  "start_row": 1,
  "end_row": 5,
  "review_final_text": false,
  "review_model": "gpt-4o"
}
```

### 4. Проверка уже сгенерированного пакета

- Метод: `POST`
- Путь: `/api/doc-bot/review-generated/`
- Представление: `src/kp_document_bot/views.py -> review_generated_view`
- Сервис: `src/kp_document_bot/services.py -> review_generated_batch`

Назначение:

- проверяет уже созданные документы по диапазону строк

Тело запроса:

```json
{
  "start_row": 1,
  "end_row": 5
}
```

### 5. Проверка загруженного документа

- Метод: `POST`
- Путь: `/api/doc-bot/review-document/`
- Представление: `src/kp_document_bot/views.py -> review_document_view`
- Сервис: `src/kp_document_bot/services.py -> review_uploaded_document`

Назначение:

- проверяет загруженный файл

Поддерживаемые типы сейчас:

- `.docx`
- `.txt`
- `.md`
- `.csv`
- `.json`

Вариант 1: `multipart/form-data`

- поле `file`
- опционально поле `model`

Вариант 2: JSON

```json
{
  "file_name": "sample.txt",
  "content": "Текст документа для проверки",
  "model": "gpt-4o"
}
```

### 6. Проверка текста

- Метод: `POST`
- Путь: `/api/doc-bot/review-text/`
- Представление: `src/kp_document_bot/views.py -> review_text_view`
- Сервис: `src/kp_document_bot/services.py -> review_text_content`

Назначение:

- проверяет произвольный текст, не файл

Тело запроса:

```json
{
  "text": "Проверь этот текст на ошибки",
  "model": "gpt-4o"
}
```

### 7. Чатовый агент

- Метод: `POST`
- Путь: `/api/doc-bot/chat/`
- Представление: `src/kp_document_bot/views.py -> chat_view`
- Маршрутизатор агента: `src/kp_document_bot/services.py -> handle_agent_message`

Назначение:

- единая агентная точка входа для платформы
- понимает естественные сообщения
- может вызвать генерацию, проверку документов, проверку текста или проверку загруженного файла

Тело запроса:

```json
{
  "message": "сгенерируй 5 документов",
  "session": {},
  "row": null,
  "text": null,
  "outgoing_number": 101,
  "review_final_text": true,
  "review_model": "gpt-4o"
}
```

Если в чат отправляется файл, endpoint также принимает `multipart/form-data`:

- поле `message`
- поле `file`

## Как работает чатовый эндпоинт

`/api/doc-bot/chat/` сначала маршрутизирует намерение пользователя, потом вызывает один из сервисов:

- `generate_batch`
- `review_generated`
- `review_uploaded_document`
- `review_text`
- `generate_documents`
- `answer_question`
- `ask_clarification`

В ответе возвращается:

- `action`
- `reply`
- `payload`
- `session`

`session` нужно сохранять на стороне платформы и передавать обратно в следующий запрос, если нужен “живой” агентный диалог.

## Примеры запросов к чат-агенту

### Сгенерировать пакет

```json
{
  "message": "сгенерируй 5 документов",
  "session": {}
}
```

### Проверить последние документы

```json
{
  "message": "проверь эти документы",
  "session": {
    "last_generated_range": {
      "start": 1,
      "end": 5
    }
  }
}
```

### Проверить текст

```json
{
  "message": "проверь этот текст",
  "session": {},
  "text": "Заказчик передается арбитражный суд..."
}
```

## Telegram-бот

Файл:

- `src/kp_document_bot/telegram_bot.py`

Запуск:

```powershell
cd src
& "..\.venv\Scripts\python.exe" ".\kp_document_bot\telegram_bot.py"
```

Что умеет Telegram-бот:

- генерировать документы
- проверять уже сгенерированные документы
- отправлять архив
- принимать `.docx` и проверять его
- помнить базовый контекст внутри чата

## Что хранится в агентной сессии

Сейчас `session` может содержать, например:

- `profile.user_name`
- `recent_history`
- `last_generated_range`
- `last_uploaded_document`
- `last_review_summary`
- `pending_action`

Для Telegram это дополнительно держится в `context.chat_data` отдельно на каждый `chat_id`.

## Технические замечания

- Это всё ещё MVP без БД и без очередей.
- Для платформы лучше хранить `session` на своей стороне и прокидывать её обратно в `/chat/`.
- Для длинных пакетных операций в будущем лучше выносить задачи в фон.
