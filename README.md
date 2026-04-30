# SunoCoach

AI music creation workflow coach that lives inside Claude. Community-validated patterns for Suno and any AI music generator. Style prompt engineering, lyric structure tagging, client profile management, and self-updating pattern detection.

## Connect to Claude

Go to **claude.ai → Settings → Connectors → Add Custom Connector** → paste:

```
https://sunocoach.j0b3.workers.dev
```

OAuth handles auth automatically — no API keys to copy-paste.

## Tiers

| Tier | Price | Sessions | Features |
|------|-------|----------|----------|
| **Free** | $0 | 10/month | Read-only workflow, basic style prompts |
| **Contributor** | $0 | Unlimited | Submit + vote on workflow patterns (email verify only) |
| **Pro** | $9/mo | Unlimited | Client profiles, priority patterns, email support |

[Upgrade to Pro](https://sunocoach.j0b3.workers.dev/billing/checkout)

## Screenshots

*(Add screenshots here after first deployment)*

## Development

```bash
docker-compose up -d
# App runs at http://localhost:8000
# Health check: http://localhost:8000/health
```

## Deploy

```bash
./deploy.sh
```

## License

MIT
