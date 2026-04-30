"""
src/db/migrate.py — Idempotent database migrations for SunoCoach.

Runs automatically on every app startup (via FastAPI @app.on_event("startup")).
Safe to run multiple times; every operation uses IF NOT EXISTS or checks
information_schema / pg_catalog before altering the schema.

Migration history
-----------------
v1  (initial)   – Create uuid-ossp extension + core tables (users, api_keys,
                  workflow_patterns, sessions, session_steps, style_prompts,
                  client_profiles, drift_events, session_usage, workflow_votes).
v2  (OAuth)     – Add users.password_hash column; create oauth_codes,
                  oauth_tokens, oauth_clients tables + indexes.
                  This migration repairs the live Render DB which was created
                  from v1 schema and never received the v2 DDL changes.
v3  (trigger)   – Ensure the update_updated_at_column trigger exists on users.
"""

import logging
from db.client import get_pool

log = logging.getLogger(__name__)


async def run_migrations() -> None:
    """Run all idempotent migrations in sequence."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        log.info("[migrate] Starting migrations …")
        await _v1_core_tables(conn)
        await _v2_oauth_tables(conn)
        await _v3_trigger(conn)
        log.info("[migrate] All migrations complete ✅")


# ──────────────────────────────────────────────────────────────────────────────
# Helper: check if a column exists
# ──────────────────────────────────────────────────────────────────────────────

async def _column_exists(conn, table: str, column: str) -> bool:
    row = await conn.fetchrow(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name = $1 AND column_name = $2
        """,
        table, column,
    )
    return row is not None


async def _table_exists(conn, table: str) -> bool:
    row = await conn.fetchrow(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = $1
        """,
        table,
    )
    return row is not None


async def _index_exists(conn, index_name: str) -> bool:
    row = await conn.fetchrow(
        "SELECT 1 FROM pg_catalog.pg_indexes WHERE indexname = $1",
        index_name,
    )
    return row is not None


async def _trigger_exists(conn, trigger_name: str, table: str) -> bool:
    row = await conn.fetchrow(
        """
        SELECT 1 FROM information_schema.triggers
        WHERE trigger_name = $1 AND event_object_table = $2
        """,
        trigger_name, table,
    )
    return row is not None


# ──────────────────────────────────────────────────────────────────────────────
# v1: Core tables (safe – all use CREATE … IF NOT EXISTS)
# ──────────────────────────────────────────────────────────────────────────────

async def _v1_core_tables(conn) -> None:
    """Create uuid-ossp extension and all core tables if they don't exist."""
    log.info("[migrate/v1] Checking core tables …")

    await conn.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            email TEXT UNIQUE NOT NULL,
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            tier TEXT NOT NULL DEFAULT 'free'
                CHECK (tier IN ('free', 'contributor', 'pro')),
            contributor_submissions INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            key_hash TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            last_used TIMESTAMPTZ,
            active BOOLEAN NOT NULL DEFAULT true
        )
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS workflow_patterns (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            name TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            steps JSONB NOT NULL,
            consistency_score DECIMAL(5,2) DEFAULT 0.00,
            vote_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'drifting', 'deprecated', 'calibrating')),
            submitted_by UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            last_validated TIMESTAMPTZ,
            UNIQUE(name, version)
        )
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            workflow_pattern_id UUID NOT NULL REFERENCES workflow_patterns(id),
            current_step INTEGER NOT NULL DEFAULT 1,
            started_at TIMESTAMPTZ DEFAULT NOW(),
            completed_at TIMESTAMPTZ,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'completed', 'abandoned'))
        )
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS session_steps (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            step_number INTEGER NOT NULL,
            action_taken TEXT,
            quality_rating INTEGER CHECK (quality_rating BETWEEN 1 AND 5),
            notes TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(session_id, step_number)
        )
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS style_prompts (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            prompt_text TEXT NOT NULL,
            genre_tags JSONB DEFAULT '[]',
            mood_tags JSONB DEFAULT '[]',
            bpm INTEGER,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS client_profiles (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            vocal_type TEXT,
            preferred_genres JSONB DEFAULT '[]',
            bpm_range_min INTEGER,
            bpm_range_max INTEGER,
            emotional_register TEXT,
            approved_prompts JSONB DEFAULT '[]',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS drift_events (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            detected_at TIMESTAMPTZ DEFAULT NOW(),
            sessions_sampled INTEGER NOT NULL,
            avg_score_before DECIMAL(5,2) NOT NULL,
            avg_score_after DECIMAL(5,2) NOT NULL,
            resolved_at TIMESTAMPTZ,
            resolved_by_pattern_id UUID
                REFERENCES workflow_patterns(id) ON DELETE SET NULL
        )
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS session_usage (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            month_year TEXT NOT NULL,
            session_count INTEGER NOT NULL DEFAULT 0,
            UNIQUE(user_id, month_year)
        )
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS workflow_votes (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            pattern_id UUID NOT NULL
                REFERENCES workflow_patterns(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
            session_evidence JSONB,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(pattern_id, user_id)
        )
    """)

    # Core indexes (idempotent)
    for stmt in [
        "CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status)",
        "CREATE INDEX IF NOT EXISTS idx_session_steps_session_id ON session_steps(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_api_keys_key_hash ON api_keys(key_hash)",
        "CREATE INDEX IF NOT EXISTS idx_style_prompts_user_id ON style_prompts(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_client_profiles_user_id ON client_profiles(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_workflow_patterns_status ON workflow_patterns(status)",
        "CREATE INDEX IF NOT EXISTS idx_drift_events_detected_at ON drift_events(detected_at)",
        "CREATE INDEX IF NOT EXISTS idx_session_usage_user_month ON session_usage(user_id, month_year)",
    ]:
        await conn.execute(stmt)

    log.info("[migrate/v1] Core tables OK ✅")


# ──────────────────────────────────────────────────────────────────────────────
# v2: OAuth tables + users.password_hash column
# ──────────────────────────────────────────────────────────────────────────────

async def _v2_oauth_tables(conn) -> None:
    """
    Add OAuth support to the schema.

    1. users.password_hash — missing from the original users table.
    2. oauth_codes         — authorization codes for the OAuth dance.
    3. oauth_tokens        — access + refresh tokens, persisted across restarts.
    4. oauth_clients       — dynamically registered OAuth clients (Claude, etc.).
    """
    log.info("[migrate/v2] Checking OAuth tables and columns …")

    # ── 2a. users.password_hash ──────────────────────────────────────────────
    if not await _column_exists(conn, "users", "password_hash"):
        log.info("[migrate/v2] Adding users.password_hash …")
        await conn.execute(
            "ALTER TABLE users ADD COLUMN password_hash TEXT NOT NULL DEFAULT ''"
        )
        log.info("[migrate/v2] users.password_hash added ✅")
    else:
        log.info("[migrate/v2] users.password_hash already exists, skipping.")

    # ── 2b. oauth_codes ──────────────────────────────────────────────────────
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS oauth_codes (
            code                  TEXT PRIMARY KEY,
            redirect_uri          TEXT NOT NULL,
            scope                 TEXT NOT NULL DEFAULT 'read',
            state                 TEXT DEFAULT '',
            code_challenge        TEXT,
            code_challenge_method TEXT,
            created_at            DOUBLE PRECISION NOT NULL,
            used                  BOOLEAN NOT NULL DEFAULT false,
            user_id               UUID REFERENCES users(id) ON DELETE SET NULL
        )
    """)

    # ── 2c. oauth_tokens ─────────────────────────────────────────────────────
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS oauth_tokens (
            token_hash    TEXT PRIMARY KEY,
            access_token  TEXT NOT NULL,
            refresh_token TEXT NOT NULL,
            created_at    DOUBLE PRECISION NOT NULL,
            expires_at    DOUBLE PRECISION NOT NULL,
            scope         TEXT NOT NULL DEFAULT 'read',
            user_id       UUID REFERENCES users(id) ON DELETE SET NULL
        )
    """)

    # ── 2d. oauth_clients ────────────────────────────────────────────────────
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS oauth_clients (
            client_id     TEXT PRIMARY KEY,
            client_secret TEXT NOT NULL,
            client_name   TEXT DEFAULT 'Claude',
            redirect_uris JSONB DEFAULT '[]',
            created_at    DOUBLE PRECISION NOT NULL
        )
    """)

    # ── OAuth indexes ─────────────────────────────────────────────────────────
    for stmt in [
        "CREATE INDEX IF NOT EXISTS idx_oauth_tokens_user_id ON oauth_tokens(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_oauth_tokens_expires_at ON oauth_tokens(expires_at)",
        "CREATE INDEX IF NOT EXISTS idx_oauth_codes_user_id ON oauth_codes(user_id)",
    ]:
        await conn.execute(stmt)

    log.info("[migrate/v2] OAuth tables OK ✅")


# ──────────────────────────────────────────────────────────────────────────────
# v3: updated_at trigger on users
# ──────────────────────────────────────────────────────────────────────────────

async def _v3_trigger(conn) -> None:
    """Ensure the updated_at auto-update trigger exists on users."""
    log.info("[migrate/v3] Checking updated_at trigger …")

    # CREATE OR REPLACE is safe to re-run for the function.
    await conn.execute("""
        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)

    if not await _trigger_exists(conn, "update_users_updated_at", "users"):
        log.info("[migrate/v3] Creating update_users_updated_at trigger …")
        await conn.execute("""
            CREATE TRIGGER update_users_updated_at
            BEFORE UPDATE ON users
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()
        """)
        log.info("[migrate/v3] Trigger created ✅")
    else:
        log.info("[migrate/v3] Trigger already exists, skipping.")

    log.info("[migrate/v3] Trigger OK ✅")
