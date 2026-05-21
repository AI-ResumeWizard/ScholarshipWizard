"""
serve.py — Single entrypoint for the scholarship app.

Routes:
  GET  /              → redirect to /widget
  GET  /widget        → serve widget.html (embeddable scholarship search)
  GET  /api/scholarships → CareerOneStop API proxy (server-side, no CORS issues)
  ALL  /dashboard*    → HTTP reverse proxy to Streamlit (port 8501)
  WS   /dashboard/_stcore/stream → WebSocket proxy to Streamlit

Streamlit is started as a background subprocess and polled until ready.
"""

import os
import subprocess
import threading
import time
from urllib.parse import urlencode

import requests as _req
import websocket as _ws
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


# ── /  →  /widget ─────────────────────────────────────────────────────────────
@app.route("/")
def root():
    return redirect("/widget")


# ── /widget  →  widget.html ───────────────────────────────────────────────────
@app.route("/widget")
def widget():
    return send_file("widget.html")


# CareerOneStop documented query params — anything outside this list can cause 404s
_COS_SUPPORTED_PARAMS = frozenset({
    "keyword", "limit", "startRecord",
    "trainingProgramLength", "sortColumns", "sortOrder",
})


def _build_cos_url(user_id: str, params: dict) -> str:
    """
    Construct the CareerOneStop URL explicitly.
    Only forward params the API actually supports; extras cause 404.
    """
    cos_params = {k: v for k, v in params.items() if k in _COS_SUPPORTED_PARAMS}
    # keyword is required — fall back to 'scholarship' if somehow absent
    cos_params.setdefault("keyword", "scholarship")
    cos_params.setdefault("limit",   "50")
    return f"https://api.careeronestop.org/v1/scholarship/{user_id}?{urlencode(cos_params)}"


# ── /api/health  →  credential check + live API ping ─────────────────────────
@app.route("/api/health")
def api_health():
    # Strip whitespace — Render env-var copy-paste often adds trailing spaces
    user_id = (os.environ.get("CAREERONESTOP_USER_ID") or "").strip()
    token   = (os.environ.get("CAREERONESTOP_TOKEN")   or "").strip()

    result = {
        "CAREERONESTOP_USER_ID_set":     bool(user_id),
        "CAREERONESTOP_USER_ID_value":   user_id or "(not set)",
        "CAREERONESTOP_TOKEN_set":       bool(token),
        "CAREERONESTOP_TOKEN_preview":   (token[:8] + "…") if token else "(not set)",
    }

    if user_id and token:
        # Test the exact URL format with keyword=nursing as specified
        test_url = f"https://api.careeronestop.org/v1/scholarship/{user_id}?keyword=nursing&limit=10"
        result["api_test_url"] = test_url
        print(f"[health] Pinging: {test_url}")
        try:
            ping = _req.get(
                test_url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            result["api_ping_status"] = ping.status_code
            result["api_ping_ok"]     = ping.status_code == 200
            if ping.status_code != 200:
                result["api_ping_body"] = ping.text[:500]
            else:
                # Show how many results came back as a sanity check
                try:
                    data = ping.json()
                    count = len(data.get("Scholarships") or data.get("scholarships") or [])
                    result["api_ping_scholarship_count"] = count
                except Exception:
                    pass
        except Exception as exc:
            result["api_ping_error"] = str(exc)

    return jsonify(result)


# ── /api/scholarships  →  CareerOneStop proxy ─────────────────────────────────
@app.route("/api/scholarships")
def cos_proxy():
    # Strip whitespace — trailing spaces in Render env vars are a common cause of 404
    user_id = (os.environ.get("CAREERONESTOP_USER_ID") or "").strip()
    token   = (os.environ.get("CAREERONESTOP_TOKEN")   or "").strip()

    print(f"[API] /api/scholarships — user_id={'set' if user_id else 'MISSING'}, "
          f"token={'set' if token else 'MISSING'}")

    if not user_id or not token:
        msg = ("CareerOneStop credentials not configured. "
               "Set CAREERONESTOP_USER_ID and CAREERONESTOP_TOKEN in Render env vars.")
        print(f"[API] ERROR: {msg}")
        return jsonify({"error": msg}), 503

    incoming = dict(request.args)
    print(f"[API] Incoming params from browser: {incoming}")

    # Build the URL explicitly with only documented CareerOneStop params.
    # Forwarding unknown params (awardAmountMin, typeOfAward, etc.) causes 404.
    full_url = _build_cos_url(user_id, incoming)
    print(f"[API] Calling: {full_url}")

    try:
        resp = _req.get(
            full_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=25,
        )
        print(f"[API] Response: HTTP {resp.status_code} ({len(resp.content)} bytes)")
        if resp.status_code != 200:
            print(f"[API] Error body: {resp.text[:500]}")

        return Response(
            resp.content,
            status=resp.status_code,
            headers={
                "Content-Type":                resp.headers.get("Content-Type", "application/json"),
                "Access-Control-Allow-Origin": "*",
                "Cache-Control":               "public, max-age=120" if resp.status_code == 200 else "no-cache",
            },
        )
    except _req.Timeout:
        print("[API] ERROR: timed out after 25 s")
        return jsonify({"error": "CareerOneStop API timed out (25 s)"}), 504
    except Exception as exc:
        print(f"[API] ERROR: {exc}")
        return jsonify({"error": str(exc)}), 502


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
