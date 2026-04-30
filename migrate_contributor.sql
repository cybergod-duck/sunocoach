-- Migration: Add contributor_submissions column to users table
ALTER TABLE users ADD COLUMN IF NOT EXISTS contributor_submissions INTEGER NOT NULL DEFAULT 0;
