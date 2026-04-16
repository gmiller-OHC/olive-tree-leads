"""
database.py — Supabase client and all DB operations for Olive Tree Lead Machine
"""

import os
import streamlit as st
from supabase import create_client, Client
from datetime import datetime
from typing import Optional


def get_client() -> Client:
    """Get Supabase client using secrets from Streamlit or environment variables."""
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
    except Exception:
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_KEY", "")

    if not url or not key:
        raise ValueError(
            "Missing Supabase credentials. "
            "Add SUPABASE_URL and SUPABASE_KEY to .streamlit/secrets.toml"
        )
    return create_client(url, key)


# ── Customers ─────────────────────────────────────────────────────────────────

def load_customers_from_csv(csv_path: str):
    """Bulk-insert customers from CSV into Supabase (skips duplicates)."""
    import pandas as pd
    df = pd.read_csv(csv_path)
    supabase = get_client()

    rows = []
    for _, row in df.iterrows():
        addr = str(row.get("Address", "")).strip()
        name = str(row.get("Name", "")).strip()
        if addr and addr != "nan":
            rows.append({"name": name, "address": addr, "geocoded": False})

    if rows:
        # upsert on address so re-runs are safe
        supabase.table("customers").upsert(rows, on_conflict="address").execute()
    print(f"Loaded {len(rows)} customers into Supabase")


def get_ungeocoded_customers(limit: int = 50) -> list[dict]:
    supabase = get_client()
    result = (
        supabase.table("customers")
        .select("id, name, address")
        .eq("geocoded", False)
        .not_.is_("address", "null")
        .limit(limit)
        .execute()
    )
    return result.data or []


def update_customer_geocode(id: int, lat: float, lng: float, city: str):
    supabase = get_client()
    supabase.table("customers").update(
        {"lat": lat, "lng": lng, "city": city, "geocoded": True}
    ).eq("id", id).execute()


def get_all_customers_geocoded() -> list[dict]:
    supabase = get_client()
    result = (
        supabase.table("customers")
        .select("id, name, address, lat, lng, city")
        .eq("geocoded", True)
        .execute()
    )
    return result.data or []


# ── Leads ──────────────────────────────────────────────────────────────────────

def upsert_lead(
    address: str,
    lat: float,
    lng: float,
    city: str,
    postal_code: str,
    score: float,
    nearest_customer_m: Optional[float],
):
    supabase = get_client()
    supabase.table("leads").upsert(
        {
            "address": address,
            "lat": lat,
            "lng": lng,
            "city": city,
            "postal_code": postal_code,
            "score": round(score, 1),
            "nearest_customer_m": round(nearest_customer_m, 1) if nearest_customer_m else None,
            "is_customer": False,
            "last_updated": datetime.utcnow().isoformat(),
        },
        on_conflict="address",
    ).execute()


def get_daily_leads(limit: int = 25, exclude_visited: bool = True) -> list[dict]:
    """Top N unvisited leads sorted by score descending."""
    supabase = get_client()

    if exclude_visited:
        # Get visited lead IDs first
        visited = supabase.table("visits").select("lead_id").execute()
        visited_ids = list({v["lead_id"] for v in (visited.data or [])})

        query = (
            supabase.table("leads")
            .select("*")
            .eq("is_customer", False)
            .not_.is_("lat", "null")
            .order("score", desc=True)
            .limit(limit + len(visited_ids))  # fetch extra, filter client-side
        )
        result = query.execute()
        leads = [r for r in (result.data or []) if r["id"] not in visited_ids]
        return leads[:limit]
    else:
        result = (
            supabase.table("leads")
            .select("*")
            .eq("is_customer", False)
            .not_.is_("lat", "null")
            .order("score", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []


def get_leads_near(lat: float, lng: float, radius_km: float = 2.0, limit: int = 20) -> list[dict]:
    """Get leads within radius using bounding-box pre-filter then Haversine."""
    import math
    from geopy.distance import distance as geodist

    lat_delta = radius_km / 111.0
    lng_delta = radius_km / (111.0 * math.cos(math.radians(lat)))

    supabase = get_client()

    # Get visited IDs to exclude
    visited = supabase.table("visits").select("lead_id").execute()
    visited_ids = {v["lead_id"] for v in (visited.data or [])}

    result = (
        supabase.table("leads")
        .select("*")
        .eq("is_customer", False)
        .not_.is_("lat", "null")
        .gte("lat", lat - lat_delta)
        .lte("lat", lat + lat_delta)
        .gte("lng", lng - lng_delta)
        .lte("lng", lng + lng_delta)
        .order("score", desc=True)
        .limit(200)
        .execute()
    )

    candidates = result.data or []

    # Precise Haversine filter + exclude visited
    hits = []
    for row in candidates:
        if row["id"] in visited_ids:
            continue
        d = geodist((lat, lng), (row["lat"], row["lng"])).meters
        if d <= radius_km * 1000:
            row["distance_m"] = round(d)
            hits.append(row)

    return sorted(hits, key=lambda x: x["distance_m"])[:limit]


def get_all_leads_for_map(min_score: float = 0) -> list[dict]:
    """Fetch all geocoded leads with latest visit outcome joined."""
    supabase = get_client()

    leads_result = (
        supabase.table("leads")
        .select("*")
        .not_.is_("lat", "null")
        .gte("score", min_score)
        .execute()
    )
    leads = leads_result.data or []

    # Get visits (latest per lead)
    visits_result = supabase.table("visits").select("lead_id, outcome, visited_at").execute()
    visits = visits_result.data or []

    # Build map: lead_id → most recent outcome
    from collections import defaultdict
    visit_map = {}
    for v in sorted(visits, key=lambda x: x.get("visited_at", "")):
        visit_map[v["lead_id"]] = v["outcome"]

    for lead in leads:
        lead["visit_outcome"] = visit_map.get(lead["id"])

    return leads


def mark_visited(lead_id: int, outcome: str, notes: str = ""):
    supabase = get_client()
    supabase.table("visits").insert(
        {
            "lead_id": lead_id,
            "outcome": outcome,
            "notes": notes,
            "visited_at": datetime.utcnow().isoformat(),
        }
    ).execute()


def get_stats() -> dict:
    supabase = get_client()

    total = supabase.table("leads").select("id", count="exact").eq("is_customer", False).execute()
    visited_rows = supabase.table("visits").select("lead_id", count="exact").execute()
    booked_rows = (
        supabase.table("visits")
        .select("id", count="exact")
        .eq("outcome", "booked_assessment")
        .execute()
    )
    customers = (
        supabase.table("customers")
        .select("id", count="exact")
        .eq("geocoded", True)
        .execute()
    )

    # Unique visited leads
    visited_data = supabase.table("visits").select("lead_id").execute()
    unique_visited = len({v["lead_id"] for v in (visited_data.data or [])})

    return {
        "total_leads": total.count or 0,
        "visited": unique_visited,
        "booked": booked_rows.count or 0,
        "customers": customers.count or 0,
    }
