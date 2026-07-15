# Gap-анализ дизайна Stitch и текущего Mailing Agent

Ветка: `new-design`

## Решение по интеграции

Редактор из Stitch встраивается в существующий сервис как верхнеуровневый раздел **«Шаблоны»**. Он не заменяет этапы сбора аудитории, подготовки документов, отправки и статистики. Текущий экран «Настройки» становится экраном настройки рассылки и выбора уже подготовленных версий шаблонов.

Целевой путь:

`Шаблоны → Настройки рассылки → Аудитория → Документы → Отправка → Результаты`

Короткий путь остаётся доступным: из «Настроек рассылки» можно загрузить новый файл и сразу перейти в редактор его версии.

## Что есть в сервисе сейчас

### Интерфейс

- SPA собрана в `templates/index.html`.
- Основная навигация: `settings`, `parser`, `generator`, `sender`, `status`, `statistics`.
- Загрузка шаблонов письма, КП и договора находится в «Настройках».
- Анализ шаблона и PDF-предпросмотр уже имеют модальное окно, но gate отключён ранним `return true` в `confirmTemplateAnalysisBeforeDocumentsStart()`.
- Компоненты и стили редактора как отдельная система отсутствуют; большая часть UI и CSS находится в одном HTML-файле.

### Backend и хранение

- `POST /api/upload/template` принимает `mail`, `kp`, `contract`.
- Для `kp` adaptive engine компилирует DOCX/PDF/HTML, находит placeholders, создаёт версию и ставит сертификацию в очередь.
- `GET /api/templates/adaptive/status` возвращает последнюю, активную и все adaptive-версии.
- `POST /api/templates/adaptive/{template_id}/activate` активирует только версию с успешной сертификацией.
- `GET /api/documents/template-analysis` собирает сведения о шаблонах, полях и входных строках.
- `POST /api/documents/template-preview` формирует тестовые DOCX/PDF для выбранной строки.
- `GET /api/documents/template-preview/file` отдаёт результат предпросмотра.
- `AdaptiveTemplateStore` уже реализует immutable-каталог версий, `latest`, `active` и fail-closed активацию.
- `TemplatePackage` хранит исходник, формат, checksum, найденные поля, occurrences, capabilities и warnings.
- Данные организаций уже доступны в `clients.data` и в `data.xlsx`, но каталога источников и стабильной схемы полей пока нет.

## Матрица экранов Stitch

Обозначения:

- **Переиспользуем** — существующий контракт подходит без изменения смысла.
- **Адаптируем** — логика есть, но нужен новый UI или расширение ответа.
- **Разрабатываем** — сущности и контракта в сервисе нет.

| Экран Stitch | Решение | Текущая опора | Разрыв |
| --- | --- | --- | --- |
| Загрузка и генерация AI | Адаптируем | `/api/upload/template`, compile task, template analysis | Единый стартовый экран и библиотека; AI-создание шаблона — позже |
| Редактор: коммерческое предложение | Разрабатываем поверх adaptive engine | `TemplatePackage`, occurrences, renderer | Нет draft-модели, документа редактора, команд редактирования и autosave |
| Редактор: текст письма | Разрабатываем | TXT/DOCX upload, `_render_mail_template`, subject template | Нет структурированного HTML/body editor и безопасной модели блоков |
| Редактор: динамические поля и AI | Разрабатываем | placeholders и значения строки | Нет `TemplateField`, режима заполнения, provenance, AI-конфигурации и ручного override |
| Выбор поля внешнего сервиса | Разрабатываем | `clients.data`, колонки `data.xlsx` | Нет `DataSource`, `SourceSchema`, каталога полей и mapping API |
| Сравнение и обновление данных | Разрабатываем | повторная генерация job | Нет снимка предыдущего значения, diff и правил keep/replace/merge |
| Предпросмотр и проверка | Адаптируем | analysis, preview DOCX/PDF, certification | Нужно объединить проверки, фильтры проблем, preview modes и подтверждение |
| Pro-Enterprise Design System | Адаптируем | существующие CSS primitives | Нужны токены, изолированные компоненты и responsive-сетка редактора |

## Сопоставление сущностей

| Целевая сущность | Что уже есть | Решение для MVP |
| --- | --- | --- |
| `Template` | Файл конкретного типа внутри job | Ввести логическую карточку шаблона: id, tenant, kind, name |
| `TemplateVersion` | `TemplatePackage` + version directory | Использовать package как render-артефакт; добавить metadata/draft рядом с manifest |
| `TemplateField` | `fields` и `occurrences` | Добавить descriptor: label, type, format, required, default, fill mode |
| `TemplateValidation` | `CertificationResult` | Сохранить certification как техническую проверку; UI агрегирует её с mapping issues |
| `TemplatePreview` | временные DOCX/PDF | Переиспользовать API, расширить выбором template/version и preview mode |
| `DataSource` | `clients.data` и `data.xlsx` | В MVP один системный источник `mailing_client`; внешние CRM не симулировать |
| `FieldMapping` | не существует | Добавить явное сопоставление template field → source path |
| `AiFieldConfig` | не существует | Не включать в первый вертикальный срез; заложить версионируемую схему |
| `FieldValueState` | не существует | Добавить после базового mapping; ручные overrides должны быть отдельным слоем |
| `CampaignTemplateBinding` | job-копия файлов | Сначала сохранять выбранные version ids в job document; затем pin на время запуска |

## Контракты, которые переиспользуем

### Загрузка

`POST /api/upload/template`

- оставить поддержку существующих форматов;
- оставить авторизацию и аудит;
- оставить compile/certification pipeline для КП;
- в ответе уже достаточно данных, чтобы открыть созданную версию;
- не менять старые имена файлов до миграции генератора.

### Версии и активация

`GET /api/templates/adaptive/status`

- использовать для первой библиотеки версий КП;
- показывать `latest`, `active`, certification status, warnings и найденные поля.

`POST /api/templates/adaptive/{template_id}/activate`

- оставить проверку `certification.status == passed`;
- не разрешать UI активировать pending/failed;
- не заменять рабочую активную версию при ошибке новой.

### Анализ и предпросмотр

`GET /api/documents/template-analysis`

- использовать для сводки «что найдено»;
- перестать прятать уже существующий review-сценарий;
- позднее расширить проблемами mapping.

`POST /api/documents/template-preview`

- использовать как первый реальный preview;
- в MVP preview строится на первой или выбранной организации;
- позже добавить явные `template_id`/`version_id` и режимы final/variables/sources.

## Новые backend-возможности

### Первый вертикальный срез

1. Унифицированный read-model библиотеки для `mail`, `kp`, `contract`.
2. Получение одной версии с manifest, certification и найденными полями.
3. Metadata версии: отображаемое имя, статус draft/ready/active, modified_at.
4. Job binding: выбранная версия письма, КП и договора.
5. Preview конкретной версии до активации.

Предлагаемые API:

```text
GET    /api/templates
GET    /api/templates/{template_id}
GET    /api/templates/{template_id}/versions/{version_id}
PATCH  /api/templates/{template_id}/versions/{version_id}
POST   /api/templates/{template_id}/versions/{version_id}/preview
POST   /api/templates/{template_id}/versions/{version_id}/activate
GET    /api/jobs/{job_id}/template-bindings
PUT    /api/jobs/{job_id}/template-bindings
```

Старые endpoints остаются рабочими и могут быть внутренней реализацией новых фасадов.

### Следующий срез

```text
GET    /api/template-sources
GET    /api/template-sources/{source_id}/schema
PUT    /api/templates/{template_id}/versions/{version_id}/fields/{field_name}
POST   /api/templates/{template_id}/versions/{version_id}/resolve
GET    /api/templates/{template_id}/versions/{version_id}/issues
```

## Правила информационной архитектуры

### Глобальный раздел «Шаблоны»

Содержит библиотеку, создание/загрузку, редактор, поля, preview и историю версий. Пользователь работает здесь независимо от конкретной рассылки.

### Экран «Настройки рассылки»

Содержит:

- название/параметры рассылки;
- тему письма, если она не закреплена в версии;
- выбор активных версий письма, КП и договора;
- ссылку «Редактировать шаблон»;
- быстрый upload как shortcut.

Он не должен содержать второй редактор или отдельный список версий.

### Экран «Документы»

Содержит:

- выбранные версии только для чтения;
- preview на данных текущей организации;
- сводку заполнения и проблем;
- запуск массовой генерации.

## Ограничения MVP

- Настоящие Bitrix24/amoCRM интеграции в коде отсутствуют, поэтому их нельзя показывать как работающие источники.
- Email сейчас отправляется как plain text; визуальные кнопки и блоки из Stitch требуют отдельного безопасного HTML-email renderer.
- Adaptive versioning реализован только для КП. Письмо и договор сначала будут представлены в унифицированной библиотеке через compatibility-обёртку.
- Визуальное редактирование DOCX/PDF нельзя обещать как pixel-perfect round-trip. В MVP редактируются metadata, поля и mappings; исходный документ остаётся каноническим.
- AI-генерация шаблона и AI-полей идёт после стабильных версий, mapping и preview.

## Порядок реализации после анализа

1. Вынести дизайн-токены и стили нового раздела из монолитного `index.html`.
2. Добавить пункт «Шаблоны» и пустой screen container без изменения текущего workflow.
3. Подключить реальную библиотеку КП к adaptive status.
4. Подключить upload и polling certification.
5. Открывать детали версии, найденные поля, warnings и preview.
6. Добавить выбор/привязку версии к job.
7. Только после этого вводить field mapping, provenance, AI и ручные overrides.

## Критерий завершения первого среза

Пользователь может открыть «Шаблоны», увидеть реальные версии КП, загрузить новую, дождаться проверки, посмотреть найденные поля и preview, активировать прошедшую сертификацию и выбрать её в текущей рассылке. Старый путь загрузки и существующая генерация продолжают работать.
