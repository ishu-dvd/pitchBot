# Security Policy

## Secrets and PII
- Never commit credentials, phone numbers, personal audio, or live transcripts.
- Keep all provider credentials in local environment variables.
- Use `.env` locally; `.env.example` contains placeholders only.

## Side effects
- Telephony and WhatsApp adapters remain disabled unless explicitly enabled.
- External network access should remain off in zero-cost test runs.

## Reporting
If you discover a security issue, open a private security advisory on GitHub.
