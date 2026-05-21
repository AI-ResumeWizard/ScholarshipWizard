"""
Scholarship Scraper — 3-phase wide-net approach.

Phase 1: Scrape ALL AI/ML/tech-strategy scholarships (no profile filter at scrape time)
Phase 2: Categorize each result across 8 dimensions
Phase 3: Score 1–10 against owner profile; email sorted into HIGH / MEDIUM / LOW tiers
"""

import hashlib
import json
import os
import re
import smtplib
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

# ── Owner profile (scoring weights — never change without asking) ──────────────
PROFILE = {
    "degree": "masters",
    "age": 57,
    "jewish": True,
    "state": "michigan",
    "undergrad_school": "ohio state",
    "grad_school": "wake forest",
    "program": "ms ai strategy",
    "field": "business/strategy",   # not pure coding
    "us_citizen": True,
    "gender": "male",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}
SEEN_FILE = "seen_scholarships.json"

# ── Phase 1 sources ───────────────────────────────────────────────────────────
# type "list"    = structured card/item list pages (scholarships.com etc.)
# type "article" = blog/article that lists scholarships as headings
# type "direct"  = single-program pages (Google, NSF, etc.)
SOURCES = [
    {
        "name": "Scholarships.com – AI/Tech",
        "type": "list",
        "url": "https://www.scholarships.com/financial-aid/college-scholarships/scholarships-by-type/ai-artificial-intelligence-scholarships/",
        "item_selector": ".scholarship-item, article, .result-item, .listing, .card",
        "title_selector": "h2, h3, .scholarship-name, .title, a",
        "amount_selector": ".amount, .award, .scholarship-amount",
        "deadline_selector": ".deadline, .date, .scholarship-deadline",
    },
    {
        "name": "Scholarships.com – Graduate",
        "type": "list",
        "url": "https://www.scholarships.com/financial-aid/college-scholarships/scholarships-by-degree/graduate-scholarships/",
        "item_selector": ".scholarship-item, article, .result-item, .listing, .card",
        "title_selector": "h2, h3, .scholarship-name, .title, a",
        "amount_selector": ".amount, .award",
        "deadline_selector": ".deadline, .date",
    },
    {
        "name": "Scholarships.com – Technology",
        "type": "list",
        "url": "https://www.scholarships.com/financial-aid/college-scholarships/scholarships-by-major/technology-scholarships/",
        "item_selector": ".scholarship-item, article, .result-item, .listing, .card",
        "title_selector": "h2, h3, .scholarship-name, .title, a",
        "amount_selector": ".amount, .award",
        "deadline_selector": ".deadline, .date",
    },
    {
        "name": "ScholarshipsandGrants – AI/ML",
        "type": "list",
        "url": "https://scholarshipsandgrants.us/major/ai-ml/",
        "item_selector": "article, .scholarship, .entry",
        "title_selector": "h2, h3, .entry-title",
        "amount_selector": ".amount",
        "deadline_selector": ".deadline",
    },
    {
        "name": "ScholarshipsandGrants – Technology",
        "type": "list",
        "url": "https://scholarshipsandgrants.us/major/technology/",
        "item_selector": "article, .scholarship, .entry",
        "title_selector": "h2, h3, .entry-title",
        "amount_selector": ".amount",
        "deadline_selector": ".deadline",
    },
    {
        "name": "Fastweb – Graduate Scholarships",
        "type": "article",
        "url": "https://www.fastweb.com/college-scholarships/articles/scholarships-for-graduate-students",
        "item_selector": "h2, h3",
        "title_selector": None,
        "amount_selector": None,
        "deadline_selector": None,
    },
    {
        "name": "Fastweb – AI Scholarships",
        "type": "article",
        "url": "https://www.fastweb.com/college-scholarships/articles/artificial-intelligence-scholarships",
        "item_selector": "h2, h3",
        "title_selector": None,
        "amount_selector": None,
        "deadline_selector": None,
    },
    {
        "name": "Abroadin – AI Scholarships",
        "type": "article",
        "url": "https://abroadin.com/blog/scholarships-for-ai/",
        "item_selector": "h2, h3",
        "title_selector": None,
        "amount_selector": None,
        "deadline_selector": None,
    },
    {
        "name": "ScholarshipBob – AI",
        "type": "article",
        "url": "https://scholarshipbob.com/ai-scholarships/",
        "item_selector": "h2, h3, h4",
        "title_selector": None,
        "amount_selector": None,
        "deadline_selector": None,
    },
    {
        "name": "Microsoft Scholarships",
        "type": "direct",
        "url": "https://careers.microsoft.com/students/us/en/scholarship",
        "item_selector": ".card, article, section, h2, h3",
        "title_selector": "h2, h3, .title",
        "amount_selector": None,
        "deadline_selector": None,
    },
    {
        "name": "Google – Build Your Future Scholarships",
        "type": "direct",
        "url": "https://buildyourfuture.withgoogle.com/scholarships",
        "item_selector": "article, .card, section, h2, h3",
        "title_selector": "h2, h3, .title",
        "amount_selector": None,
        "deadline_selector": None,
    },
    {
        "name": "Amazon Future Engineer",
        "type": "direct",
        "url": "https://www.amazonfutureengineer.com/scholarships",
        "item_selector": "article, .card, section, h2, h3",
        "title_selector": "h2, h3",
        "amount_selector": None,
        "deadline_selector": None,
    },
    {
        "name": "IBM University Programs",
        "type": "article",
        "url": "https://research.ibm.com/university/awards/fellowships.html",
        "item_selector": "h2, h3, h4",
        "title_selector": None,
        "amount_selector": None,
        "deadline_selector": None,
    },
    {
        "name": "NSF Graduate Fellowships",
        "type": "direct",
        "url": "https://www.nsfgrfp.org/",
        "item_selector": "h1, h2, h3, .content",
        "title_selector": "h1, h2",
        "amount_selector": None,
        "deadline_selector": None,
    },
    {
        "name": "DOE University Student Programs",
        "type": "article",
        "url": "https://science.osti.gov/University-and-Grants-Office/Students-and-Early-Career-Scientists",
        "item_selector": "h2, h3, li a",
        "title_selector": None,
        "amount_selector": None,
        "deadline_selector": None,
    },
    {
        "name": "Hillel – Jewish Scholarships",
        "type": "article",
        "url": "https://www.hillel.org/jewish/jewish-scholarships/",
        "item_selector": "h2, h3, h4",
        "title_selector": None,
        "amount_selector": None,
        "deadline_selector": None,
    },
    {
        "name": "AARP Foundation Scholarships",
        "type": "direct",
        "url": "https://www.aarp.org/aarp-foundation/our-work/income/scholarships/",
        "item_selector": "article, .card, h2, h3",
        "title_selector": "h2, h3",
        "amount_selector": None,
        "deadline_selector": None,
    },
]

# ── Curated high-fit list (always included, pre-scored) ───────────────────────
CURATED = [
    {
        "title": "JVS Michigan Scholarship Fund",
        "provider": "Jewish Vocational Service Detroit",
        "amount": "Up to $5,000",
        "deadline": "Annual – check jvsdetroit.org",
        "url": "https://www.jvsdetroit.org/scholarship/",
        "score": 9,
        "score_reasons": ["Jewish identity — direct match", "Michigan resident", "graduate student"],
    },
    {
        "title": "Jewish Federation of Metropolitan Detroit – Education Grants",
        "provider": "Jewish Federation of Metropolitan Detroit",
        "amount": "Varies",
        "deadline": "Rolling – contact jewishdetroit.org",
        "url": "https://www.jewishdetroit.org/",
        "score": 8,
        "score_reasons": ["Jewish identity — direct match", "Michigan resident"],
    },
    {
        "title": "B'nai B'rith International Scholarship",
        "provider": "B'nai B'rith International",
        "amount": "$1,000–$5,000",
        "deadline": "March 31 annually",
        "url": "https://www.bnaibrith.org/scholarship.html",
        "score": 8,
        "score_reasons": ["Jewish identity — direct match", "graduate student"],
    },
    {
        "title": "American Jewish Committee (AJC) Fellowship",
        "provider": "American Jewish Committee",
        "amount": "Varies",
        "deadline": "Annual – check ajc.org",
        "url": "https://www.ajc.org/",
        "score": 7,
        "score_reasons": ["Jewish identity", "policy/strategy focus — profile match"],
    },
    {
        "title": "AARP Foundation – Back to School Scholarship",
        "provider": "AARP Foundation",
        "amount": "Up to $2,500",
        "deadline": "Annual – check aarp.org",
        "url": "https://www.aarp.org/aarp-foundation/our-work/income/scholarships/",
        "score": 9,
        "score_reasons": ["Adult learner / 50+ — direct match", "graduate school"],
    },
    {
        "title": "Schmidt Futures AI2050 Fellowship",
        "provider": "Schmidt Futures",
        "amount": "Up to $100,000",
        "deadline": "Annual – check ai2050.schmidtfutures.com",
        "url": "https://ai2050.schmidtfutures.com/",
        "score": 8,
        "score_reasons": ["AI strategy focus — strongest field match", "research-oriented"],
    },
    {
        "title": "Wake Forest Graduate School Merit Fellowships",
        "provider": "Wake Forest University",
        "amount": "Varies",
        "deadline": "Contact graduate.wfu.edu",
        "url": "https://graduate.wfu.edu/financial-support/",
        "score": 8,
        "score_reasons": ["Currently enrolled at Wake Forest", "ask program director directly"],
    },
    {
        "title": "Wake Forest School of Business – Graduate Assistantship",
        "provider": "Wake Forest University",
        "amount": "Tuition + stipend",
        "deadline": "Rolling",
        "url": "https://business.wfu.edu/",
        "score": 7,
        "score_reasons": ["Enrolled MS student", "departmental assistantships often unadvertised"],
    },
    {
        "title": "NSF Graduate Research Fellowship Program (GRFP)",
        "provider": "National Science Foundation",
        "amount": "$37,000/year + tuition",
        "deadline": "October annually",
        "url": "https://www.nsfgrfp.org/",
        "score": 6,
        "score_reasons": ["Graduate AI/STEM", "highly competitive — strong application story possible"],
    },
    {
        "title": "DOE Office of Science Graduate Student Research (SCGSR)",
        "provider": "U.S. Department of Energy",
        "amount": "Stipend + travel",
        "deadline": "Annual cycles – check energy.gov",
        "url": "https://science.osti.gov/University-and-Grants-Office/Students-and-Early-Career-Scientists/Graduate-Student-Research-Program",
        "score": 5,
        "score_reasons": ["Graduate AI/STEM", "US federal fellowship"],
    },
    {
        "title": "Ohio State Alumni Association Scholarships",
        "provider": "Ohio State University Alumni Association",
        "amount": "Varies",
        "deadline": "Check ohiostatealumni.org",
        "url": "https://www.ohiostatealumni.org/",
        "score": 6,
        "score_reasons": ["Ohio State alumnus — direct match"],
    },
    {
        "title": "Michigan Council of Women in Technology Foundation Scholarship",
        "provider": "MCWT Foundation",
        "amount": "$3,000–$5,000",
        "deadline": "February annually",
        "url": "https://www.mcwt.org/scholarships",
        "score": 4,
        "score_reasons": ["Michigan resident", "technology field — note: check gender eligibility"],
    },
]


# ── Utilities ─────────────────────────────────────────────────────────────────
def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f, indent=2)


def uid(title):
    return hashlib.md5(title.lower().strip().encode()).hexdigest()[:12]


def dollar_in(text):
    m = re.search(
        r'\$[\d,]+(?:,\d{3})*(?:\s*(?:k|K|thousand))?(?:\s*/\s*year)?',
        text
    )
    return m.group(0) if m else None


def date_in(text):
    m = re.search(
        r'(?:January|February|March|April|May|June|July|August|'
        r'September|October|November|December)\s+\d{1,2}(?:,\s*\d{4})?'
        r'|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b',
        text, re.IGNORECASE
    )
    return m.group(0) if m else None


def resolve_link(href, base_url):
    if not href:
        return base_url
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        p = urlparse(base_url)
        return f"{p.scheme}://{p.netloc}{href}"
    return base_url


# ── Phase 1: Scraping ─────────────────────────────────────────────────────────
def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return BeautifulSoup(r.text, "lxml")


def scrape_list(source):
    results = []
    soup = fetch(source["url"])
    items = soup.select(source["item_selector"])
    for item in items[:60]:
        title_el = item.select_one(source["title_selector"]) if source.get("title_selector") else item
        title = (title_el or item).get_text(strip=True)
        if not title or len(title) < 8:
            continue
        raw = item.get_text(" ", strip=True)[:500]
        link_el = item.find("a", href=True)
        amount_el = item.select_one(source["amount_selector"]) if source.get("amount_selector") else None
        deadline_el = item.select_one(source["deadline_selector"]) if source.get("deadline_selector") else None
        results.append({
            "title": title[:120],
            "provider": source["name"],
            "amount": (amount_el.get_text(strip=True) if amount_el else None) or dollar_in(raw) or "Varies",
            "deadline": (deadline_el.get_text(strip=True) if deadline_el else None) or date_in(raw) or "Check website",
            "url": resolve_link(link_el["href"] if link_el else None, source["url"]),
            "raw_text": raw,
            "source": source["name"],
        })
    return results


def scrape_article(source):
    results = []
    soup = fetch(source["url"])
    headings = soup.select(source["item_selector"])
    for h in headings:
        title = h.get_text(strip=True)
        if not title or len(title) < 10 or len(title) > 150:
            continue
        context_parts = []
        for sib in h.find_next_siblings()[:5]:
            if sib.name in ("h2", "h3", "h4"):
                break
            context_parts.append(sib.get_text(" ", strip=True))
        context = " ".join(context_parts)[:400]
        combined = title + " " + context
        link_el = h.find("a", href=True) or h.find_next("a", href=True)
        results.append({
            "title": title[:120],
            "provider": source["name"],
            "amount": dollar_in(combined) or "See listing",
            "deadline": date_in(combined) or "Check website",
            "url": resolve_link(link_el["href"] if link_el else None, source["url"]),
            "raw_text": combined[:500],
            "source": source["name"],
        })
    return results


def scrape_direct(source):
    results = []
    soup = fetch(source["url"])
    items = soup.select(source["item_selector"])
    cards = [i for i in items if i.name not in ("h1", "h2", "h3", "h4")]
    headings = [i for i in items if i.name in ("h1", "h2", "h3", "h4")]
    targets = cards[:20] if cards else headings[:10]
    for item in targets:
        title = item.get_text(strip=True)
        if not title or len(title) < 8:
            continue
        raw = item.get_text(" ", strip=True)
        for sib in item.find_next_siblings()[:3]:
            raw += " " + sib.get_text(" ", strip=True)
        raw = raw[:500]
        link_el = item.find("a", href=True) or item.find_next("a", href=True)
        results.append({
            "title": title[:120],
            "provider": source["name"],
            "amount": dollar_in(raw) or "See page",
            "deadline": date_in(raw) or "Check website",
            "url": resolve_link(link_el["href"] if link_el else None, source["url"]),
            "raw_text": raw,
            "source": source["name"],
        })
    return results


def scrape_source(source):
    fn = {"list": scrape_list, "article": scrape_article, "direct": scrape_direct}.get(
        source.get("type", "list"), scrape_list
    )
    try:
        results = fn(source)
        print(f"    → {len(results)} items")
        return results
    except Exception as e:
        print(f"    [warn] {e}")
        return []


# ── Phase 2: Categorization ───────────────────────────────────────────────────
def categorize(s):
    text = (
        s.get("title", "") + " "
        + s.get("raw_text", "") + " "
        + s.get("why", "") + " "
        + " ".join(s.get("score_reasons", []))
    ).lower()

    # Degree level
    has_grad = any(w in text for w in ["graduate", "grad student", "master", "ms ", "m.s.", "postgrad"])
    has_phd = any(w in text for w in ["phd", "doctoral", "doctorate", "ph.d."])
    has_undergrad = any(w in text for w in ["undergraduate", "undergrad", "bachelor", "high school", "freshman", "sophomore"])
    if has_phd and not has_grad:
        degree = "PhD only"
    elif has_undergrad and not has_grad:
        degree = "undergrad only"
    elif has_grad:
        degree = "graduate"
    else:
        degree = "any"

    # Citizenship
    if re.search(r'\bu\.?s\.?\s*citi', text):
        citizenship = "US citizen required"
    elif "permanent resident" in text or "green card" in text:
        citizenship = "US citizen or PR"
    elif "international" in text:
        citizenship = "open/international"
    else:
        citizenship = "not specified"

    # Age restriction
    if any(w in text for w in ["50+", "55+", "60+", "over 50", "adult learner", "non-traditional", "returning adult", "returning student", "mature student", "career change"]):
        age = "50+ / adult learner"
    elif re.search(r'under\s+(?:25|30|35)\b|(?:18|21)[–\-](?:25|30)\b', text):
        age = "under 25–30"
    else:
        age = "open"

    # State requirement
    states = re.findall(r'\b(michigan|ohio|north carolina|california|new york|florida|texas|illinois|pennsylvania)\b', text)
    state = ", ".join(sorted(set(states))).title() if states else "any"

    # Identity criteria
    criteria = []
    if any(w in text for w in ["jewish", "hillel", "jvs ", "b'nai", "bnai", "jewish federation", "jewish vocational"]):
        criteria.append("Jewish")
    if any(w in text for w in ["veteran", "military service", "armed forces"]):
        criteria.append("veteran")
    if re.search(r'\bwomen in (?:tech|stem|ai|cs)\b|\bfor women\b|\bwomen only\b|\bfemale students\b', text):
        criteria.append("women in STEM")
    if any(w in text for w in ["underrepresented", "minority", "bipoc", "hispanic", "latinx", "black students", "african american"]):
        criteria.append("diversity")
    if any(w in text for w in ["first-generation", "first gen ", "first-gen"]):
        criteria.append("first-gen")
    identity = ", ".join(criteria) if criteria else "open"

    # Field specificity
    has_ai = any(w in text for w in ["artificial intelligence", "machine learning", " ai ", "ai/ml", "data science", "deep learning", "neural network", "nlp", "large language"])
    has_strat = any(w in text for w in ["strategy", "business", "management", "policy", "leadership", "technology strategy", "tech strategy", "mba"])
    has_cs = any(w in text for w in ["computer science", "software engineering", "programming", "software development"])
    has_stem = any(w in text for w in ["stem", "engineering", "technology", "science"])

    if has_ai and has_strat:
        field = "AI + business/strategy"
    elif has_ai:
        field = "AI/ML"
    elif has_strat:
        field = "business/strategy"
    elif has_cs:
        field = "CS/engineering"
    elif has_stem:
        field = "STEM"
    else:
        field = "general"

    return {
        "degree_level": degree,
        "citizenship": citizenship,
        "age_restriction": age,
        "state_required": state,
        "identity_criteria": identity,
        "field": field,
    }


# ── Phase 3: Scoring ──────────────────────────────────────────────────────────
def score_scholarship(s, cats):
    """Return (score: int 1-10, reasons: list[str])."""
    text = (s.get("title", "") + " " + s.get("raw_text", "")).lower()
    pts = 1
    reasons = []

    # Hard disqualifiers
    if cats["degree_level"] == "undergrad only":
        return 1, ["undergrad only — profile is masters"]
    if cats["degree_level"] == "PhD only":
        return 2, ["PhD only — profile is masters"]
    if re.search(r'\bfor women only\b|\bwomen[- ]only\b|\bfemale only\b', text):
        return 1, ["women-only — not applicable"]
    if re.search(r'\bunder\s+25\b|\bunder\s+30\b|\bage\s+limit.*2[0-9]\b', text):
        return 1, ["age cap too young"]

    # Degree level (+2 graduate, +1 any)
    if cats["degree_level"] == "graduate":
        pts += 2
        reasons.append("graduate level — direct match")
    elif cats["degree_level"] == "any":
        pts += 1
        reasons.append("open to graduate students")

    # Field match (up to +3)
    if cats["field"] == "AI + business/strategy":
        pts += 3
        reasons.append("AI + business/strategy — strongest field match")
    elif cats["field"] == "AI/ML":
        pts += 2
        reasons.append("AI/ML field — strong match")
    elif cats["field"] == "business/strategy":
        pts += 2
        reasons.append("business/strategy — profile interest match")
    elif cats["field"] == "CS/engineering":
        pts += 1
        reasons.append("CS/tech adjacent — partial match")
    elif cats["field"] == "STEM":
        pts += 1
        reasons.append("STEM — broad match")

    # Jewish identity (+3 — rare and high value)
    if "Jewish" in cats["identity_criteria"]:
        pts += 3
        reasons.append("Jewish identity — direct match")

    # Adult learner / 50+ (+2)
    if cats["age_restriction"] == "50+ / adult learner":
        pts += 2
        reasons.append("adult learner / 50+ — direct match")
    elif any(w in text for w in ["non-traditional", "returning student", "career change", "career changers", "re-entry"]):
        pts += 1
        reasons.append("non-traditional / career change — matches profile")

    # Michigan (+1)
    if "michigan" in text or "Michigan" in cats["state_required"]:
        pts += 1
        reasons.append("Michigan residency match")

    # Ohio State (+1)
    if any(w in text for w in ["ohio state", "osu alum", "buckeye"]):
        pts += 1
        reasons.append("Ohio State alumnus match")

    # Wake Forest (+1)
    if any(w in text for w in ["wake forest", "wfu", "winston-salem"]):
        pts += 1
        reasons.append("Wake Forest — currently enrolled")

    return max(1, min(10, pts)), reasons


# ── Email builder ─────────────────────────────────────────────────────────────
def score_badge(n):
    if n >= 8:
        bg, fg, label = "#e8f5e9", "#1b5e20", f"HIGH {n}/10"
    elif n >= 5:
        bg, fg, label = "#fff3e0", "#bf360c", f"MED {n}/10"
    else:
        bg, fg, label = "#f5f5f5", "#616161", f"{n}/10"
    return (
        f'<span style="background:{bg};color:{fg};padding:2px 8px;'
        f'border-radius:12px;font-size:11px;font-weight:700;">{label}</span>'
    )


def card_html(s):
    n = s.get("score", 0)
    cats = s.get("cats", {})
    reasons = s.get("score_reasons", [])

    tag = score_badge(n)
    if s.get("source") == "curated":
        tag += (' <span style="background:#e3f2fd;color:#1565c0;padding:2px 8px;'
                'border-radius:12px;font-size:11px;font-weight:600;">CURATED</span>')

    meta = " · ".join(filter(None, [
        cats.get("degree_level", "").title() if cats.get("degree_level") not in ("any", "") else "",
        cats.get("field", ""),
        cats.get("identity_criteria", "") if cats.get("identity_criteria") != "open" else "",
        cats.get("state_required", "") if cats.get("state_required") != "any" else "",
        cats.get("age_restriction", "") if cats.get("age_restriction") != "open" else "",
    ]))

    reasons_html = ""
    if reasons:
        items = "".join(f"<li>{r}</li>" for r in reasons)
        reasons_html = (
            f'<ul style="margin:6px 0 4px;padding-left:18px;font-size:12px;color:#555;">'
            f'{items}</ul>'
        )

    return f"""
    <div style="border:1px solid #e0e0e0;border-radius:8px;padding:16px;margin-bottom:10px;background:#fff;">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap;">
        <div style="flex:1;min-width:200px;">
          <div style="margin-bottom:4px;">{tag}</div>
          <strong style="font-size:15px;color:#1a1a1a;">{s["title"]}</strong><br>
          <span style="font-size:12px;color:#777;">{s.get("provider","")}</span>
          {f'<div style="font-size:11px;color:#999;margin-top:3px;">{meta}</div>' if meta else ""}
        </div>
        <div style="text-align:right;font-size:13px;font-weight:600;color:#222;min-width:90px;">
          {s.get("amount","Varies")}
          <div style="font-weight:normal;color:#888;font-size:12px;">{s.get("deadline","Check website")}</div>
        </div>
      </div>
      {reasons_html}
      <a href="{s["url"]}" style="font-size:13px;color:#1565c0;text-decoration:none;">Apply / Learn more →</a>
    </div>"""


def build_email(high, medium, low):
    date_str = datetime.now().strftime("%B %d, %Y")
    total = len(high) + len(medium) + len(low)

    high_html = (
        "".join(card_html(s) for s in high)
        or "<p style='color:#888;font-size:14px;'>No high-match scholarships found this week.</p>"
    )
    medium_html = (
        "".join(card_html(s) for s in medium)
        or "<p style='color:#888;font-size:14px;'>No medium-match scholarships found this week.</p>"
    )

    low_rows = "".join(
        f'<tr>'
        f'<td style="padding:6px 8px;font-size:13px;border-bottom:1px solid #f0f0f0;">'
        f'<a href="{s["url"]}" style="color:#1565c0;text-decoration:none;">{s["title"]}</a></td>'
        f'<td style="padding:6px 8px;font-size:12px;color:#888;white-space:nowrap;">{s.get("amount","—")}</td>'
        f'<td style="padding:6px 8px;white-space:nowrap;">{score_badge(s.get("score",1))}</td>'
        f'</tr>'
        for s in low
    )
    low_section = ""
    if low:
        low_section = f"""
        <details style="margin-top:24px;">
          <summary style="cursor:pointer;font-size:15px;font-weight:600;color:#757575;
                          list-style:none;padding:12px 0;border-top:1px solid #eee;">
            ▸ All Others / Low Match ({len(low)}) — click to expand
          </summary>
          <table style="width:100%;border-collapse:collapse;margin-top:10px;">
            <tr style="background:#f5f5f5;">
              <th style="padding:6px 8px;text-align:left;font-size:12px;color:#666;font-weight:600;">Scholarship</th>
              <th style="padding:6px 8px;text-align:left;font-size:12px;color:#666;font-weight:600;">Amount</th>
              <th style="padding:6px 8px;text-align:left;font-size:12px;color:#666;font-weight:600;">Score</th>
            </tr>
            {low_rows}
          </table>
        </details>"""

    return f"""<html><body style="font-family:system-ui,-apple-system,sans-serif;max-width:700px;margin:0 auto;padding:20px;background:#f5f5f5;">
  <div style="background:#1a237e;color:#fff;padding:22px 28px;border-radius:10px 10px 0 0;">
    <h1 style="margin:0;font-size:22px;font-weight:700;">Scholarship Digest</h1>
    <p style="margin:6px 0 0;opacity:0.8;font-size:14px;">{date_str} · Wake Forest MS AI Strategy · {total} total matches</p>
  </div>
  <div style="background:#fff;padding:24px 28px;border-radius:0 0 10px 10px;border:1px solid #e0e0e0;border-top:none;">

    <div style="background:#e8f5e9;border-left:4px solid #2e7d32;border-radius:4px;padding:12px 16px;margin-bottom:24px;font-size:14px;">
      <strong style="color:#1b5e20;">{len(high)} HIGH MATCH (8–10)</strong> &nbsp;·&nbsp;
      <span style="color:#bf360c;font-weight:600;">{len(medium)} MEDIUM MATCH (5–7)</span> &nbsp;·&nbsp;
      <span style="color:#757575;">{len(low)} low match (1–4)</span>
    </div>

    <h2 style="font-size:17px;color:#1b5e20;border-bottom:2px solid #e8f5e9;padding-bottom:8px;margin-bottom:16px;">
      HIGH MATCH — Score 8–10 ({len(high)})
    </h2>
    {high_html}

    <h2 style="font-size:17px;color:#bf360c;border-bottom:2px solid #fff3e0;padding-bottom:8px;margin-top:28px;margin-bottom:16px;">
      MEDIUM MATCH — Score 5–7 ({len(medium)})
    </h2>
    {medium_html}

    {low_section}

    <hr style="border:none;border-top:1px solid #eee;margin:28px 0 14px;">
    <p style="font-size:11px;color:#bbb;text-align:center;margin:0;line-height:1.8;">
      Profile: Wake Forest MS AI Strategy · Michigan · Jewish · OSU alum · 57+ · business/strategy focus<br>
      Scoring: edit <code>score_scholarship()</code> in scraper.py · Curated: edit <code>CURATED</code> list
    </p>
  </div>
</body></html>"""


# ── Email sender ──────────────────────────────────────────────────────────────
def send_email(html_body, high_count):
    sender = os.environ["EMAIL_FROM"]
    password = os.environ["EMAIL_PASSWORD"]
    recipient = os.environ["EMAIL_TO"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = (
        f"Scholarship Digest – {datetime.now().strftime('%b %d, %Y')} "
        f"({high_count} high match)"
    )
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, recipient, msg.as_string())
    print("Email sent.")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"Scholarship scraper — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    seen = load_seen()

    # Phase 1: Wide scraping (no profile filter)
    raw = []
    for source in SOURCES:
        print(f"  Scraping: {source['name']}...")
        raw.extend(scrape_source(source))
        time.sleep(2)
    print(f"\nPhase 1: {len(raw)} total items scraped")

    # Dedup scraped against seen and within this run
    seen_this_run: set = set()
    new_scraped = []
    for r in raw:
        key = uid(r["title"])
        if key not in seen and key not in seen_this_run:
            new_scraped.append(r)
            seen_this_run.add(key)
            seen.add(key)
    print(f"After dedup: {len(new_scraped)} new items")

    # Phase 2 + 3 on scraped items
    for r in new_scraped:
        r["cats"] = categorize(r)
        r["score"], r["score_reasons"] = score_scholarship(r, r["cats"])

    # Curated: always show every week (no dedup), but categorize for display
    for c in CURATED:
        c["source"] = "curated"
        if "raw_text" not in c:
            c["raw_text"] = " ".join(c.get("score_reasons", [])) + " " + c.get("why", "")
        c["cats"] = categorize(c)

    save_seen(seen)

    # Combine, sort by score descending
    combined = new_scraped + CURATED
    combined.sort(key=lambda x: x.get("score", 0), reverse=True)

    high = [s for s in combined if s.get("score", 0) >= 8]
    medium = [s for s in combined if 5 <= s.get("score", 0) <= 7]
    low = [s for s in combined if s.get("score", 0) < 5]

    print(f"\nScoring complete — HIGH: {len(high)}, MEDIUM: {len(medium)}, LOW: {len(low)}")

    html = build_email(high, medium, low)
    send_email(html, len(high))
    print("Done.")


if __name__ == "__main__":
    main()
