#!/bin/bash
set -e

echo "=== SunoCoach Deploy Script ==="

# 1. Check dependencies
command -v wrangler >/dev/null 2>&1 || { echo "wrangler CLI required. Install: npm install -g wrangler"; exit 1; }
command -v psql >/dev/null 2>&1 || { echo "psql required for migrations"; exit 1; }

# 2. Run DB migrations
echo "[1/5] Running database migrations..."
if [ -n "$DATABASE_URL" ]; then
    psql "$DATABASE_URL" -f src/db/schema.sql
    echo "Migrations complete."
else
    echo "WARNING: DATABASE_URL not set. Skipping migrations."
fi

# 3. Seed initial workflow pattern if table is empty
echo "[2/5] Seeding initial workflow pattern..."
if [ -n "$DATABASE_URL" ]; then
    PATTERN_COUNT=$(psql "$DATABASE_URL" -t -c "SELECT COUNT(*) FROM workflow_patterns WHERE name = 'locked-in-v1';")
    if [ "$PATTERN_COUNT" -eq "0" ]; then
        psql "$DATABASE_URL" -f src/db/seed.sql
        echo "Seed complete."
    else
        echo "Pattern already exists. Skipping seed."
    fi
else
    echo "WARNING: DATABASE_URL not set. Skipping seed."
fi

# 4. Deploy to Cloudflare Workers
echo "[3/5] Deploying to Cloudflare Workers..."
wrangler deploy

# 5. Get live URL
echo "[4/5] Getting deployment URL..."
DEPLOY_URL=$(wrangler info --json 2>/dev/null | grep -o '"url": "[^"]*"' | head -1 | cut -d'"' -f4)
if [ -z "$DEPLOY_URL" ]; then
    DEPLOY_URL="https://sunocoach.YOUR_SUBDOMAIN.workers.dev"
fi

echo "Live URL: $DEPLOY_URL"

# 6. Verify health endpoint
echo "[5/5] Verifying /health endpoint..."
for i in {1..5}; do
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$DEPLOY_URL/health" || echo "000")
    if [ "$STATUS" = "200" ]; then
        echo "Health check PASSED (200 OK)"
        echo ""
        echo "=== DEPLOYMENT COMPLETE ==="
        echo "URL: $DEPLOY_URL"
        echo "Health: $DEPLOY_URL/health"
        echo "OAuth Discovery: $DEPLOY_URL/.well-known/oauth-authorization-server"
        echo ""
        echo "Next steps:"
        echo "1. Set secrets: wrangler secret put STRIPE_SECRET_KEY"
        echo "2. Set secrets: wrangler secret put DATABASE_URL"
        echo "3. Submit to MCP registries (see registry/ directory)"
        exit 0
    fi
    echo "Attempt $i: HTTP $STATUS. Retrying in 3s..."
    sleep 3
done

echo "WARNING: Health check did not return 200. Check logs: wrangler tail"
exit 1
