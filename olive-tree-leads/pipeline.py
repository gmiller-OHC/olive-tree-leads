"""
pipeline.py — Fetches residential properties from OpenStreetMap, geocodes
existing customers, scores every lead, and writes results to Supabase.

Run modes:
  python pipeline.py setup   → first-time: geocode customers + fetch leads
  python pipeline.py refresh → daily: geocode any new customers + fetch leads
"""

import requests
import time
import math
import logging
import sys
import os
import pathlib

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

BASE_DIR = pathlib.Path(__file__).parent

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# ── Chunked bounding boxes to avoid Overpass timeouts ─────────────────────────
# Breaking the service area into 4 smaller zones
BBOX_CHUNKS = [
    # (min_lat, min_lon, max_lat, max_lon, label)
    (44.00, -82.00, 44.45, -81.20, "South Bruce - Kincardine/Tiverton/Paisley"),
    (44.40, -81.70, 44.85, -81.00, "Central - Port Elgin/Southampton/Wiarton"),
    (44.75, -81.80, 45.40, -81.00, "North Peninsula - Lion's Head/Tobermory"),
    (44.40, -81.10, 44.85, -80.60, "East - Owen Sound/Meaford/Chatsworth"),
]

SERVICE_CITIES = {
    "port elgin", "southampton", "kincardine", "owen sound", "wiarton",
    "tobermory", "tiverton", "paisley", "chesley", "tara", "allenford",
    "meaford", "chatsworth", "walkerton", "ripley", "cargill", "mildmay",
    "elmwood", "holyrood", "saugeen shores", "lion's head", "lions head",
    "sauble beach", "hepworth", "shallow lake", "mar", "desboro", "hanover",
    "durham", "georgian bluffs", "south bruce peninsula", "arran-elderslie",
    "bruce peninsula", "northern bruce peninsula",
}


# ── Overpass fetching with retry ───────────────────────────────────────────────

def fetch_chunk(min_lat, min_lon, max_lat, max_lon, label, retries=3) -> list:
    """Fetch one geographic chunk from Overpass with retry logic."""
    query = f"""
    [out:json][timeout:60];
    (
      node["addr:housenumber"]["addr:street"]
          ({min_lat},{min_lon},{max_lat},{max_lon});
      way["addr:housenumber"]["addr:street"]
          ({min_lat},{min_lon},{max_lat},{max_lon});
    );
    out center;
    """
    for attempt in range(1, retries + 1):
        try:
            logger.info(f"  Fetching chunk: {label} (attempt {attempt})")
            resp = requests.post(
                OVERPASS_URL,
                data={"data": query},
                timeout=90,
            )
            resp.raise_for_status()
            elements = resp.json().get("elements", [])
            logger.info(f"  ✓ {label}: {len(elements)} elements")
            return elements
        except Exception as exc:
            logger.warning(f"  ✗ Attempt {attempt} failed: {exc}")
            if attempt < retries:
                wait = attempt * 15
                logger.info(f"  Waiting {wait}s before retry...")
                time.sleep(wait)
    logger.error(f"  All retries failed for chunk: {label}")
    return []


def fetch_properties_overpass() -> list[dict]:
    """Fetch all chunks and combine results."""
    all_elements = []
    for chunk in BBOX_CHUNKS:
        min_lat, min_lon, max_lat, max_lon, label = chunk
        elements = fetch_chunk(min_lat, min_lon, max_lat, max_lon, label)
        all_elements.extend(elements)
        if len(BBOX_CHUNKS) > 1:
            time.sleep(5)  # be polite to Overpass between chunks
    logger.info(f"Total elements fetched: {len(all_elements):,}")
    return all_elements


def parse_overpass_elements(elements: list) -> list[dict]:
    skip_buildings = {
        "commercial", "industrial", "retail", "office", "school", "hospital",
        "church", "garage", "shed", "storage", "barn", "greenhouse", "carport",
    }
    records = []
    seen_addresses = set()

    for el in elements:
        tags = el.get("tags", {})
        if tags.get("building", "").lower() in skip_buildings:
            continue
        if tags.get("amenity") or tags.get("shop") or tags.get("office"):
            continue

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

        if city and city.lower() not in SERVICE_CITIES:
            continue

        address = f"{number} {street}, {city}, Ontario, Canada".strip(", ")

        # Deduplicate
        if address in seen_addresses:
            continue
        seen_addresses.add(address)

        records.append({
            "address":    address,
            "lat":        lat,
            "lng":        lng,
            "city":       city,
            "postal_code": postal,
            "start_date": tags.get("start_date", ""),
        })

    logger.info(f"Parsed {len(records):,} unique residential addresses")
    return records


def extract_year_built(start_date_str: str):
    import re
    if not start_date_str:
        return None
    m = re.search(r"\b(19|20)\d{2}\b", start_date_str)
    return int(m.group()) if m else None


# ── Customer geocoding ─────────────────────────────────────────────────────────

def geocode_customers(batch: int = 100):
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
        except Exception as exc:
            logger.warning(f"  ✗ {addr[:60]} — {exc}")

    logger.info(f"Geocoded {success}/{len(pending)} customers")


# ── Lead scoring ───────────────────────────────────────────────────────────────

def score_lead(lat, lng, customer_coords, year_built=None):
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


def is_existing_customer(lat, lng, customer_coords, threshold_m=40.0):
    return any(
        geodist((lat, lng), (c["lat"], c["lng"])).meters < threshold_m
        for c in customer_coords
    )


# ── Main pipeline ──────────────────────────────────────────────────────────────

def refresh_leads():
    customers = db.get_all_customers_geocoded()
    logger.info(f"Loaded {len(customers)} geocoded customers as reference points")

    elements = fetch_properties_overpass()
    if not elements:
        logger.error("No elements from Overpass — aborting")
        return

    records = parse_overpass_elements(elements)
    updated = 0
    skipped = 0

    for rec in records:
        lat, lng = rec["lat"], rec["lng"]
        if is_existing_customer(lat, lng, customers):
            skipped += 1
            continue
        year_built = extract_year_built(rec.get("start_date", ""))
        score, nearest_m = score_lead(lat, lng, customers, year_built)
        db.upsert_lead(
            address            = rec["address"],
            lat                = lat,
            lng                = lng,
            city               = rec["city"],
            postal_code        = rec["postal_code"],
            score              = score,
            nearest_customer_m = nearest_m,
        )
        updated += 1
        if updated % 500 == 0:
            logger.info(f"  …{updated:,} leads written")

    logger.info(f"Done — {updated:,} leads upserted, {skipped} customer addresses skipped")


def run_setup():
    """Setup: geocode customers (loaded via Supabase dashboard) then fetch leads."""
    logger.info("=== SETUP MODE ===")
    geocode_customers(batch=400)
    refresh_leads()
    logger.info("=== SETUP COMPLETE ===")


def run_daily_refresh():
    logger.info("=== DAILY REFRESH ===")
    geocode_customers(batch=50)
    refresh_leads()
    logger.info("=== DAILY REFRESH COMPLETE ===")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "refresh"
    if mode == "setup":
        run_setup()
    else:
        run_daily_refresh()
