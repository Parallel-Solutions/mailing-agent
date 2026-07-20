# Manual testing access

## Addresses

- Application (React CampaignFlow): http://localhost:9806
- Mailpit UI: http://localhost:8025

## Credentials

- Login: `demo`
- Password: `demo-pass-123`

## Commands

```powershell
.\scripts\dev.ps1 start
.\scripts\dev.ps1 reset
.\scripts\dev.ps1 stop
.\scripts\qa.ps1 full
```

## Seed entities

After start/seed:

- Mailpit SMTP connection (`sender@mailpit.local`)
- Backup SMTP connection
- Email / KP / contract templates
- Audiences: «Демо аудитория», «Регион Центр»
- Campaigns: draft, scheduled, completed, completed_with_errors, running (with queue batches)

## Fixture files

- `fixtures/manual/recipients-valid.xlsx`
- `fixtures/manual/recipients-valid.csv`
- `fixtures/manual/recipients-with-errors.xlsx`
- `fixtures/manual/recipients-with-duplicates.xlsx`
