"""Quick check that OAuth DB tables exist."""
import asyncio
import asyncpg

DB_URL = "postgresql://postgres.xtjtbvejhoxtzqcikwii:jk1sDBbS3O3szK3e@aws-1-us-east-1.pooler.supabase.com:6543/postgres"

async def main():
    conn = await asyncpg.connect(DB_URL)
    try:
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name IN ($1,$2,$3)",
            "oauth_codes", "oauth_tokens", "oauth_clients"
        )
        names = {r["table_name"] for r in rows}
        for t in ["oauth_codes", "oauth_tokens", "oauth_clients"]:
            if t in names:
                print(f"  ✅ {t} — EXISTS")
            else:
                print(f"  ❌ {t} — MISSING")
        
        # Check row counts
        for t in ["oauth_codes", "oauth_tokens", "oauth_clients"]:
            count = await conn.fetchval(f"SELECT COUNT(*) FROM {t}")
            print(f"     {t} rows: {count}")
    finally:
        await conn.close()

asyncio.run(main())
