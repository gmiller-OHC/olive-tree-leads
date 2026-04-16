-- ============================================================
-- Olive Tree Lead Machine — Supabase Database Setup
-- Run this once in the Supabase SQL Editor
-- ============================================================

-- 1. Existing customers (imported from Jobber)
CREATE TABLE IF NOT EXISTS customers (
    id         SERIAL PRIMARY KEY,
    name       TEXT,
    address    TEXT UNIQUE NOT NULL,
    lat        DOUBLE PRECISION,
    lng        DOUBLE PRECISION,
    city       TEXT,
    geocoded   BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Lead properties (populated by pipeline.py from OpenStreetMap)
CREATE TABLE IF NOT EXISTS leads (
    id                  SERIAL PRIMARY KEY,
    address             TEXT UNIQUE NOT NULL,
    lat                 DOUBLE PRECISION,
    lng                 DOUBLE PRECISION,
    city                TEXT,
    postal_code         TEXT,
    score               DOUBLE PRECISION DEFAULT 50,
    nearest_customer_m  DOUBLE PRECISION,
    is_customer         BOOLEAN DEFAULT FALSE,
    last_updated        TIMESTAMPTZ DEFAULT NOW()
);

-- 3. Door-knock outcomes (recorded by sales guy in the app)
CREATE TABLE IF NOT EXISTS visits (
    id         SERIAL PRIMARY KEY,
    lead_id    INTEGER REFERENCES leads(id) ON DELETE CASCADE,
    outcome    TEXT NOT NULL,
    -- outcomes: booked_assessment | callback | not_interested | not_home
    notes      TEXT DEFAULT '',
    visited_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for fast queries
CREATE INDEX IF NOT EXISTS idx_leads_score      ON leads(score DESC);
CREATE INDEX IF NOT EXISTS idx_leads_lat_lng    ON leads(lat, lng);
CREATE INDEX IF NOT EXISTS idx_leads_customer   ON leads(is_customer);
CREATE INDEX IF NOT EXISTS idx_visits_lead_id   ON visits(lead_id);
CREATE INDEX IF NOT EXISTS idx_customers_geocoded ON customers(geocoded);
