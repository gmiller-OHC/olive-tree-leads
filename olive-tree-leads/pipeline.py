"""
pipeline.py — Fetches residential properties from OpenStreetMap, geocodes
existing customers, scores every lead, and writes results to Supabase.

Run modes:
  python pipeline.py setup   → first-time: load customers, geocode, fetch leads
  python pipeline.py refresh → daily: re-score existing leads + fetch new ones
"""

import requests
import time
import math
import logging
import sys
import os

from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
from geopy.distance import distance as geodist

import database as db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Service area bounding box (Tobermory → Kincardine → Owen Sound) ───────────
BBOX = dict(min_lat=44.0, max_lat=45.4, min_lon=-82.0, max_lon=-80.6)

# Municipalities we care about — used to filter out-of-area OSM results
SERVICE_CITIES = {
    "port elgin", "southampton", "kincardine", "owen sound", "wiarton",
    "tobermory", "tiverton", "paisley", "chesley", "tara", "allenford",
    "meaford", "chatsworth", "walkerton", "ripley", "cargill", "mildmay",
    "elmwood", "holyrood", "saugeen shores", "lion's head", "lions head",
    "sauble beach", "hepworth", "shallow lake", "mar", "desboro", "hanover",
    "durham", "georgian bluffs", "south bruce peninsula", "arran-elderslie",
}

OVERPASS_URL = "https://overpass-api.de/api/interpreter"


# ── Property data from OpenStreetMap ─────────────────────────────────────────

def fetch_properties_overpass() -> list[dict]:
    """Fetch all addressed residential nodes/ways in the bounding box."""
    query = f"""
    [out:json][timeout:180];
    (
      node["addr:housenumber"]["addr:street"]
          ({BBOX['min_lat']},{BBOX['min_lon']},{BBOX['max_lat']},{BBOX['max_lon']});
      way["addr:housenumber"]["addr:street"]
          ({BBOX['min_lat']},{BBOX['min_lon']},{BBOX['max_lat']},{BBOX['max_lon']});
    );
    out center;
    """
    logger.info("Querying Overpass API for service-area properties...")
    try:
        resp = requests.post(OVERPASS_URL, data={"data": query}, timeout=200)
        resp.raise_for_status()
        elements = resp.json().get("elements", [])
        logger.info(f"Overpass returned {len(elements):,} elements")
        return elements
    except Exception as exc:
        logger.error(f"Overpass error: {exc}")
        return []


def parse_overpass_elements(elements: list) -> list[dict]:
    """Convert raw OSM elements into clean address records."""
    # Building types we skip (not residential)
    skip_buildings = {
        "commercial", "industrial", "retail", "office", "school", "hospital",
        "church", "garage", "shed", "storage", "barn", "greenhouse", "carport",
    }

    records = []
    for el in elements:
        tags = el.get("tags", {})

        # Skip non-residential building types
        if tags.get("building", "").lower() in skip_buildings:
            continue

        # Skip if tagged as amenity / shop / office (not a home)
        if tags.get("amenity") or tags.get("shop") or tags.get("office"):
            continue

        # Coordinates
        if el["type"] == "node":
            lat, lng = el.get("lat"), el.get("lon")
        elif el["type"] == "way":
            center = el.get("center", {})
            lat, lng = center.get("lat"), center.get("lon")
        else:
            continue

        if lat is None or lng is None:
            continue

        number = tags.get("addr:housenumber", "").strip()
        street = tags.get("addr:street", "").strip()
        city   = (tags.get("addr:city") or tags.get("addr:place") or "").strip()
        postal = tags.get("addr:postcode", "").strip()

        if not number or not street:
            continue

        # Filter to service area cities
        if city and city.lower() not in SERVICE_CITIES:
            continue  # outside service area — skip

        address = f"{number} {street}, {city}, Ontario, Canada".strip(", ")

        records.append({
            "address":    address,
            "lat":        lat,
            "lng":        lng,
            "city":       city,
            "postal_code": postal,
            "start_date": tags.get("start_date", ""),
        })

    logger.info(f"Parsed {len(records):,} residential addresses")
    return records


def extract_year_built(start_date_str: str) -> int | None:
    """Pull a 4-digit year out of an OSM start_date string."""
    import re
    if not start_date_str:
        return None
    m = re.search(r"\b(19|20)\d{2}\b", start_date_str)
    return int(m.group()) if m else None


# ── Customer geocoding ─────────────────────────────────────────────────────────

def geocode_customers(batch: int = 100):
    """Geocode any customers that don't yet have lat/lng coordinates."""
    geolocator = Nominatim(user_agent="olive_tree_exteriors_lead_machine")
    geocode    = RateLimiter(geolocator.geocode, min_delay_seconds=1.2)

    pending = db.get_ungeocoded_customers(limit=batch)
    if not pending:
        logger.info("All customers already geocoded.")
        return

    logger.info(f"Geocoding {len(pending)} customer addresses...")
    success = 0
    for row in pending:
        addr = row.get("address", "")
        if not addr:
            continue
        try:
            loc = geocode(addr)
            if loc:
                raw  = loc.raw.get("address", {}) if hasattr(loc, "raw") else {}
                city = (
                    raw.get("city") or raw.get("town") or
                    raw.get("village") or raw.get("hamlet") or ""
                )
                db.update_customer_geocode(row["id"], loc.latitude, loc.longitude, city)
                success += 1
                logger.debug(f"  ✓ {addr[:60]}")
        except Exception as exc:
            logger.warning(f"  ✗ {addr[:60]} — {exc}")

    logger.info(f"Geocoded {success}/{len(pending)} customers")


# ── Lead scoring ───────────────────────────────────────────────────────────────

def score_lead(
    lat: float,
    lng: float,
    customer_coords: list[dict],
    year_built: int | None = None,
) -> tuple[float, float | None]:
    """
    Return (score 0-100, nearest_customer_metres).

    Scoring weights:
      Proximity to existing customers  — up to +35 pts
      Home age (prime window 1975-2005) — up to +20 pts
      Base                              — 50 pts
    """
    score = 50.0
    nearest_m = None

    if customer_coords:
        distances = [
            geodist((lat, lng), (c["lat"], c["lng"])).meters
            for c in customer_coords
        ]
        nearest_m = min(distances)

        if nearest_m < 100:
            score += 35
        elif nearest_m < 300:
            score += 28
        elif nearest_m < 600:
            score += 20
        elif nearest_m < 1_200:
            score += 12
        elif nearest_m < 2_500:
            score += 6
        elif nearest_m < 5_000:
            score += 2

    if year_built:
        if 1975 <= year_built <= 2005:
            score += 20
        elif 2005 < year_built <= 2015:
            score += 8
        elif year_built > 2015:
            score -= 12
        elif year_built < 1960:
            score += 3

    return min(100.0, max(0.0, score)), nearest_m


def is_existing_customer(lat: float, lng: float, customer_coords: list[dict],
                          threshold_m: float = 40.0) -> bool:
    """True if this property is within threshold_m of a known customer."""
    return any(
        geodist((lat, lng), (c["lat"], c["lng"])).meters < threshold_m
        for c in customer_coords
    )


# ── Main pipeline functions ────────────────────────────────────────────────────

def refresh_leads():
    """Fetch properties from OSM, score them, write to Supabase."""
    customers = db.get_all_customers_geocoded()
    logger.info(f"Loaded {len(customers)} geocoded customers as reference points")

    elements = fetch_properties_overpass()
    if not elements:
        logger.error("No elements from Overpass — aborting refresh")
        return

    records = parse_overpass_elements(elements)

    updated      = 0
    skipped_biz  = 0

    for rec in records:
        lat, lng = rec["lat"], rec["lng"]

        # Skip if it's an existing customer's address
        if is_existing_customer(lat, lng, customers):
            skipped_biz += 1
            continue

        year_built = extract_year_built(rec.get("start_date", ""))
        score, nearest_m = score_lead(lat, lng, customers, year_built)

        db.upsert_lead(
            address         = rec["address"],
            lat             = lat,
            lng             = lng,
            city            = rec["city"],
            postal_code     = rec["postal_code"],
            score           = score,
            nearest_customer_m = nearest_m,
        )
        updated += 1

        if updated % 500 == 0:
            logger.info(f"  …{updated:,} leads written so far")

    logger.info(
        f"Pipeline complete — {updated:,} leads upserted, "
        f"{skipped_biz} existing-customer addresses skipped"
    )


def run_setup():
    """One-time setup: load customer CSV → geocode → fetch leads."""
    logger.info("=== SETUP MODE ===")
    db.load_customers_from_csv("data/customers.csv")
    geocode_customers(batch=400)
    refresh_leads()
    logger.info("=== SETUP COMPLETE ===")


def run_daily_refresh():
    """Daily job: geocode any new customers, then re-score all leads."""
    logger.info("=== DAILY REFRESH ===")
    geocode_customers(batch=50)   # pick up any newly-added customers
    refresh_leads()
    logger.info("=== DAILY REFRESH COMPLETE ===")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "refresh"
    if mode == "setup":
        run_setup()
    else:
        run_daily_refresh()
