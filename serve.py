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


# ── /api/health  →  quick credential + connectivity check ─────────────────────
@app.route("/api/health")
def api_health():
    user_id = os.environ.get("CAREERONESTOP_USER_ID", "")
    token   = os.environ.get("CAREERONESTOP_TOKEN", "")
    result  = {
        "CAREERONESTOP_USER_ID_set": bool(user_id),
        "CAREERONESTOP_USER_ID_value": user_id or "(not set)",
        "CAREERONESTOP_TOKEN_set": bool(token),
        "CAREERONESTOP_TOKEN_preview": (token[:8] + "…") if token else "(not set)",
    }
    # Quick live ping to the API with a tiny request
    if user_id and token:
        try:
            ping = _req.get(
                f"https://api.careeronestop.org/v1/scholarship/{user_id}",
                headers={"Authorization": f"Bearer {token}"},
                params={"keyword": "scholarship", "limit": "1"},
                timeout=10,
            )
            result["api_ping_status"] = ping.status_code
            result["api_ping_ok"]     = ping.status_code == 200
            if ping.status_code != 200:
                result["api_ping_body"] = ping.text[:400]
        except Exception as exc:
            result["api_ping_error"] = str(exc)
    return jsonify(result)


# ── /api/scholarships  →  CareerOneStop proxy ─────────────────────────────────
@app.route("/api/scholarships")
def cos_proxy():
    user_id = os.environ.get("CAREERONESTOP_USER_ID")
    token   = os.environ.get("CAREERONESTOP_TOKEN")

    print(f"[API] /api/scholarships called — user_id={'set' if user_id else 'MISSING'}, "
          f"token={'set' if token else 'MISSING'}")

    if not user_id or not token:
        msg = ("CareerOneStop credentials not configured on server. "
               "Set CAREERONESTOP_USER_ID and CAREERONESTOP_TOKEN env vars.")
        print(f"[API] ERROR: {msg}")
        return jsonify({"error": msg}), 503

    # Forward all query params from the browser completely unchanged — no overrides
    params = dict(request.args)
    target_url = f"https://api.careeronestop.org/v1/scholarship/{user_id}"
    full_url   = f"{target_url}?{urlencode(params)}"
    print(f"[API] Incoming params: {params}")
    print(f"[API] Calling CareerOneStop: {full_url}")

    try:
        resp = _req.get(
            target_url,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=25,
        )
        print(f"[API] CareerOneStop response: HTTP {resp.status_code} "
              f"({len(resp.content)} bytes)")
        if resp.status_code != 200:
            print(f"[API] Error body: {resp.text[:500]}")
            # Pass the real error body back to the browser so it's visible
            return Response(
                resp.content,
                status=resp.status_code,
                headers={
                    "Content-Type":                "application/json",
                    "Access-Control-Allow-Origin": "*",
                },
            )
        return Response(
            resp.content,
            status=200,
            headers={
                "Content-Type":                resp.headers.get("Content-Type", "application/json"),
                "Access-Control-Allow-Origin": "*",
                "Cache-Control":               "public, max-age=120",
            },
        )
    except _req.Timeout:
        print("[API] ERROR: CareerOneStop timed out after 25 s")
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
