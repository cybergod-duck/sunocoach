-- SunoCoach Database Schema
-- PostgreSQL (Supabase compatible)

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email TEXT UNIQUE NOT NULL,
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT,
    tier TEXT NOT NULL DEFAULT 'free' CHECK (tier IN ('free', 'contributor', 'pro')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- API Keys table
CREATE TABLE IF NOT EXISTS api_keys (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_used TIMESTAMPTZ,
    active BOOLEAN NOT NULL DEFAULT true
);

-- Workflow Patterns table
CREATE TABLE IF NOT EXISTS workflow_patterns (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    steps JSONB NOT NULL,
    consistency_score DECIMAL(5,2) DEFAULT 0.00,
    vote_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'drifting', 'deprecated', 'calibrating')),
    submitted_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_validated TIMESTAMPTZ,
    UNIQUE(name, version)
);

-- Sessions table
CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workflow_pattern_id UUID NOT NULL REFERENCES workflow_patterns(id),
    current_step INTEGER NOT NULL DEFAULT 1,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed', 'abandoned'))
);

-- Session Steps table
CREATE TABLE IF NOT EXISTS session_steps (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    step_number INTEGER NOT NULL,
    action_taken TEXT,
    quality_rating INTEGER CHECK (quality_rating BETWEEN 1 AND 5),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(session_id, step_number)
);

-- Style Prompts table
CREATE TABLE IF NOT EXISTS style_prompts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    genre_tags JSONB DEFAULT '[]',
    mood_tags JSONB DEFAULT '[]',
    bpm INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Client Profiles table
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
);

-- Drift Events table
CREATE TABLE IF NOT EXISTS drift_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    detected_at TIMESTAMPTZ DEFAULT NOW(),
    sessions_sampled INTEGER NOT NULL,
    avg_score_before DECIMAL(5,2) NOT NULL,
    avg_score_after DECIMAL(5,2) NOT NULL,
    resolved_at TIMESTAMPTZ,
    resolved_by_pattern_id UUID REFERENCES workflow_patterns(id) ON DELETE SET NULL
);

-- Session usage tracking for rate limiting
CREATE TABLE IF NOT EXISTS session_usage (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    month_year TEXT NOT NULL, -- Format: YYYY-MM
    session_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(user_id, month_year)
);

-- Workflow votes tracking
CREATE TABLE IF NOT EXISTS workflow_votes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    pattern_id UUID NOT NULL REFERENCES workflow_patterns(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    session_evidence JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(pattern_id, user_id)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_session_steps_session_id ON session_steps(session_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_key_hash ON api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_style_prompts_user_id ON style_prompts(user_id);
CREATE INDEX IF NOT EXISTS idx_client_profiles_user_id ON client_profiles(user_id);
CREATE INDEX IF NOT EXISTS idx_workflow_patterns_status ON workflow_patterns(status);
CREATE INDEX IF NOT EXISTS idx_drift_events_detected_at ON drift_events(detected_at);
CREATE INDEX IF NOT EXISTS idx_session_usage_user_month ON session_usage(user_id, month_year);

-- Trigger to update updated_at on users
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
