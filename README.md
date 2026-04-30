# SunoCoach

AI music creation workflow coach that lives inside Claude. Community-validated patterns for Suno and any AI music generator. Style prompt engineering, lyric structure tagging, client profile management, and self-updating pattern detection.

## Stack

- **Backend:** FastAPI + Uvicorn (Python 3.11)
- **Database:** PostgreSQL via asyncpg (Supabase in production)
- **Cache:** In-memory (Redis optional)
- **Auth:** OAuth 2.0
- **Billing:** Stripe Checkout + Webhooks
- **Deploy:** Docker → Render/Railway/Fly.io

## Connect to Claude

Go to **claude.ai → Settings → Connectors → Add Custom Connector** → paste your deployed URL:

```
https://YOUR_APP_URL
```

OAuth handles auth automatically — no API keys to copy-paste.

## Tiers

| Tier | Price | Sessions | Features |
|------|-------|----------|----------|
| **Free** | $0 | 10/month | Read-only workflow, basic style prompts |
| **Contributor** | $0 | Unlimited | Submit + vote on workflow patterns (email verify only) |
| **Pro** | $9/mo | Unlimited | Client profiles, priority patterns, email support |

[Upgrade to Pro](https://YOUR_APP_URL/billing/checkout)

## Development

```bash
# Local dev with Docker Compose
docker-compose up -d

# App runs at http://localhost:8000
# Health check: http://localhost:8000/health
```

## Deploy

### Option 1: Render (Free tier, cold starts)
1. Connect GitHub repo to [render.com](https://render.com)
2. Select "Web Service" → point to your repo
3. Set environment variables in Render dashboard:
   - `DATABASE_URL` (Supabase connection pooler)
   - `STRIPE_SECRET_KEY`
   - `STRIPE_WEBHOOK_SECRET`
   - `STRIPE_PRICE_ID_PRO`
   - `OAUTH_CLIENT_SECRET`
   - `APP_URL` (your Render URL)
4. Deploy

### Option 2: Railway (~$5/mo, no cold starts)
1. Connect repo at [railway.app](https://railway.app)
2. Railway auto-detects Dockerfile
3. Set same env vars in Railway dashboard
4. Deploy

### Option 3: Fly.io (Free tier, global edge)
```bash
fly launch --dockerfile Dockerfile
fly secrets set DATABASE_URL=... STRIPE_SECRET_KEY=...
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string (required) |
| `STRIPE_SECRET_KEY` | Stripe API key (required) |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook signing secret (required) |
| `STRIPE_PRICE_ID_PRO` | Stripe Price ID for Pro tier (required) |
| `OAUTH_CLIENT_SECRET` | OAuth client secret (required) |
| `APP_URL` | Public URL of deployed app (required) |
| `REDIS_URL` | Optional Redis for rate limiting |

## License

MIT
