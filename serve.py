"""
serve.py — Single entrypoint for the scholarship app.

Routes:
  GET  /              → redirect to /widget
  GET  /widget        → serve widget.html (embeddable scholarship search)
  GET  /api/scholarships → multi-source scholarship proxy (server-side, no CORS)
  GET  /api/health    → live source health check
  ALL  /dashboard*    → HTTP reverse proxy to Streamlit (port 8501)
  WS   /dashboard/_stcore/stream → WebSocket proxy to Streamlit

Sources (parallel):
  - CareerOneStop Training API (/v1/training — requires env vars)
  - Grants.gov federal opportunities (no key needed)
  - Scholarships.com AI scholarships page (scraped)
  - ScholarshipsAndGrants.us AI/ML page (scraped)
  - GoingMerry graduate scholarships (scraped)
  - ScholarshipAPI.com (optional — requires SCHOLARSHIPAPI_KEY)

Streamlit is started as a background subprocess and polled until ready.
"""

import hashlib
import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait as _fut_wait
from urllib.parse import urlencode, quote as _url_quote

import requests as _req
import websocket as _ws
from bs4 import BeautifulSoup
from flask import Flask, Response, jsonify, redirect, request, send_file, stream_with_context
from flask_sock import Sock

# ── Start Streamlit on an internal port ───────────────────────────────────────
STREAMLIT_PORT = 8501
_STREAMLIT_BASE = f"http://localhost:{STREAMLIT_PORT}"
_STREAMLIT_WS   = f"ws://localhost:{STREAMLIT_PORT}"


def _run_streamlit():
    subprocess.run([
        "streamlit", "run", "dashboard.py",
        "--server.port",                  str(STREAMLIT_PORT),
        "--server.address",               "localhost",
        "--server.enableCORS",            "false",
        "--server.enableXsrfProtection",  "false",
        "--server.headless",              "true",
    ])


_st_thread = threading.Thread(target=_run_streamlit, daemon=True)
_st_thread.start()

# Poll until Streamlit's health endpoint responds (up to 60 s)
print(f"Waiting for Streamlit to start on port {STREAMLIT_PORT}…")
for _ in range(60):
    try:
        _req.get(f"{_STREAMLIT_BASE}/_stcore/health", timeout=1)
        print("Streamlit is ready.")
        break
    except Exception:
        time.sleep(1)
else:
    print("[warn] Streamlit did not respond in time — requests will retry automatically.")


# ── Flask app ─────────────────────────────────────────────────────────────────
app  = Flask(__name__)
sock = Sock(app)

# Headers we must not forward (hop-by-hop)
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
    "host", "content-length",
    # WebSocket negotiation headers (handled by flask-sock / websocket-client)
    "sec-websocket-key", "sec-websocket-version", "sec-websocket-extensions",
    "sec-websocket-protocol",
})


def _fwd(headers: dict) -> dict:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


# ── Multi-source constants ────────────────────────────────────────────────────
_SCRAPE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_SOURCE_CACHE: dict = {}   # { cache_key: (unix_ts, payload_dict) }
_CACHE_TTL    = 600        # 10 min — scholarships don't change minute-by-minute
_MULTI_TIMEOUT = 18        # seconds to wait for all parallel sources


def _cache_get(key: str):
    entry = _SOURCE_CACHE.get(key)
    return entry[1] if (entry and time.time() - entry[0] < _CACHE_TTL) else None


def _cache_set(key: str, value):
    _SOURCE_CACHE[key] = (time.time(), value)


def _uid(name: str) -> str:
    return hashlib.md5(name.lower().strip().encode()).hexdigest()[:12]


def _dedup(results: list) -> list:
    """Remove duplicate scholarships (same name, case-insensitive)."""
    seen, out = set(), []
    for s in results:
        name = (s.get("ScholarshipName") or "").strip()
        key  = _uid(name) if len(name) >= 6 else None
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(s)
    return out


# ── Source fetchers (each returns a list of normalized dicts) ─────────────────

def _fetch_cos_training(user_id: str, token: str, keyword: str, location: str = "") -> list:
    """
    CareerOneStop /v1/training/{userId}/scholarship — the endpoint that actually exists.
    The /v1/scholarship endpoint returns 404; scholarship data is licensed from Gale Group.
    """
    url = (f"https://api.careeronestop.org/v1/training/{user_id}/scholarship"
           f"?keyword={_url_quote(keyword or 'scholarship')}"
           f"&trainingProgramLength=4&sortColumns=1&sortOrder=0&enableMetaData=true")
    if location:
        url += f"&location={_url_quote(location)}"
    print(f"[COS-Training] GET {url}")
    r = _req.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    print(f"[COS-Training] HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"[COS-Training] body: {r.text[:200]}")
        return []
    data  = r.json()
    items = (data.get("TrainingPrograms") or data.get("trainingPrograms")
             or data.get("Scholarships") or data.get("scholarships") or [])
    out = []
    for s in items[:50]:
        name = (s.get("TrainingName") or s.get("ScholarshipName")
                or s.get("ProgramName") or s.get("Name") or "").strip()
        if not name:
            continue
        out.append({
            "ScholarshipName": name[:120],
            "Provider":        (s.get("ProviderName") or s.get("Provider")
                               or s.get("Organization") or "CareerOneStop"),
            "Amount":          str(s.get("Amount") or s.get("TuitionAmount") or "See listing"),
            "DeadlineDate":    s.get("DeadlineDate") or s.get("ApplicationDeadline") or "",
            "URL":             (s.get("URL") or s.get("TrainingURL") or s.get("Link")
                               or "https://www.careeronestop.org/toolkit/training/find-scholarships.aspx"),
            "Description":     (s.get("Description") or s.get("TrainingDescription") or "")[:300],
            "Source":          "CareerOneStop Training",
        })
    return out


def _fetch_grants_gov(keyword: str) -> list:
    body = {
        "keyword":        keyword or "scholarship AI technology graduate",
        "oppStatuses":    "forecasted|posted",
        "rows":           50,
        "startRecordNum": 0,
    }
    print(f"[Grants.gov] POST keyword={keyword!r}")
    r = _req.post(
        "https://apply07.grants.gov/grantsws/rest/opportunities/search/",
        json=body, timeout=15,
    )
    print(f"[Grants.gov] HTTP {r.status_code}")
    if r.status_code != 200:
        return []
    hits = r.json().get("oppHits") or []
    out  = []
    for h in hits[:50]:
        title = (h.get("title") or h.get("oppTitle") or "").strip()
        if not title:
            continue
        synopsis = h.get("synopsis") or {}
        desc     = synopsis.get("text", "") if isinstance(synopsis, dict) else str(synopsis)
        opp_id   = h.get("id") or h.get("number") or ""
        info_url = (h.get("additionalInformationUrl")
                    or f"https://www.grants.gov/view-opportunity.html?oppId={opp_id}")
        out.append({
            "ScholarshipName": title[:120],
            "Provider":        h.get("agencyName") or h.get("agency") or "U.S. Federal Government",
            "Amount":          str(h.get("awardCeiling") or h.get("estimatedTotalProgramFunding") or "See listing"),
            "DeadlineDate":    h.get("closeDate") or h.get("closingDate") or "",
            "URL":             info_url,
            "Description":     desc[:300],
            "Source":          "Grants.gov (Federal)",
        })
    return out


def _fetch_scholarships_com(keyword: str) -> list:
    url = ("https://www.scholarships.com/financial-aid/college-scholarships/"
           "scholarships-by-type/ai-artificial-intelligence-scholarships/")
    print(f"[Scholarships.com] GET {url}")
    r = _req.get(url, headers=_SCRAPE_HEADERS, timeout=15)
    print(f"[Scholarships.com] HTTP {r.status_code}")
    if r.status_code != 200:
        return []
    soup = BeautifulSoup(r.text, "lxml")
    out  = []
    for item in soup.select(".scholarship-item, article, .result-item, .listing, .card")[:40]:
        title_el = item.select_one("h2, h3, .scholarship-name, .title, a")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if not title or len(title) < 8:
            continue
        link = title_el.find("a", href=True) or item.find("a", href=True)
        href = link["href"] if link else url
        if href.startswith("/"):
            href = "https://www.scholarships.com" + href
        amt_el = item.select_one(".amount, .award, .scholarship-amount")
        amount = amt_el.get_text(strip=True) if amt_el else "See listing"
        out.append({
            "ScholarshipName": title[:120],
            "Provider":        "Scholarships.com",
            "Amount":          amount,
            "DeadlineDate":    "",
            "URL":             href,
            "Description":     "",
            "Source":          "Scholarships.com",
        })
    return out


def _fetch_scholarshipsandgrants(keyword: str) -> list:
    url = "https://scholarshipsandgrants.us/major/ai-ml/"
    print(f"[ScholarshipsAndGrants] GET {url}")
    r = _req.get(url, headers=_SCRAPE_HEADERS, timeout=15)
    print(f"[ScholarshipsAndGrants] HTTP {r.status_code}")
    if r.status_code != 200:
        return []
    soup = BeautifulSoup(r.text, "lxml")
    out  = []
    for item in soup.select("article, .scholarship, .entry")[:40]:
        title_el = item.select_one("h2, h3, .entry-title")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if not title or len(title) < 8:
            continue
        link = title_el.find("a", href=True) or item.find("a", href=True)
        href = link["href"] if link else url
        if href.startswith("/"):
            href = "https://scholarshipsandgrants.us" + href
        amt_el = item.select_one(".amount")
        amount = amt_el.get_text(strip=True) if amt_el else "See listing"
        out.append({
            "ScholarshipName": title[:120],
            "Provider":        "ScholarshipsAndGrants.us",
            "Amount":          amount,
            "DeadlineDate":    "",
            "URL":             href,
            "Description":     "",
            "Source":          "ScholarshipsAndGrants",
        })
    return out


def _fetch_goingmerry(keyword: str) -> list:
    url = "https://www.goingmerry.com/scholarships?degree=graduate"
    if keyword:
        url += f"&q={_url_quote(keyword)}"
    print(f"[GoingMerry] GET {url}")
    r = _req.get(url, headers=_SCRAPE_HEADERS, timeout=15)
    print(f"[GoingMerry] HTTP {r.status_code}")
    if r.status_code != 200:
        return []
    soup = BeautifulSoup(r.text, "lxml")
    out  = []
    for card in soup.select(".scholarship-card, [class*='scholarship'], article, .card")[:30]:
        title_el = card.select_one("h2, h3, [class*='title'], [class*='name']")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if not title or len(title) < 6:
            continue
        link = card.find("a", href=True)
        href = link["href"] if link else url
        if href.startswith("/"):
            href = "https://www.goingmerry.com" + href
        amt_el = card.select_one("[class*='amount'], [class*='award']")
        amount = amt_el.get_text(strip=True) if amt_el else "See listing"
        out.append({
            "ScholarshipName": title[:120],
            "Provider":        "GoingMerry",
            "Amount":          amount,
            "DeadlineDate":    "",
            "URL":             href,
            "Description":     "",
            "Source":          "GoingMerry",
        })
    return out


def _fetch_scholarshipapi(api_key: str, keyword: str) -> list:
    print(f"[ScholarshipAPI] GET keyword={keyword!r}")
    r = _req.get(
        "https://scholarshipapi.com/api/v1/scholarships",
        headers={"X-Api-Key": api_key, "Authorization": f"Bearer {api_key}"},
        params={"search": keyword or "scholarship", "limit": 50},
        timeout=15,
    )
    print(f"[ScholarshipAPI] HTTP {r.status_code}")
    if r.status_code != 200:
        return []
    data  = r.json()
    items = data.get("scholarships") or data.get("data") or (data if isinstance(data, list) else [])
    out   = []
    for s in items[:30]:
        out.append({
            "ScholarshipName": (s.get("name") or s.get("scholarship_name") or s.get("title") or "Scholarship")[:120],
            "Provider":        s.get("sponsor_name") or s.get("organization") or s.get("provider") or "Unknown",
            "Amount":          str(s.get("award_amount") or s.get("amount") or "See listing"),
            "DeadlineDate":    s.get("deadline") or s.get("deadline_date") or "",
            "URL":             s.get("link") or s.get("url") or s.get("website") or "",
            "Description":     (s.get("description") or s.get("details") or "")[:300],
            "Source":          "ScholarshipAPI",
        })
    return out


# ── Parallel orchestrator ──────────────────────────────────────────────────────
def _fetch_all(user_id: str, token: str, keyword: str,
               location: str, sapi_key: str) -> tuple[list, dict]:
    """
    Call all sources in parallel. Returns (merged_list, source_breakdown).
    COS Training only runs when credentials are present.
    Sources that don't respond within _MULTI_TIMEOUT are skipped gracefully.
    """
    tasks: dict = {
        "Grants.gov":          lambda: _fetch_grants_gov(keyword),
        "Scholarships.com":    lambda: _fetch_scholarships_com(keyword),
        "ScholarshipsAndGrants": lambda: _fetch_scholarshipsandgrants(keyword),
        "GoingMerry":          lambda: _fetch_goingmerry(keyword),
    }
    if user_id and token:
        tasks["CareerOneStop Training"] = lambda: _fetch_cos_training(user_id, token, keyword, location)
    if sapi_key:
        tasks["ScholarshipAPI"] = lambda: _fetch_scholarshipapi(sapi_key, keyword)

    all_items: list = []
    breakdown: dict = {}

    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        future_map = {pool.submit(fn): name for name, fn in tasks.items()}
        done, timed_out = _fut_wait(future_map.keys(), timeout=_MULTI_TIMEOUT)

        for f in done:
            src = future_map[f]
            try:
                items = f.result()
                breakdown[src] = len(items)
                print(f"[multi] {src}: {len(items)} results")
                all_items.extend(items)
            except Exception as exc:
                print(f"[multi] {src} error: {exc}")
                breakdown[src] = 0

        for f in timed_out:
            src = future_map[f]
            print(f"[multi] {src} timed out")
            breakdown[src] = 0
            f.cancel()

    return _dedup(all_items), breakdown


# ── /  →  /widget ─────────────────────────────────────────────────────────────
@app.route("/")
def root():
    return redirect("/widget")


# ── /widget  →  widget.html ───────────────────────────────────────────────────
@app.route("/widget")
def widget():
    return send_file("widget.html")


# ── /api/health  →  credential check + live multi-source ping ────────────────
@app.route("/api/health")
def api_health():
    user_id  = (os.environ.get("CAREERONESTOP_USER_ID") or "").strip()
    token    = (os.environ.get("CAREERONESTOP_TOKEN")   or "").strip()
    sapi_key = (os.environ.get("SCHOLARSHIPAPI_KEY")    or "").strip()

    result: dict = {
        "CAREERONESTOP_USER_ID_set":   bool(user_id),
        "CAREERONESTOP_USER_ID_value": user_id or "(not set)",
        "CAREERONESTOP_TOKEN_set":     bool(token),
        "CAREERONESTOP_TOKEN_preview": (token[:8] + "…") if token else "(not set)",
        "SCHOLARSHIPAPI_KEY_set":      bool(sapi_key),
    }

    # ── Ping CareerOneStop Training endpoint (not /scholarship — that 404s) ──
    if user_id and token:
        test_url = (f"https://api.careeronestop.org/v1/training/{user_id}/scholarship"
                    f"?keyword=scholarship&trainingProgramLength=4&sortColumns=1&sortOrder=0"
                    f"&enableMetaData=true")
        result["cos_training_test_url"] = test_url
        print(f"[health] COS-Training ping: {test_url}")
        try:
            ping = _req.get(
                test_url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            result["cos_training_ping_status"] = ping.status_code
            result["cos_training_ping_ok"]     = ping.status_code == 200
            if ping.status_code != 200:
                result["cos_training_ping_body"] = ping.text[:300]
            else:
                try:
                    data  = ping.json()
                    items = (data.get("TrainingPrograms") or data.get("trainingPrograms")
                             or data.get("Scholarships") or [])
                    result["cos_training_ping_count"] = len(items)
                except Exception:
                    pass
        except Exception as exc:
            result["cos_training_ping_error"] = str(exc)
    else:
        result["cos_training_ping_ok"] = False
        result["cos_training_ping_note"] = "credentials not set — COS Training will be skipped"

    # ── Ping Grants.gov ───────────────────────────────────────────────────────
    print("[health] Grants.gov ping")
    try:
        gg = _req.post(
            "https://apply07.grants.gov/grantsws/rest/opportunities/search/",
            json={"keyword": "scholarship graduate", "oppStatuses": "posted", "rows": 1},
            timeout=10,
        )
        result["grantsgov_ping_status"] = gg.status_code
        result["grantsgov_ping_ok"]     = gg.status_code == 200
    except Exception as exc:
        result["grantsgov_ping_ok"]    = False
        result["grantsgov_ping_error"] = str(exc)

    # ── Ping Scholarships.com ─────────────────────────────────────────────────
    print("[health] Scholarships.com ping")
    try:
        sc = _req.get(
            "https://www.scholarships.com/financial-aid/college-scholarships/"
            "scholarships-by-type/ai-artificial-intelligence-scholarships/",
            headers=_SCRAPE_HEADERS, timeout=10,
        )
        result["scholarships_com_ping_status"] = sc.status_code
        result["scholarships_com_ping_ok"]     = sc.status_code == 200
    except Exception as exc:
        result["scholarships_com_ping_ok"]    = False
        result["scholarships_com_ping_error"] = str(exc)

    # ── Ping ScholarshipsAndGrants.us ─────────────────────────────────────────
    print("[health] ScholarshipsAndGrants.us ping")
    try:
        sg = _req.get(
            "https://scholarshipsandgrants.us/major/ai-ml/",
            headers=_SCRAPE_HEADERS, timeout=10,
        )
        result["scholarshipsandgrants_ping_status"] = sg.status_code
        result["scholarshipsandgrants_ping_ok"]     = sg.status_code == 200
    except Exception as exc:
        result["scholarshipsandgrants_ping_ok"]    = False
        result["scholarshipsandgrants_ping_error"] = str(exc)

    # ── Ping GoingMerry ───────────────────────────────────────────────────────
    print("[health] GoingMerry ping")
    try:
        gm = _req.get(
            "https://www.goingmerry.com/scholarships?degree=graduate",
            headers=_SCRAPE_HEADERS, timeout=10,
        )
        result["goingmerry_ping_status"] = gm.status_code
        result["goingmerry_ping_ok"]     = gm.status_code == 200
    except Exception as exc:
        result["goingmerry_ping_ok"]    = False
        result["goingmerry_ping_error"] = str(exc)

    # ── ScholarshipAPI.com ────────────────────────────────────────────────────
    result["scholarshipapi_configured"] = bool(sapi_key)

    return jsonify(result)


# ── /api/scholarships  →  multi-source parallel proxy ────────────────────────
@app.route("/api/scholarships")
def scholarships_proxy():
    user_id  = (os.environ.get("CAREERONESTOP_USER_ID") or "").strip()
    token    = (os.environ.get("CAREERONESTOP_TOKEN")   or "").strip()
    sapi_key = (os.environ.get("SCHOLARSHIPAPI_KEY")    or "").strip()

    incoming = dict(request.args)
    keyword  = incoming.get("keyword", "scholarship")
    location = incoming.get("location", "")
    print(f"[API] /api/scholarships keyword={keyword!r} location={location!r} "
          f"cos={'set' if (user_id and token) else 'no-creds'}")

    cache_key = hashlib.md5(json.dumps(sorted(incoming.items())).encode()).hexdigest()
    cached    = _cache_get(cache_key)
    if cached:
        print(f"[API] Cache hit ({cache_key[:8]})")
        return Response(
            json.dumps(cached), status=200,
            headers={
                "Content-Type":                "application/json",
                "Access-Control-Allow-Origin": "*",
                "Cache-Control":               "public, max-age=120",
                "X-Cache":                     "HIT",
            },
        )

    print(f"[API] Cache miss — fetching all sources")
    merged, breakdown = _fetch_all(user_id, token, keyword, location, sapi_key)

    payload = {
        "Scholarships":    merged,
        "Count":           len(merged),
        "SourceBreakdown": breakdown,
    }
    _cache_set(cache_key, payload)

    print(f"[API] Returning {len(merged)} merged results — breakdown: {breakdown}")
    return Response(
        json.dumps(payload), status=200,
        headers={
            "Content-Type":                "application/json",
            "Access-Control-Allow-Origin": "*",
            "Cache-Control":               "public, max-age=120",
            "X-Cache":                     "MISS",
        },
    )


# ── /dashboard*  →  HTTP reverse proxy to Streamlit ──────────────────────────
_WARMUP_HTML = (
    "<html><body style='font-family:sans-serif;text-align:center;padding:15vh 0;color:#555'>"
    "<h3>Dashboard is warming up…</h3>"
    "<p>Streamlit is starting. This page will reload automatically.</p>"
    "<script>setTimeout(()=>location.reload(),3000)</script>"
    "</body></html>"
)


@app.route("/dashboard", defaults={"subpath": ""})
@app.route("/dashboard/", defaults={"subpath": ""})
@app.route("/dashboard/<path:subpath>")
def dashboard_http(subpath):
    target = f"{_STREAMLIT_BASE}/{subpath}"
    if request.query_string:
        target += "?" + request.query_string.decode("utf-8", errors="replace")

    try:
        upstream = _req.request(
            method=request.method,
            url=target,
            headers=_fwd(dict(request.headers)),
            data=request.get_data(),
            allow_redirects=False,
            stream=True,
            timeout=30,
        )
    except _req.ConnectionError:
        return _WARMUP_HTML, 503, {"Content-Type": "text/html"}
    except _req.Timeout:
        return "Streamlit gateway timeout", 504

    # Rewrite redirect Location headers so /dashboard prefix is preserved
    out_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }
    if "location" in upstream.headers:
        loc = upstream.headers["location"]
        if loc.startswith("/") and not loc.startswith("/dashboard"):
            out_headers["location"] = "/dashboard" + loc

    return Response(
        stream_with_context(upstream.iter_content(chunk_size=8192)),
        status=upstream.status_code,
        headers=out_headers,
    )


# ── /dashboard/_stcore/stream  →  WebSocket proxy ────────────────────────────
@sock.route("/dashboard/_stcore/stream")
def dashboard_ws(browser_ws):
    """
    Bidirectional WebSocket relay: browser ↔ Flask ↔ Streamlit.
    One thread forwards Streamlit → browser; the main loop handles browser → Streamlit.
    """
    st_ws = _ws.WebSocket()
    try:
        st_ws.connect(f"{_STREAMLIT_WS}/_stcore/stream")
    except Exception as exc:
        print(f"[ws] Could not connect to Streamlit WS: {exc}")
        return

    done = threading.Event()

    def st_to_browser():
        while not done.is_set():
            try:
                msg = st_ws.recv()
                if msg is None:
                    break
                browser_ws.send(msg)
            except Exception:
                break
        done.set()

    relay = threading.Thread(target=st_to_browser, daemon=True)
    relay.start()

    try:
        while not done.is_set():
            try:
                msg = browser_ws.receive(timeout=10)
                if msg is None:
                    break
                st_ws.send(msg)
            except Exception:
                break
    finally:
        done.set()
        try:
            st_ws.close()
        except Exception:
            pass


# ── CORS preflight for /api/* ─────────────────────────────────────────────────
@app.after_request
def add_cors(response):
    if request.path.startswith("/api/"):
        response.headers["Access-Control-Allow-Origin"]  = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response


# ── Entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Flask serving on port {port}")
    # threaded=True is required so WebSocket and HTTP requests don't block each other
    app.run(host="0.0.0.0", port=port, threaded=True, debug=False)
