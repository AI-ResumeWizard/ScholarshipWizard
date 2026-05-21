"""Streamlit dashboard for the Scholarship Scraper — full filter suite."""

import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from urllib.parse import quote as _url_quote, urlparse as _urlparse

import streamlit as st

st.set_page_config(
    page_title="Scholarship Dashboard",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

SCHOLARSHIPS_FILE = "scholarships.json"

# ── Option lists ──────────────────────────────────────────────────────────────
SORT_OPTIONS    = ["Best match", "Amount (high → low)", "Deadline (soonest)", "Newest first"]
DEADLINE_OPT    = [7, 30, 60, 90, 180, 365, 9999]
FIELD_OPTIONS   = ["All", "AI & Machine Learning", "Business & Strategy",
                   "Policy & Government", "Data Science", "Cybersecurity",
                   "Robotics", "Ethics & Society"]
FUNDING_TYPES   = ["All", "Full tuition", "Partial", "Stipend", "One-time", "Renewable"]
DEGREE_OPTIONS  = ["All", "Masters", "PhD", "Masters + PhD"]
CITIZEN_OPTIONS = ["All", "US Citizen only", "Permanent Resident OK", "International OK"]
ENROLL_OPTIONS  = ["All", "Full-time", "Part-time"]
SCHOOL_OPTIONS  = ["All", "Wake Forest", "Any university"]
PRESET_NAMES    = ["My Full Profile", "Max Money", "Apply This Week",
                   "Long Shots", "Quick Wins", "Reset All"]

# ── Preset configurations ────────────────────────────────────────────────────
_BASE = dict(
    f_amount_range=(0, 100_000), f_include_unknown=True, f_funding_type="All",
    f_id_jewish=False, f_id_female=False, f_id_veteran=False, f_id_lgbtq=False,
    f_id_hispanic=False, f_id_black=False, f_id_native=False, f_id_asian=False,
    f_id_firstgen=False, f_id_disability=False,
    f_loc_michigan=False, f_loc_ohio=False, f_loc_any_us=True, f_loc_international=False,
    f_age_no_restrict=True, f_age_range=(18, 80),
    f_citizenship="All",
    f_degree_level="All", f_gpa_no_req=True, f_gpa_min=2.0,
    f_field_focus="All", f_enrollment="All", f_school="All",
    f_deadline_days=365, f_show_expired=False, f_show_rolling=True,
    f_source_mode="Both",
    f_keyword="", f_exclude_kw="", f_sort_by="Best match",
    f_pf_jewish=True, f_pf_michigan=True, f_pf_osu=True, f_pf_age57=True,
    f_pf_adult=True, f_pf_masters=True, f_pf_wfu=True,
)

def _preset(**overrides):
    return {**_BASE, **overrides}

PRESET_DEFS = {
    "My Full Profile": _preset(f_id_jewish=True, f_loc_michigan=True),
    "Max Money":       _preset(f_amount_range=(5_000, 100_000), f_include_unknown=False,
                                f_deadline_days=9999,
                                f_sort_by="Amount (high → low)"),
    "Apply This Week": _preset(f_deadline_days=7, f_show_rolling=True,
                                f_sort_by="Deadline (soonest)"),
    "Long Shots":      _preset(f_amount_range=(10_000, 100_000), f_include_unknown=False,
                                f_citizenship="US Citizen only", f_deadline_days=9999,
                                f_sort_by="Amount (high → low)"),
    "Quick Wins":      _preset(f_id_jewish=True, f_loc_michigan=True,
                                f_deadline_days=9999, f_show_rolling=True),
    "Reset All":       _preset(f_pf_jewish=False, f_pf_michigan=False, f_pf_osu=False,
                                f_pf_age57=False, f_pf_adult=False, f_pf_masters=False,
                                f_pf_wfu=False, f_deadline_days=9999),
}


# ── Data / parsing helpers ────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_data():
    if not os.path.exists(SCHOLARSHIPS_FILE):
        return None
    with open(SCHOLARSHIPS_FILE) as f:
        return json.load(f)


def parse_amount_numeric(s):
    nums = re.findall(r'[\d,]+', str(s))
    vals = [int(n.replace(',', '')) for n in nums
            if n.replace(',', '').isdigit() and int(n.replace(',', '')) >= 500]
    return max(vals) if vals else 0


def parse_deadline_date(s):
    if not s:
        return None
    MONTHS = {"january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
              "july":7,"august":8,"september":9,"october":10,"november":11,"december":12}
    m = re.search(r'(january|february|march|april|may|june|july|august|'
                  r'september|october|november|december)\s+(\d{1,2})', str(s).lower())
    if not m:
        return None
    try:
        today = date.today()
        dl = date(today.year, MONTHS[m.group(1)], int(m.group(2)))
        if dl < today:
            dl = date(today.year + 1, MONTHS[m.group(1)], int(m.group(2)))
        return dl
    except ValueError:
        return None


def is_rolling(s):
    return "rolling" in str(s.get("deadline", "")).lower()


def is_homepage_url(url):
    """True when the URL path carries no scholarship-specific info (root domain only)."""
    if not url or url in ("#", ""):
        return True
    try:
        return _urlparse(url).path.rstrip("/") == ""
    except Exception:
        return False


def _safe_url(url):
    """Escape URL for safe embedding in HTML attributes and JS strings."""
    return re.sub(r"[\"'<>`\n\r]", "", url or "")


# ── Live re-scoring ───────────────────────────────────────────────────────────
def compute_score(s, pf):
    cats = s.get("cats", {})
    text = (s.get("title", "") + " " + s.get("raw_text", "")).lower()
    pts, reasons = 1, []

    if cats.get("degree_level") == "undergrad only":
        return 1, ["undergrad only"]
    if cats.get("degree_level") == "PhD only":
        return 2, ["PhD only"]
    if re.search(r'\bfor women only\b|\bwomen[- ]only\b', text):
        return 1, ["women-only"]

    if pf["f_pf_masters"]:
        if cats.get("degree_level") == "graduate":
            pts += 2; reasons.append("graduate level — direct match")
        elif cats.get("degree_level") == "any":
            pts += 1; reasons.append("open to graduate students")

    field = cats.get("field", "")
    if field == "AI + business/strategy":
        pts += 3; reasons.append("AI + business/strategy — strongest field match")
    elif field in ("AI/ML", "business/strategy"):
        pts += 2; reasons.append(f"{field} — strong match")
    elif field in ("CS/engineering", "STEM"):
        pts += 1; reasons.append(f"{field} — partial match")

    if pf["f_pf_jewish"] and "Jewish" in cats.get("identity_criteria", ""):
        pts += 3; reasons.append("Jewish identity — direct match")

    if pf["f_pf_adult"] and cats.get("age_restriction") == "50+ / adult learner":
        pts += 2; reasons.append("adult learner / 50+ — direct match")
    if pf["f_pf_age57"] and any(w in text for w in
            ["non-traditional", "returning student", "career change", "re-entry"]):
        pts += 1; reasons.append("non-traditional / career-change focus")

    if pf["f_pf_michigan"] and ("michigan" in text or
            "Michigan" in cats.get("state_required", "")):
        pts += 1; reasons.append("Michigan residency match")

    if pf["f_pf_osu"] and any(w in text for w in ["ohio state", "osu alum", "buckeye"]):
        pts += 1; reasons.append("Ohio State alumnus match")

    if pf["f_pf_wfu"] and any(w in text for w in ["wake forest", "wfu"]):
        pts += 1; reasons.append("Wake Forest — currently enrolled")

    return max(1, min(10, pts)), reasons


# ── Card renderer ─────────────────────────────────────────────────────────────
def score_color(n):
    return "#2e7d32" if n >= 8 else ("#e65100" if n >= 5 else "#757575")

def score_label(n):
    return "HIGH" if n >= 8 else ("MED" if n >= 5 else "LOW")

def render_card(s):
    score    = s.get("score", 0)
    reasons  = s.get("score_reasons", [])
    cats     = s.get("cats", {})
    color    = score_color(score)
    url      = s.get("url", "") or ""
    title    = s.get("title", "Untitled")
    provider = s.get("provider", "")
    homepage = is_homepage_url(url)
    src_url  = s.get("source_url", "")

    # Google fallback search URL
    gq  = _url_quote(f"{title} {provider} scholarship apply")
    goo = f"https://www.google.com/search?q={gq}"

    # URL safe for HTML attributes / JS (strip chars that break quoting)
    su   = _safe_url(url)
    su_g = _safe_url(goo)
    url_disp = (url[:65] + "…") if len(url) > 68 else url

    with st.container(border=True):
        left, right = st.columns([5, 2])

        with left:
            # ── Badge row ────────────────────────────────────────────────────
            badges = (
                f'<span style="background:{color};color:#fff;padding:3px 10px;'
                f'border-radius:12px;font-size:12px;font-weight:700;">'
                f'{score_label(score)} {score}/10</span>'
            )
            if s.get("source") == "curated":
                badges += ('&nbsp;<span style="background:#e3f2fd;color:#1565c0;'
                           'padding:3px 9px;border-radius:12px;font-size:11px;'
                           'font-weight:600;">CURATED</span>')
            if homepage:
                badges += ('&nbsp;<span style="background:#fff3cd;color:#856404;'
                           'padding:3px 9px;border-radius:12px;font-size:11px;'
                           'font-weight:600;" title="URL points to a homepage — '
                           'use Search Google to find the direct page">⚠ Verify link</span>')
            st.markdown(badges, unsafe_allow_html=True)

            st.markdown(f"**{title}**")
            st.caption(provider)

            # ── Score bar ────────────────────────────────────────────────────
            st.markdown(
                f'<div style="background:#e0e0e0;border-radius:4px;height:7px;margin:5px 0 8px;">'
                f'<div style="background:{color};width:{score*10}%;height:7px;'
                f'border-radius:4px;"></div></div>',
                unsafe_allow_html=True,
            )
            if reasons:
                st.markdown("**Why it matches:** " + " · ".join(f"✓ {r}" for r in reasons[:4]))

            meta = " · ".join(filter(None, [
                cats.get("degree_level","").title() if cats.get("degree_level") not in ("any","") else "",
                cats.get("field","") if cats.get("field") != "general" else "",
                cats.get("identity_criteria","") if cats.get("identity_criteria") != "open" else "",
                f'📍 {cats["state_required"]}' if cats.get("state_required","any") != "any" else "",
                f'👤 {cats["age_restriction"]}' if cats.get("age_restriction","open") != "open" else "",
            ]))
            if meta:
                st.caption(meta)

        with right:
            st.metric("Award", s.get("amount", "Varies"))
            st.caption(f"📅 {s.get('deadline','Check website')}")

        # ── Action row (full-width, below both columns) ───────────────────────
        apply_btn = (
            f'<a href="{su}" target="_blank" rel="noopener noreferrer" '
            f'style="display:inline-flex;align-items:center;padding:6px 15px;'
            f'background:#e53935;color:#fff;border-radius:5px;text-decoration:none;'
            f'font-size:13px;font-weight:600;white-space:nowrap;">Apply →</a>'
        ) if su else ""

        copy_js = (
            f"(function(b){{navigator.clipboard.writeText('{su}')"
            f".then(function(){{b.textContent='✓ Copied';setTimeout(function()"
            f"{{b.textContent='📋 Copy'}},2000)}})"
            f".catch(function(){{b.textContent='📋 Copy'}})}})(this)"
        )

        source_note = ""
        if src_url and src_url != url:
            ss = _safe_url(src_url)
            source_note = (
                f'<a href="{ss}" target="_blank" rel="noopener noreferrer" '
                f'style="font-size:11px;color:#bbb;text-decoration:none;" '
                f'title="Original listing page">↩ listing</a>'
            )

        st.markdown(
            f"""<div style="display:flex;align-items:center;gap:8px;
                            margin-top:10px;padding-top:8px;
                            border-top:1px solid #f0f0f0;flex-wrap:wrap;">
              {apply_btn}
              <a href="{su_g}" target="_blank" rel="noopener noreferrer"
                 style="display:inline-flex;align-items:center;padding:6px 13px;
                        background:#1a73e8;color:#fff;border-radius:5px;
                        text-decoration:none;font-size:13px;font-weight:600;
                        white-space:nowrap;">🔍 Search Google</a>
              <a href="{su}" target="_blank" rel="noopener noreferrer"
                 style="flex:1;min-width:120px;font-size:11px;color:#bbb;
                        text-decoration:none;word-break:break-all;line-height:1.4;"
                 title="{su}">{url_disp}</a>
              {source_note}
              <button onclick="{copy_js}"
                      style="font-size:12px;padding:5px 11px;border:1px solid #ddd;
                             border-radius:4px;background:#f5f5f5;cursor:pointer;
                             color:#555;white-space:nowrap;flex-shrink:0;">
                📋 Copy
              </button>
            </div>""",
            unsafe_allow_html=True,
        )


# ── Load data (before sidebar so source list is available) ────────────────────
data             = load_data()
raw_scholarships = data.get("scholarships", []) if data else []
scraped_at       = data.get("scraped_at", "") if data else ""
available_sources = sorted({s.get("source","") for s in raw_scholarships
                             if s.get("source") and s.get("source") != "curated"})

# ── Session-state initialization + preset handling ────────────────────────────
if "f_initialized" not in st.session_state:
    for k, v in PRESET_DEFS["My Full Profile"].items():
        st.session_state[k] = v
    st.session_state["f_initialized"] = True
    st.session_state["f_last_preset"] = "My Full Profile"
    st.session_state["f_sources"]     = available_sources

# Sync source list when new data arrives
if available_sources and "f_sources" not in st.session_state:
    st.session_state["f_sources"] = available_sources

# Detect preset change and apply
_current_preset = st.session_state.get("f_preset", "My Full Profile")
if _current_preset != st.session_state.get("f_last_preset"):
    for k, v in PRESET_DEFS.get(_current_preset, {}).items():
        st.session_state[k] = v
    st.session_state["f_last_preset"] = _current_preset
    st.session_state["f_sources"]     = available_sources
    st.rerun()


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🎓 Scholarship Wizard")
    st.caption("Wake Forest MS AI Strategy")

    # ── PRESET ───────────────────────────────────────────────────────────────
    st.selectbox("⚡ Profile Preset", PRESET_NAMES, key="f_preset")
    st.divider()

    # ── SEARCH ───────────────────────────────────────────────────────────────
    st.text_input("🔍 Keyword search", placeholder="AI, fellowship, Jewish…", key="f_keyword")
    st.text_input("🚫 Exclude keywords", placeholder="coding, bootcamp…", key="f_exclude_kw")
    st.selectbox("↕ Sort by", SORT_OPTIONS, key="f_sort_by")
    st.divider()

    # ── MONEY ────────────────────────────────────────────────────────────────
    with st.expander("💰 Money Filters", expanded=True):
        st.slider("Award amount range", 0, 100_000,
                  key="f_amount_range", step=500, format="$%d")
        st.checkbox("Include 'Varies' / unknown amounts", key="f_include_unknown")
        st.selectbox("Funding type", FUNDING_TYPES, key="f_funding_type")

    # ── ELIGIBILITY ───────────────────────────────────────────────────────────
    with st.expander("✅ Eligibility Filters", expanded=False):
        st.caption("Check all identity groups you qualify for. Checked = show those scholarships + open ones.")
        c1, c2 = st.columns(2)
        with c1:
            st.checkbox("✡ Jewish",          key="f_id_jewish")
            st.checkbox("♀ Female",           key="f_id_female")
            st.checkbox("🎖 Veteran",          key="f_id_veteran")
            st.checkbox("🏳️‍🌈 LGBTQ+",         key="f_id_lgbtq")
            st.checkbox("🌎 Hispanic/Latino",  key="f_id_hispanic")
        with c2:
            st.checkbox("✊ Black/African Am.", key="f_id_black")
            st.checkbox("🪶 Native American",  key="f_id_native")
            st.checkbox("🌏 Asian American",   key="f_id_asian")
            st.checkbox("🎓 First-gen",        key="f_id_firstgen")
            st.checkbox("♿ Disability",        key="f_id_disability")

        st.caption("Location eligibility:")
        lc1, lc2 = st.columns(2)
        with lc1:
            st.checkbox("📍 Michigan",     key="f_loc_michigan")
            st.checkbox("📍 Ohio",         key="f_loc_ohio")
        with lc2:
            st.checkbox("🇺🇸 Any US state", key="f_loc_any_us")
            st.checkbox("🌐 International", key="f_loc_international")

        st.checkbox("No age restriction", key="f_age_no_restrict")
        if not st.session_state.get("f_age_no_restrict", True):
            st.slider("Age range", 18, 80, key="f_age_range")

        st.selectbox("Citizenship", CITIZEN_OPTIONS, key="f_citizenship")

    # ── ACADEMIC ──────────────────────────────────────────────────────────────
    with st.expander("🎓 Academic Filters", expanded=False):
        st.selectbox("Degree level", DEGREE_OPTIONS, key="f_degree_level")
        st.selectbox("Field focus", FIELD_OPTIONS, key="f_field_focus")
        st.selectbox("Enrollment status", ENROLL_OPTIONS, key="f_enrollment")
        st.selectbox("School specific", SCHOOL_OPTIONS, key="f_school")
        st.checkbox("GPA requirement not required", key="f_gpa_no_req")
        if not st.session_state.get("f_gpa_no_req", True):
            st.slider("Minimum GPA I have", 2.0, 4.0,
                      key="f_gpa_min", step=0.1, format="%.1f")

    # ── DEADLINE ──────────────────────────────────────────────────────────────
    with st.expander("📅 Deadline Filters", expanded=True):
        st.selectbox("Deadline within",  DEADLINE_OPT, key="f_deadline_days",
                     format_func=lambda x: "No limit" if x == 9999 else f"{x} days")
        st.checkbox("Show rolling deadlines", key="f_show_rolling")
        st.checkbox("Show expired deadlines", key="f_show_expired")

    # ── SOURCE ────────────────────────────────────────────────────────────────
    with st.expander("🔍 Source Filters", expanded=False):
        st.radio("Show", ["Both", "Curated only", "Scraped only"], key="f_source_mode")
        if available_sources and st.session_state.get("f_source_mode") != "Curated only":
            st.multiselect("Scraped sources", available_sources,
                           default=st.session_state.get("f_sources", available_sources),
                           key="f_sources")

    # ── PROFILE (scoring) ────────────────────────────────────────────────────
    with st.expander("👤 My Profile (Scoring)", expanded=False):
        st.caption("Checked attributes raise match scores. Unchecked = no scoring boost.")
        st.checkbox("✡ Jewish identity",      key="f_pf_jewish")
        st.checkbox("📍 Michigan resident",   key="f_pf_michigan")
        st.checkbox("🔴 Ohio State alum",     key="f_pf_osu")
        st.checkbox("👤 Age 57+",             key="f_pf_age57")
        st.checkbox("📚 Adult learner",       key="f_pf_adult")
        st.checkbox("🎓 Masters student",     key="f_pf_masters")
        st.checkbox("🏫 Wake Forest enrolled",key="f_pf_wfu")

    st.divider()
    run_clicked = st.button("🔄 Run Scrape Now", type="primary", use_container_width=True)
    st.caption("Scrapes all sources and updates the dashboard. No email is sent.")


# ── Handle scrape run ─────────────────────────────────────────────────────────
if run_clicked:
    with st.spinner("Scraping scholarships… ~2 minutes."):
        try:
            result = subprocess.run(
                [sys.executable, "scraper.py", "--scrape-only"],
                capture_output=True, text=True, timeout=360,
            )
        except subprocess.TimeoutExpired:
            st.error("Scrape timed out after 6 minutes.")
            st.stop()

    if result.returncode == 0:
        # Parse total found from scraper output
        m = re.search(
            r"Scoring complete.*?HIGH:\s*(\d+).*?MEDIUM:\s*(\d+).*?LOW:\s*(\d+)",
            result.stdout,
        )
        total = sum(int(m.group(i)) for i in (1, 2, 3)) if m else None
        msg = f"✅ Found {total} scholarships! Refreshing…" if total else "✅ Scrape complete — refreshing…"
        st.success(msg)
        load_data.clear()   # bust the 5-minute cache so new data loads immediately
        st.rerun()
    else:
        st.error("Scrape failed.")
        with st.expander("Error details"):
            st.code(result.stderr[-2000:] or result.stdout[-2000:])


# ── Empty state ───────────────────────────────────────────────────────────────
if data is None:
    st.title("🎓 Scholarship Dashboard")
    st.info(
        "**No scholarship data yet.**\n\n"
        "Click **Run Scrape Now** in the sidebar, or wait for the Monday 8 am UTC cron job.",
        icon="ℹ️",
    )
    st.stop()


# ── Read all active filter values from session state ─────────────────────────
pf = {k: st.session_state.get(k, v)
      for k, v in PRESET_DEFS["My Full Profile"].items()
      if k.startswith("f_pf_")}

amount_lo, amount_hi = st.session_state.get("f_amount_range", (0, 100_000))
include_unknown  = st.session_state.get("f_include_unknown", True)
funding_type     = st.session_state.get("f_funding_type", "All")
deadline_days    = st.session_state.get("f_deadline_days", 365)
show_rolling     = st.session_state.get("f_show_rolling", True)
show_expired     = st.session_state.get("f_show_expired", False)
field_focus      = st.session_state.get("f_field_focus", "All")
degree_level     = st.session_state.get("f_degree_level", "All")
citizenship_f    = st.session_state.get("f_citizenship", "All")
enrollment_f     = st.session_state.get("f_enrollment", "All")
school_f         = st.session_state.get("f_school", "All")
gpa_no_req       = st.session_state.get("f_gpa_no_req", True)
gpa_min_f        = st.session_state.get("f_gpa_min", 2.0)
age_no_restrict  = st.session_state.get("f_age_no_restrict", True)
age_lo, age_hi   = st.session_state.get("f_age_range", (18, 80))
source_mode      = st.session_state.get("f_source_mode", "Both")
selected_sources = st.session_state.get("f_sources", available_sources)
keyword          = st.session_state.get("f_keyword", "")
exclude_kw       = st.session_state.get("f_exclude_kw", "")
sort_by          = st.session_state.get("f_sort_by", "Best match")

# Identity eligibility flags → which criteria strings to match
IDENTITY_MAP = {
    "f_id_jewish":   ["jewish"],
    "f_id_female":   ["women", "female", "woman"],
    "f_id_veteran":  ["veteran", "military"],
    "f_id_lgbtq":    ["lgbtq", "lgbt"],
    "f_id_hispanic": ["hispanic", "latinx", "latino"],
    "f_id_black":    ["black", "african american"],
    "f_id_native":   ["native american", "indigenous"],
    "f_id_asian":    ["asian american"],
    "f_id_firstgen": ["first-generation", "first gen", "first-gen"],
    "f_id_disability":["disability", "disabled"],
}
active_identities = [
    kw for flag, kws in IDENTITY_MAP.items()
    if st.session_state.get(flag, False)
    for kw in kws
]

loc_michigan    = st.session_state.get("f_loc_michigan", False)
loc_ohio        = st.session_state.get("f_loc_ohio", False)
loc_any_us      = st.session_state.get("f_loc_any_us", True)
loc_intl        = st.session_state.get("f_loc_international", False)
any_loc_checked = any([loc_michigan, loc_ohio, loc_any_us, loc_intl])
cutoff_date     = date.today() + timedelta(days=min(deadline_days, 3650))
today           = date.today()


# ── Filter + re-score ─────────────────────────────────────────────────────────
filtered = []
for s in raw_scholarships:
    cats     = s.get("cats", {})
    text_low = (s.get("title","") + " " + s.get("raw_text","")).lower()
    amount_n = s.get("amount_numeric") or parse_amount_numeric(s.get("amount",""))
    dl       = parse_deadline_date(s.get("deadline_parsed") or s.get("deadline",""))
    rolling  = is_rolling(s)

    # ── Source filter ──────────────────────────────────────────────────────
    is_curated = s.get("source") == "curated"
    if source_mode == "Curated only" and not is_curated:
        continue
    if source_mode == "Scraped only" and is_curated:
        continue
    if not is_curated and available_sources:
        if s.get("source","") not in selected_sources:
            continue

    # ── Amount filter ──────────────────────────────────────────────────────
    if amount_n == 0:
        if not include_unknown:
            continue
    else:
        if amount_n < amount_lo or amount_n > amount_hi:
            continue

    # ── Funding type ───────────────────────────────────────────────────────
    if funding_type != "All":
        ft = funding_type.lower()
        match = False
        if ft == "full tuition" and "tuition" in text_low:
            match = True
        elif ft == "partial" and any(w in text_low for w in ["partial","up to"]):
            match = True
        elif ft == "stipend" and "stipend" in text_low:
            match = True
        elif ft == "one-time" and any(w in text_low for w in ["one-time","one time"]):
            match = True
        elif ft == "renewable" and any(w in text_low for w in ["renewable","per year","annually","each year"]):
            match = True
        if not match:
            continue

    # ── Deadline filter ────────────────────────────────────────────────────
    if rolling:
        if not show_rolling:
            continue
    elif dl is None:
        pass  # unknown deadline always passes
    else:
        expired = dl < today
        if expired and not show_expired:
            continue
        if not expired and deadline_days != 9999 and dl > cutoff_date:
            continue

    # ── Identity eligibility ───────────────────────────────────────────────
    if active_identities:
        identity_str = cats.get("identity_criteria","open").lower()
        if identity_str != "open":
            if not any(kw in identity_str for kw in active_identities):
                continue

    # ── Location filter ────────────────────────────────────────────────────
    if any_loc_checked:
        state_req = cats.get("state_required","any").lower()
        citizen   = cats.get("citizenship","").lower()
        passes = False
        if loc_michigan and ("michigan" in state_req or state_req == "any"):
            passes = True
        if loc_ohio and ("ohio" in state_req or state_req == "any"):
            passes = True
        if loc_any_us and state_req == "any":
            passes = True
        if loc_intl and "international" in citizen:
            passes = True
        if not passes:
            continue

    # ── Citizenship filter ─────────────────────────────────────────────────
    if citizenship_f != "All":
        cit = cats.get("citizenship","").lower()
        if citizenship_f == "US Citizen only" and "us citizen" not in cit and cit:
            continue
        if citizenship_f == "Permanent Resident OK" and "permanent" not in cit and cit:
            continue
        if citizenship_f == "International OK" and "international" not in cit and cit:
            continue

    # ── Degree level ───────────────────────────────────────────────────────
    if degree_level != "All":
        dl_cat = cats.get("degree_level","any")
        if degree_level == "Masters" and dl_cat not in ("graduate","any"):
            continue
        if degree_level == "PhD" and dl_cat not in ("PhD only","graduate (masters + PhD)","any"):
            continue
        if degree_level == "Masters + PhD" and dl_cat not in ("graduate","PhD only","graduate (masters + PhD)","any"):
            continue

    # ── Field focus ────────────────────────────────────────────────────────
    if field_focus != "All":
        field_cat = cats.get("field","").lower()
        ff = field_focus.lower()
        if "ai" in ff or "machine learning" in ff:
            if not any(w in field_cat for w in ("ai","machine learning","data")):
                continue
        elif "business" in ff or "strategy" in ff:
            if not any(w in field_cat for w in ("business","strategy")):
                continue
        elif "policy" in ff or "government" in ff:
            if not any(w in text_low for w in ("policy","government","federal","public sector")):
                continue
        elif "data science" in ff:
            if "data" not in field_cat and "data science" not in text_low:
                continue
        elif "cybersecurity" in ff:
            if not any(w in text_low for w in ("cybersecurity","cyber security","security")):
                continue
        elif "robotics" in ff:
            if "robot" not in text_low:
                continue
        elif "ethics" in ff:
            if not any(w in text_low for w in ("ethics","society","responsible","governance")):
                continue

    # ── Enrollment status ──────────────────────────────────────────────────
    if enrollment_f != "All":
        ef = enrollment_f.lower()
        if ef == "full-time" and "part-time" in text_low and "full-time" not in text_low:
            continue
        if ef == "part-time" and "full-time" in text_low and "part-time" not in text_low:
            continue

    # ── School specific ────────────────────────────────────────────────────
    if school_f == "Wake Forest":
        if not any(w in text_low for w in ("wake forest","wfu")):
            continue

    # ── GPA filter ─────────────────────────────────────────────────────────
    if not gpa_no_req:
        gpa_match = re.search(r'(\d\.\d)\s*gpa', text_low)
        if gpa_match:
            required_gpa = float(gpa_match.group(1))
            if required_gpa > gpa_min_f:
                continue

    # ── Age filter ─────────────────────────────────────────────────────────
    if not age_no_restrict:
        age_text = re.search(r'(\d{2})[–\-](\d{2})\s*years?\s*old', text_low)
        if age_text:
            sch_lo, sch_hi = int(age_text.group(1)), int(age_text.group(2))
            if sch_hi < age_lo or sch_lo > age_hi:
                continue

    # ── Keyword search ─────────────────────────────────────────────────────
    haystack = " ".join([s.get("title",""), s.get("provider",""),
                         " ".join(s.get("score_reasons",[])), s.get("raw_text","")]).lower()
    if keyword and keyword.lower() not in haystack:
        continue
    if exclude_kw:
        if any(w.strip() in haystack for w in exclude_kw.split(",") if w.strip()):
            continue

    # ── Re-score with live profile flags ───────────────────────────────────
    score, reasons = compute_score(s, pf)
    filtered.append({**s, "score": score, "score_reasons": reasons,
                     "_dl": dl, "_rolling": rolling, "_amount_n": amount_n})


# ── Sort ──────────────────────────────────────────────────────────────────────
if sort_by == "Amount (high → low)":
    filtered.sort(key=lambda x: x.get("_amount_n", 0), reverse=True)
elif sort_by == "Deadline (soonest)":
    def _dl_key(x):
        d = x.get("_dl")
        return d if d else date(9999, 12, 31)
    filtered.sort(key=_dl_key)
elif sort_by == "Newest first":
    curated = [s for s in filtered if s.get("source") == "curated"]
    scraped = [s for s in filtered if s.get("source") != "curated"]
    filtered = scraped + curated
else:
    filtered.sort(key=lambda x: x.get("score", 0), reverse=True)

high   = [s for s in filtered if s.get("score", 0) >= 8]
medium = [s for s in filtered if 5 <= s.get("score", 0) <= 7]
low    = [s for s in filtered if s.get("score", 0) < 5]

est_funding  = sum(s.get("_amount_n", 0) for s in filtered if s.get("_amount_n", 0) > 0)
expiring_soon = sum(
    1 for s in filtered
    if s.get("_dl") and not s.get("_rolling") and
    today <= s["_dl"] <= today + timedelta(days=30)
)


# ── Page header ───────────────────────────────────────────────────────────────
st.title("🎓 Scholarship Dashboard")
if scraped_at:
    try:
        st.caption(f"Last scraped: {datetime.fromisoformat(scraped_at).strftime('%B %d, %Y at %I:%M %p')}")
    except Exception:
        st.caption(f"Last scraped: {scraped_at}")

# ── Results summary bar ───────────────────────────────────────────────────────
r1, r2, r3, r4 = st.columns(4)
r1.metric("Scholarships shown",    len(filtered),
          help="After all active sidebar filters.")
r2.metric("High matches (8–10)",   len(high))
r3.metric("Est. funding available",
          f"${est_funding:,}" if est_funding else "—",
          help="Sum of all known award amounts in current results.")
r4.metric("Expiring ≤ 30 days",    expiring_soon,
          delta=f"{expiring_soon} urgent" if expiring_soon else None,
          delta_color="inverse")

st.divider()

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs([
    f"🏆 HIGH MATCH  ({len(high)})",
    f"📊 MEDIUM MATCH  ({len(medium)})",
    f"📋 All Results  ({len(filtered)})",
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
        st.info("No results. Try loosening the sidebar filters or selecting a preset.")
