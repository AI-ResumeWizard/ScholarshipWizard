"""Streamlit dashboard for the Scholarship Scraper."""

import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta

import streamlit as st

st.set_page_config(
    page_title="Scholarship Dashboard",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

SCHOLARSHIPS_FILE = "scholarships.json"


# ── Data helpers ──────────────────────────────────────────────────────────────
def load_data():
    if not os.path.exists(SCHOLARSHIPS_FILE):
        return None
    with open(SCHOLARSHIPS_FILE) as f:
        return json.load(f)


def parse_amount_numeric(s):
    nums = re.findall(r'[\d,]+', s)
    vals = []
    for n in nums:
        try:
            v = int(n.replace(',', ''))
            if v >= 500:
                vals.append(v)
        except ValueError:
            pass
    return max(vals) if vals else 0


def parse_deadline_date(s):
    if not s:
        return None
    MONTHS = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }
    m = re.search(
        r'(january|february|march|april|may|june|july|august|'
        r'september|october|november|december)\s+(\d{1,2})',
        s.lower()
    )
    if not m:
        return None
    try:
        today = date.today()
        month_num = MONTHS[m.group(1)]
        day = int(m.group(2))
        dl = date(today.year, month_num, day)
        if dl < today:
            dl = date(today.year + 1, month_num, day)
        return dl
    except ValueError:
        return None


# ── Live re-scoring based on active profile flags ─────────────────────────────
def compute_score(s, flags):
    cats = s.get("cats", {})
    text = (s.get("title", "") + " " + s.get("raw_text", "")).lower()
    pts = 1
    reasons = []

    if cats.get("degree_level") == "undergrad only":
        return 1, ["undergrad only"]
    if cats.get("degree_level") == "PhD only":
        return 2, ["PhD only"]
    if re.search(r'\bfor women only\b|\bwomen[- ]only\b', text):
        return 1, ["women-only"]

    if flags["masters"]:
        if cats.get("degree_level") == "graduate":
            pts += 2
            reasons.append("graduate level — direct match")
        elif cats.get("degree_level") == "any":
            pts += 1
            reasons.append("open to graduate students")

    field = cats.get("field", "")
    if field == "AI + business/strategy":
        pts += 3
        reasons.append("AI + business/strategy — strongest field match")
    elif field in ("AI/ML", "business/strategy"):
        pts += 2
        reasons.append(f"{field} — strong match")
    elif field in ("CS/engineering", "STEM"):
        pts += 1
        reasons.append(f"{field} — partial match")

    if flags["jewish"] and "Jewish" in cats.get("identity_criteria", ""):
        pts += 3
        reasons.append("Jewish identity — direct match")

    if flags["adult_learner"] and cats.get("age_restriction") == "50+ / adult learner":
        pts += 2
        reasons.append("adult learner / 50+ — direct match")

    if flags["age_57plus"] and any(
        w in text for w in ["non-traditional", "returning student", "career change", "re-entry"]
    ):
        pts += 1
        reasons.append("non-traditional / career-change focus")

    if flags["michigan"] and (
        "michigan" in text or "Michigan" in cats.get("state_required", "")
    ):
        pts += 1
        reasons.append("Michigan residency match")

    if flags["osu"] and any(w in text for w in ["ohio state", "osu alum", "buckeye"]):
        pts += 1
        reasons.append("Ohio State alumnus match")

    if flags["wake_forest"] and any(w in text for w in ["wake forest", "wfu"]):
        pts += 1
        reasons.append("Wake Forest — currently enrolled")

    return max(1, min(10, pts)), reasons


# ── Visual helpers ────────────────────────────────────────────────────────────
def score_color(n):
    if n >= 8:
        return "#2e7d32"
    if n >= 5:
        return "#e65100"
    return "#757575"


def score_label(n):
    if n >= 8:
        return "HIGH"
    if n >= 5:
        return "MED"
    return "LOW"


def render_card(s):
    score = s.get("score", 0)
    reasons = s.get("score_reasons", [])
    cats = s.get("cats", {})
    color = score_color(score)
    label = score_label(score)

    with st.container(border=True):
        col_left, col_right = st.columns([5, 2])

        with col_left:
            st.markdown(
                f'<span style="background:{color};color:#fff;padding:3px 10px;border-radius:12px;'
                f'font-size:12px;font-weight:700;">{label} {score}/10</span>'
                + ('&nbsp;<span style="background:#e3f2fd;color:#1565c0;padding:3px 9px;'
                   'border-radius:12px;font-size:11px;font-weight:600;">CURATED</span>'
                   if s.get("source") == "curated" else ""),
                unsafe_allow_html=True,
            )
            st.markdown(f"**{s['title']}**")
            st.caption(s.get("provider", ""))

            # Score bar
            st.markdown(
                f'<div style="background:#e0e0e0;border-radius:4px;height:7px;margin:6px 0 8px;">'
                f'<div style="background:{color};width:{score * 10}%;height:7px;border-radius:4px;'
                f'transition:width 0.3s;"></div></div>',
                unsafe_allow_html=True,
            )

            if reasons:
                st.markdown("**Why it matches:** " + " · ".join(f"✓ {r}" for r in reasons[:4]))

            meta_bits = []
            dl = cats.get("degree_level", "")
            if dl and dl not in ("any", ""):
                meta_bits.append(dl.title())
            if cats.get("field") and cats["field"] != "general":
                meta_bits.append(cats["field"])
            if cats.get("identity_criteria") and cats["identity_criteria"] != "open":
                meta_bits.append(cats["identity_criteria"])
            if cats.get("state_required") and cats["state_required"] != "any":
                meta_bits.append(f"📍 {cats['state_required']}")
            if cats.get("age_restriction") and cats["age_restriction"] != "open":
                meta_bits.append(f"👤 {cats['age_restriction']}")
            if meta_bits:
                st.caption(" · ".join(meta_bits))

        with col_right:
            st.metric("Award", s.get("amount", "Varies"))
            st.caption(f"📅 {s.get('deadline', 'Check website')}")
            url = s.get("url", "#")
            if url and url != "#":
                st.link_button("Apply →", url, use_container_width=True)


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🎓 Scholarship Wizard")
    st.caption("Wake Forest MS AI Strategy")
    st.divider()

    st.subheader("Filters")
    min_amount = st.slider(
        "Minimum Award Amount", 0, 50_000, 0, 500, format="$%d"
    )
    deadline_days = st.selectbox(
        "Deadline within",
        [30, 60, 90, 180, 365],
        index=4,
        format_func=lambda x: f"{x} days",
    )
    field_focus = st.selectbox(
        "Field Focus",
        ["All", "Business & Strategy", "Pure CS", "Policy & Government"],
    )

    st.divider()
    st.subheader("My Profile")
    st.caption("Uncheck to hide scholarships that require that attribute.")

    flags = {
        "jewish":        st.checkbox("✡ Jewish identity",      True),
        "michigan":      st.checkbox("📍 Michigan resident",    True),
        "osu":           st.checkbox("🔴 Ohio State alum",      True),
        "age_57plus":    st.checkbox("👤 Age 57+",              True),
        "adult_learner": st.checkbox("📚 Adult learner",        True),
        "masters":       st.checkbox("🎓 Masters student",      True),
        "wake_forest":   st.checkbox("🏫 Wake Forest enrolled", True),
    }

    st.divider()
    run_clicked = st.button(
        "🔄 Run Scrape Now", type="primary", use_container_width=True
    )
    st.caption(
        "Runs scraper.py — requires EMAIL env vars to be set. "
        "Also triggers your weekly digest email."
    )


# ── Handle scrape run ─────────────────────────────────────────────────────────
if run_clicked:
    with st.spinner("Scraping scholarships… this takes about 2 minutes."):
        try:
            result = subprocess.run(
                [sys.executable, "scraper.py"],
                capture_output=True, text=True, timeout=360,
            )
        except subprocess.TimeoutExpired:
            st.error("Scrape timed out after 6 minutes. Try again.")
            st.stop()
    if result.returncode == 0:
        st.success("Scrape complete — data refreshed.")
        st.rerun()
    else:
        st.error("Scrape failed. Check that EMAIL env vars are set.")
        with st.expander("Error details"):
            st.code(result.stderr[-2000:] or result.stdout[-2000:])


# ── Load data ─────────────────────────────────────────────────────────────────
data = load_data()

if data is None:
    st.title("🎓 Scholarship Dashboard")
    st.info(
        "**No scholarship data yet.**\n\n"
        "Click **Run Scrape Now** in the sidebar to fetch scholarships for the first time, "
        "or wait for the Monday 8 am UTC cron job on Render.",
        icon="ℹ️",
    )
    st.stop()

raw_scholarships = data.get("scholarships", [])
scraped_at = data.get("scraped_at", "")

# ── Page header ───────────────────────────────────────────────────────────────
st.title("🎓 Scholarship Dashboard")
if scraped_at:
    try:
        dt = datetime.fromisoformat(scraped_at)
        st.caption(f"Last scraped: {dt.strftime('%B %d, %Y at %I:%M %p')}")
    except Exception:
        st.caption(f"Last scraped: {scraped_at}")

# ── Search box ────────────────────────────────────────────────────────────────
search_query = st.text_input(
    "🔍 Search scholarships",
    placeholder="e.g., Jewish, Michigan, AI strategy, fellowship…",
)

# ── Filter & re-score ─────────────────────────────────────────────────────────
cutoff_date = date.today() + timedelta(days=deadline_days)
filtered = []

for s in raw_scholarships:
    score, reasons = compute_score(s, flags)
    s = {**s, "score": score, "score_reasons": reasons}

    # Identity filter: hide Jewish-specific scholarships when Jewish flag is off
    cats = s.get("cats", {})
    if "Jewish" in cats.get("identity_criteria", "") and not flags["jewish"]:
        continue

    # Amount filter (only apply when we have a known amount)
    amount_num = s.get("amount_numeric") or parse_amount_numeric(s.get("amount", ""))
    if amount_num > 0 and amount_num < min_amount:
        continue

    # Deadline filter (unknowns pass through)
    dl_str = s.get("deadline_parsed") or ""
    if dl_str:
        try:
            dl = date.fromisoformat(dl_str) if isinstance(dl_str, str) else dl_str
            if dl > cutoff_date:
                continue
        except (ValueError, TypeError):
            pass

    # Field focus filter
    field = cats.get("field", "")
    text_low = (s.get("title", "") + " " + s.get("raw_text", "")).lower()
    if field_focus == "Business & Strategy":
        if "strategy" not in field.lower() and "business" not in field.lower():
            continue
    elif field_focus == "Pure CS":
        if not any(w in field.lower() for w in ("cs", "engineering", "ai")):
            continue
    elif field_focus == "Policy & Government":
        if not any(w in text_low for w in ("policy", "government", "federal", "public sector")):
            continue

    # Keyword search
    if search_query:
        haystack = " ".join([
            s.get("title", ""),
            s.get("provider", ""),
            " ".join(s.get("score_reasons", [])),
            s.get("raw_text", ""),
        ]).lower()
        if search_query.lower() not in haystack:
            continue

    filtered.append(s)

filtered.sort(key=lambda x: x.get("score", 0), reverse=True)

high   = [s for s in filtered if s.get("score", 0) >= 8]
medium = [s for s in filtered if 5 <= s.get("score", 0) <= 7]
low    = [s for s in filtered if s.get("score", 0) < 5]

est_funding = sum(
    s.get("amount_numeric") or parse_amount_numeric(s.get("amount", ""))
    for s in high
    if (s.get("amount_numeric") or parse_amount_numeric(s.get("amount", ""))) > 0
)

# ── Metric cards ──────────────────────────────────────────────────────────────
c1, c2, c3 = st.columns(3)
c1.metric("Total Matches", len(filtered), help="After applying all sidebar filters.")
c2.metric("High Matches (8–10)", len(high))
c3.metric(
    "Est. Funding Available",
    f"${est_funding:,}" if est_funding else "—",
    help="Sum of known award amounts for HIGH match scholarships only.",
)

st.divider()

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs([
    f"🏆 HIGH MATCH  ({len(high)})",
    f"📊 MEDIUM MATCH  ({len(medium)})",
    f"📋 All Scholarships  ({len(filtered)})",
])

with tab1:
    if high:
        for s in high:
            render_card(s)
    else:
        st.info("No high-match scholarships match your current filters.")

with tab2:
    if medium:
        for s in medium:
            render_card(s)
    else:
        st.info("No medium-match scholarships match your current filters.")

with tab3:
    if filtered:
        for s in filtered:
            render_card(s)
    else:
        st.info("No scholarships match your current filters. Try loosening the filters in the sidebar.")
