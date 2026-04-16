"""
app.py — Olive Tree Lead Machine
Streamlit web app with three modes:
  A) Daily Sniper List — top scored unvisited leads + one-tap Google Maps route
  B) Job Proximity    — leads within X km of today's active job site
  C) Territory Map    — full heat-map of the service area
"""

import urllib.parse
import streamlit as st
import folium
from streamlit_folium import st_folium

import database as db

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Olive Tree Lead Machine",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    /* Tighten up mobile padding */
    .block-container { padding-top: 1rem; padding-bottom: 1rem; }

    /* Route button */
    .route-btn a {
        display: block;
        background: #28a745;
        color: white !important;
        text-align: center;
        padding: 14px;
        border-radius: 8px;
        font-size: 17px;
        font-weight: 600;
        text-decoration: none;
        margin-bottom: 12px;
    }
    .route-btn a:hover { background: #218838; }

    /* Score badge colours */
    .hot  { color: #dc3545; font-weight: 700; }
    .warm { color: #fd7e14; font-weight: 700; }
    .cold { color: #6c757d; font-weight: 700; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def score_badge(score: float) -> str:
    if score >= 70:
        return f'<span class="hot">🔥 {score:.0f}</span>'
    elif score >= 50:
        return f'<span class="warm">⚡ {score:.0f}</span>'
    return f'<span class="cold">❄️ {score:.0f}</span>'


def google_maps_route_url(leads: list[dict]) -> str | None:
    """Build a Google Maps driving route URL for up to 10 stops."""
    if not leads:
        return None
    stops = leads[:10]
    encoded = [urllib.parse.quote(s["address"]) for s in stops]
    if len(encoded) == 1:
        return f"https://www.google.com/maps/search/?api=1&query={encoded[0]}"
    waypoints = "|".join(encoded[1:-1])
    base = (
        f"https://www.google.com/maps/dir/?api=1"
        f"&destination={encoded[-1]}"
        f"&travelmode=driving"
    )
    if waypoints:
        base += f"&waypoints={waypoints}"
    return base


def geocode_single(address: str) -> tuple[float | None, float | None]:
    """Geocode one address via Nominatim (used in Job Proximity mode)."""
    from geopy.geocoders import Nominatim
    geolocator = Nominatim(user_agent="olive_tree_exteriors_lead_machine")
    try:
        loc = geolocator.geocode(address + ", Ontario, Canada", timeout=10)
        if loc:
            return loc.latitude, loc.longitude
    except Exception as exc:
        st.error(f"Geocoding failed: {exc}")
    return None, None


OUTCOME_BUTTONS = [
    ("✅ Booked", "booked_assessment"),
    ("🔁 Callback",  "callback"),
    ("🚫 Not Int.",  "not_interested"),
    ("🏠 Not Home",  "not_home"),
]


def outcome_row(lead_id: int, key_prefix: str):
    """Render four outcome buttons for a lead."""
    cols = st.columns(4)
    for col, (label, outcome) in zip(cols, OUTCOME_BUTTONS):
        with col:
            if st.button(label, key=f"{key_prefix}_{lead_id}_{outcome}"):
                db.mark_visited(lead_id, outcome)
                st.success("Saved!")
                st.rerun()


# ── Mode A — Daily Sniper List ────────────────────────────────────────────────

def render_daily_list():
    st.header("📋 Today's Sniper List")

    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        limit = st.slider("Leads to show", 10, 50, 25, key="dl_limit")
    with col2:
        show_visited = st.checkbox("Include visited", False, key="dl_visited")
    with col3:
        if st.button("🔄 Refresh", key="dl_refresh"):
            st.cache_data.clear()
            st.rerun()

    leads = db.get_daily_leads(limit=limit, exclude_visited=not show_visited)

    if not leads:
        st.warning("No leads loaded yet. Use the **⚙️ Run Setup** button in the sidebar to populate the database.")
        return

    # ── Route CTA ──
    route_url = google_maps_route_url(leads)
    if route_url:
        st.markdown(
            f'<div class="route-btn"><a href="{route_url}" target="_blank">'
            f"📍 Open Today's Route in Google Maps (Top {min(10, len(leads))} stops)"
            f"</a></div>",
            unsafe_allow_html=True,
        )

    st.caption(f"Showing {len(leads)} leads · sorted by score · unvisited first")
    st.markdown("---")

    # ── Lead cards ──
    for i, lead in enumerate(leads, 1):
        city_str = f" — {lead['city']}" if lead.get("city") else ""
        dist_str = ""
        if lead.get("nearest_customer_m"):
            d = lead["nearest_customer_m"]
            dist_str = f"  |  {d:.0f} m to nearest customer" if d < 1000 else f"  |  {d/1000:.1f} km to nearest customer"

        header = f"#{i}  {lead['address'][:55]}…{city_str}"
        with st.expander(header, expanded=(i <= 3)):
            c1, c2 = st.columns([3, 1])
            with c1:
                st.markdown(f"**Address:** {lead['address']}")
                if lead.get("city"):
                    st.markdown(f"**City:** {lead['city']}")
                if dist_str:
                    st.markdown(f"**Customer proximity:**{dist_str}")
                maps_link = urllib.parse.quote(lead["address"])
                st.markdown(
                    f'<a href="https://www.google.com/maps/search/?api=1&query={maps_link}" target="_blank">📍 View on Google Maps</a>',
                    unsafe_allow_html=True,
                )
            with c2:
                st.markdown(f"**Score**", unsafe_allow_html=True)
                st.progress(int(lead["score"]) / 100)
                st.markdown(score_badge(lead["score"]), unsafe_allow_html=True)

            st.markdown("**Record outcome:**")
            outcome_row(lead["id"], "dl")


# ── Mode B — Job Proximity ────────────────────────────────────────────────────

def render_job_proximity():
    st.header("🎯 Job Proximity Leads")
    st.markdown("Enter today's job address to find the best nearby doors to knock while the crew is on site.")

    job_address = st.text_input(
        "Today's job address",
        placeholder="e.g. 301 Ray Street Port Elgin",
        key="jp_address",
    )
    c1, c2 = st.columns(2)
    with c1:
        radius = st.slider("Radius (km)", 0.5, 5.0, 1.5, 0.5, key="jp_radius")
    with c2:
        limit = st.number_input("Max leads", 5, 30, 15, key="jp_limit")

    if st.button("🔍 Find Nearby Leads", type="primary", key="jp_search") and job_address:
        with st.spinner("Locating job site and finding leads…"):
            lat, lng = geocode_single(job_address)

        if not lat:
            st.error("Couldn't find that address. Try including the city name.")
            return

        leads = db.get_leads_near(lat, lng, radius_km=radius, limit=int(limit))

        if not leads:
            st.warning(f"No unvisited leads within {radius} km. Try a larger radius.")
            return

        st.success(f"Found **{len(leads)}** leads within {radius} km")

        # Map
        m = folium.Map(location=[lat, lng], zoom_start=14, tiles="CartoDB positron")
        folium.Marker(
            [lat, lng],
            popup="<b>TODAY'S JOB</b>",
            icon=folium.Icon(color="red", icon="home", prefix="fa"),
            tooltip="Job site",
        ).add_to(m)
        for i, lead in enumerate(leads, 1):
            folium.Marker(
                [lead["lat"], lead["lng"]],
                popup=f"<b>#{i}</b><br>{lead['address']}<br>Score: {lead['score']:.0f}<br>{lead.get('distance_m', 0)} m away",
                icon=folium.Icon(color="green", icon="info-sign"),
                tooltip=f"#{i} · Score {lead['score']:.0f}",
            ).add_to(m)
        st_folium(m, height=380, width=None, returned_objects=[])

        # Route button
        route_url = google_maps_route_url(leads)
        if route_url:
            st.markdown(
                f'<div class="route-btn"><a href="{route_url}" target="_blank">'
                f"📍 Open Route in Google Maps"
                f"</a></div>",
                unsafe_allow_html=True,
            )

        st.markdown("---")
        for i, lead in enumerate(leads, 1):
            with st.expander(f"#{i}  {lead['address'][:55]}…  ({lead.get('distance_m', 0)} m away)"):
                c1, c2 = st.columns([3, 1])
                with c1:
                    st.markdown(f"**Address:** {lead['address']}")
                    st.markdown(f"**Distance:** {lead.get('distance_m', 0)} m from job site")
                with c2:
                    st.progress(int(lead["score"]) / 100)
                    st.markdown(score_badge(lead["score"]), unsafe_allow_html=True)
                st.markdown("**Record outcome:**")
                outcome_row(lead["id"], "jp")


# ── Mode C — Territory Map ────────────────────────────────────────────────────

def render_territory_map():
    st.header("🗺️ Territory Map")

    # Stats
    stats = db.get_stats()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Leads", f"{stats['total_leads']:,}")
    c2.metric("Doors Knocked", stats["visited"])
    c3.metric("Assessments Booked", stats["booked"])
    conv = (stats["booked"] / stats["visited"] * 100) if stats["visited"] else 0
    c4.metric("D2D Close Rate", f"{conv:.1f}%")

    st.markdown("---")

    col1, col2, col3 = st.columns(3)
    with col1:
        show_customers = st.checkbox("Show existing customers", True, key="tm_cust")
    with col2:
        min_score = st.slider("Min score", 0, 90, 30, key="tm_score")
    with col3:
        show_visited = st.checkbox("Show visited leads", True, key="tm_visited")

    all_leads   = db.get_all_leads_for_map(min_score=min_score)
    customers   = db.get_all_customers_geocoded() if show_customers else []

    # Center on Port Elgin
    m = folium.Map(location=[44.45, -81.38], zoom_start=10, tiles="CartoDB positron")

    # Existing customers — blue circles
    for c in customers:
        if c.get("lat") and c.get("lng"):
            folium.CircleMarker(
                [c["lat"], c["lng"]],
                radius=5,
                color="#0d6efd",
                fill=True,
                fill_color="#0d6efd",
                fill_opacity=0.75,
                popup=f"<b>CUSTOMER</b><br>{c['name']}<br>{c['address']}",
                tooltip=c["name"],
            ).add_to(m)

    # Leads
    for lead in all_leads:
        if not lead.get("lat") or not lead.get("lng"):
            continue

        outcome = lead.get("visit_outcome")

        if outcome == "booked_assessment":
            color, radius = "#28a745", 7   # green star
        elif outcome in ("callback", "not_home"):
            color, radius = "#ffc107", 5   # yellow
        elif outcome == "not_interested":
            if not show_visited:
                continue
            color, radius = "#6c757d", 4   # grey
        else:
            # Unvisited — heat by score
            if lead["score"] >= 70:
                color, radius = "#dc3545", 6   # red hot
            elif lead["score"] >= 50:
                color, radius = "#fd7e14", 5   # orange warm
            else:
                color, radius = "#adb5bd", 4   # grey cold

        folium.CircleMarker(
            [lead["lat"], lead["lng"]],
            radius=radius,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.8,
            popup=(
                f"<b>{lead['address']}</b><br>"
                f"Score: {lead['score']:.0f}<br>"
                f"Status: {outcome or 'Not yet visited'}"
            ),
        ).add_to(m)

    # Legend
    legend = """
    <div style="position:fixed;bottom:30px;left:30px;z-index:1000;
                background:white;padding:12px 16px;border-radius:8px;
                border:1px solid #ccc;font-size:13px;line-height:1.8;">
      <b>Legend</b><br>
      <span style="color:#0d6efd;">●</span> Existing customer<br>
      <span style="color:#dc3545;">●</span> Hot lead (70+)<br>
      <span style="color:#fd7e14;">●</span> Warm lead (50–70)<br>
      <span style="color:#adb5bd;">●</span> Cold lead (&lt;50)<br>
      <span style="color:#28a745;">●</span> Assessment booked<br>
      <span style="color:#ffc107;">●</span> Callback / not home<br>
      <span style="color:#6c757d;">●</span> Not interested
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend))

    st_folium(m, height=600, width=None, returned_objects=[])


# ── Sidebar ───────────────────────────────────────────────────────────────────

def sidebar():
    with st.sidebar:
        st.markdown("## 🌿 Olive Tree\nLead Machine")
        st.markdown("---")

        mode = st.radio(
            "View",
            ["📋 Daily List", "🎯 Job Proximity", "🗺️ Territory Map"],
            index=0,
            key="nav_mode",
        )

        st.markdown("---")
        st.markdown("**Live Stats**")
        try:
            s = db.get_stats()
            st.markdown(f"🏠 **{s['total_leads']:,}** leads in database")
            st.markdown(f"👣 **{s['visited']}** doors knocked")
            st.markdown(f"✅ **{s['booked']}** assessments booked")
            st.markdown(f"📍 **{s['customers']}** existing customers mapped")
        except Exception:
            st.caption("Connect to Supabase to see stats")

        st.markdown("---")
        with st.expander("⚙️ Admin"):
            st.caption("First-time setup or manual refresh")
            if st.button("▶ Run Full Setup", key="admin_setup"):
                with st.spinner("Running setup — this takes a few minutes on first run…"):
                    import pipeline
                    pipeline.run_setup()
                st.success("Setup complete! Reload the page.")

            if st.button("🔄 Daily Refresh", key="admin_refresh"):
                with st.spinner("Refreshing lead scores…"):
                    import pipeline
                    pipeline.run_daily_refresh()
                st.success("Leads refreshed!")
                st.rerun()

    return mode


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    mode = sidebar()

    if mode == "📋 Daily List":
        render_daily_list()
    elif mode == "🎯 Job Proximity":
        render_job_proximity()
    elif mode == "🗺️ Territory Map":
        render_territory_map()


if __name__ == "__main__":
    main()
