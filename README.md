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

## Debugging MCP on Render

The server exposes a live debug dashboard at [`/debug/mcp`](http://localhost:8000/debug/mcp) that runs the full MCP + OAuth end-to-end smoke check against the live instance.

### Usage

Open in any browser:

```
https://sunocoach.onrender.com/debug/mcp
```

The page renders a dark-themed HTML dashboard with:

- **Summary boxes** — Passed / Failed / Duration
- **Check table** — 11 steps with ✅/❌ status icons and detail messages

### What the 11 checks cover

| # | Check | What it validates |
|---|-------|-------------------|
| 1 | OAuth Discovery | `/.well-known/oauth-authorization-server` returns issuer, endpoints, scopes_supported, S256 support |
| 2 | Protected Resource | `/.well-known/oauth-protected-resource` has resource, authorization_servers, bearer_methods, scopes, documentation |
| 3 | Client Registration | `POST /oauth/register` returns `client_id` and `client_secret` |
| 4 | Authorize HTML Page | `GET /oauth/authorize` renders a login form with absolute URL action |
| 5 | User Login → Auth Code | `POST /oauth/login` with credentials returns a 302 redirect with `code` and `state` params |
| 6 | Token Exchange | `POST /oauth/token` exchanges the auth code for a Bearer access token + refresh token |
| 7 | tools/list (authenticated) | `POST /mcp` with Bearer token returns an SSE response with the tools array |
| 8 | tools/call (authenticated) | `POST /mcp` calls `get_pattern_status` and returns SSE content |
| 9 | 401 without token | `POST /mcp` without auth returns 401 with proper `WWW-Authenticate` header |
| 10 | MCP initialize (no auth) | `POST /mcp` `initialize` returns SSE with `protocolVersion: "2025-11-25"` |
| 11 | notifications/initialized | `POST /mcp` `notifications/initialized` returns 204 No Content |

### Timeout behavior

Each check has a **10-second total budget**. If a step exceeds its remaining share, it's marked as ❌ with `"timeout"` detail and execution continues to the next step.

### ⚠️ Warning

> **Do not expose `/debug/mcp` publicly in production.** This endpoint runs live OAuth token exchange and MCP tool calls against the actual database. It should be disabled or auth-protected before going to production.

### Running locally

```bash
cd sunocoach
python test_oauth_mcp_smoke.py
```

This calls the same [`run_mcp_smoke()`](sunocoach/debug/mcp_smoke.py) function used by the dashboard, but with a 30-second timeout for local debugging.

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
