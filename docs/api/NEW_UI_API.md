# New UI API (`/api/v1`)

All responses: `{ "status": "ok", "result": ... }` unless error (`4xx/5xx` with `detail`).

Auth: session cookie (same as `/api/auth/*`).

## Profile

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/profile` | Get/create profile |
| PATCH | `/api/v1/profile` | Update profile fields |

## Campaigns

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/campaigns` | List (`status`, `q`, `limit`, `offset`) |
| POST | `/api/v1/campaigns` | Create draft |
| GET | `/api/v1/campaigns/{id}` | Get |
| PATCH | `/api/v1/campaigns/{id}` | Update / autosave draft |
| POST | `/api/v1/campaigns/{id}/duplicate` | Duplicate |
| POST | `/api/v1/campaigns/{id}/archive` | Archive |
| GET | `/api/v1/campaigns/active-sending` | Dashboard active sending block |
| GET | `/api/v1/campaigns/{id}/recipients` | Paginated recipients |
| PUT | `/api/v1/campaigns/{id}/recipients` | Replace recipients |
| PATCH | `/api/v1/campaigns/{id}/recipients/{rid}` | Update one |
| POST | `/api/v1/campaigns/{id}/recipients/delete` | Bulk delete |
| POST | `/api/v1/campaigns/{id}/recipients/import` | CSV/XLSX upload |
| GET/PUT | `/api/v1/campaigns/{id}/schedule` | Get/update schedule |
| POST | `/api/v1/schedule/preview` | Preview batches without save |
| GET | `/api/v1/campaigns/{id}/validate` | Pre-launch validation |
| POST | `/api/v1/campaigns/{id}/launch` | Create batches + enqueue |
| POST | `/api/v1/campaigns/{id}/pause` | Pause |
| POST | `/api/v1/campaigns/{id}/resume` | Resume |
| POST | `/api/v1/campaigns/{id}/cancel` | Cancel |
| GET | `/api/v1/campaigns/{id}/batches` | Queue tab |
| POST | `/api/v1/campaigns/{id}/batches/{bid}/cancel` | Cancel future batch |
| POST | `/api/v1/campaigns/{id}/test-email` | Send test via Mailpit/SMTP |

## Templates

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/api/v1/templates` | List / create |
| GET/PATCH | `/api/v1/templates/{id}` | Get / save new version |
| POST | `/api/v1/templates/{id}/duplicate` | Duplicate |
| POST | `/api/v1/templates/{id}/archive` | Archive |
| GET | `/api/v1/templates/{id}/versions` | Version history |
| POST | `/api/v1/templates/{id}/preview` | Render sample |

## Audiences

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/api/v1/audiences` | List / create |
| GET/PATCH | `/api/v1/audiences/{id}` | Get / rename |
| POST | `/api/v1/audiences/{id}/duplicate` | Duplicate |
| GET/PUT | `/api/v1/audiences/{id}/members` | Members |
| POST | `/api/v1/audiences/{id}/import` | Import file |
| POST | `/api/v1/audiences/{id}/use-in-campaign/{cid}` | Copy into campaign |

## Existing APIs (unchanged)

- Auth: `/api/auth/*`
- SMTP: `/api/smtp/*`
- Statistics: `/api/sender/manager-dashboard`, campaigns, recipients, consents, …
- Consent pages: `/consent/request/{token}`, `/consent/confirm/{token}`
