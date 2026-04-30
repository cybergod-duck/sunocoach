# SunoCoach

AI music creation workflow coach that lives inside Claude. Community-validated patterns for Suno and any AI music generator. Style prompt engineering, lyric structure tagging, client profile management, and self-updating pattern detection.

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![MCP](https://img.shields.io/badge/MCP-Compatible-blue)
![Stripe](https://img.shields.io/badge/Billing-Stripe-635BFF)

## Stack

- **Backend:** FastAPI + Uvicorn (Python 3.11)
- **Database:** PostgreSQL via asyncpg (Supabase in production)
- **Cache:** In-memory (Redis optional)
- **Auth:** OAuth 2.0
- **Billing:** Stripe Checkout + Webhooks
- **Deploy:** Docker → Render/Railway/Fly.io

## Connect to Claude

Go to **claude.ai → Settings → Integrations → Add Integration** → paste your deployed URL:

```
https://sunocoach.onrender.com
```

OAuth handles auth automatically — no API keys to copy-paste.

## Tools

- `get_current_workflow` — Returns the active workflow pattern with drift status
- `get_next_step` — Returns exact instruction for the current step in plain English
- `log_step_result` — Stores result, advances session state, checks for drift
- `start_session` — Creates a new coaching session
- `generate_style_prompt` — Takes plain English, returns structured style DNA prompt
- `build_lyric_structure` — Applies correct bracket tagging to raw lyrics
- `validate_prompt` — Scores prompt against all rules, returns issues with fixes
- `save_style_prompt` — Validates then stores in user's style library
- `recall_style` — Fuzzy search user's style prompt library
- `save_client` — Creates or updates client profile
- `get_client_brief` — Returns ready-to-paste style prompt + lyric structure from client profile
- `submit_workflow` — Contributor tier: submits new workflow pattern for community scoring
- `vote_on_pattern` — Contributor tier: vote on submitted pattern
- `get_pattern_status` — Returns active/drifting/calibrating status + explanation

## Tiers

| Tier | Price | Sessions | Features |
|------|-------|----------|----------|
| **Free** | $0 | 10/month | Read-only workflow, basic style prompts |
| **Contributor** | $0 | 10/month until 3 approved patterns, then Unlimited | Submit + vote on workflow patterns (email verify only) |
| **Pro** | $9/mo | Unlimited | Client profiles, priority patterns, email support |

[Upgrade to Pro](https://sunocoach.onrender.com/billing/checkout)

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
