"""Local browser charts for desktop companions; no Cocoa or GTK dependencies."""

from __future__ import annotations

import json
import secrets
import threading
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ai_usage_intraday import INTERVAL_MINUTES, bucket_costs, bucket_model_costs, cost_breakdown


ASSET_ROOT = Path(__file__).resolve().parent / "assets" / "usage-graphs"


def day_usage(snapshot, day):
    if day == snapshot.refreshed_at.date():
        return snapshot.intraday
    return snapshot.intraday_days.get(day)


def snapshot_payload(snapshot, project=""):
    """Expose only chart aggregates, never session contents or identifiers."""
    now = snapshot.refreshed_at
    months = snapshot.monthly_daily_costs or {now.date().replace(day=1): snapshot.daily_costs}
    rows = {}
    models = set()
    for month, days in sorted(months.items()):
        rows[month.isoformat()[:7]] = []
        for day, total in days:
            parts = cost_breakdown(total, snapshot.daily_model_costs.get(day, {}))
            models.update(parts)
            usage = day_usage(snapshot, day)
            rows[month.isoformat()[:7]].append({
                "date": day.isoformat(), "total": total, "models": parts,
                "details": usage is not None and not usage.error,
            })
    for usage in (*snapshot.intraday_days.values(), snapshot.intraday):
        if usage is not None and not usage.error:
            models.update(model for _stamp, model, cost in usage.model_costs if cost > 0)
    return {
        "updated": now.astimezone().isoformat(), "today": now.date().isoformat(),
        "monthTotal": snapshot.month_cost, "todayTotal": snapshot.today_cost,
        "months": rows, "models": sorted(models), "project": project,
    }


def day_payload(snapshot, day, minutes):
    if minutes not in INTERVAL_MINUTES or day > snapshot.refreshed_at.date():
        raise ValueError("Invalid day or interval")
    usage = day_usage(snapshot, day)
    if usage is None or usage.error:
        error = (usage.error if usage is not None else "")
        if not error and snapshot.intraday is not None:
            error = snapshot.intraday.error
        return {"date": day.isoformat(), "series": [], "unpriced": 0,
                "error": error or "Timestamped usage is unavailable for this day."}
    now = snapshot.refreshed_at
    totals = bucket_costs(usage.costs, now, minutes, day)
    parts = bucket_model_costs(usage.model_costs, now, minutes, day)
    series = []
    for (stamp, total), models in zip(totals, parts):
        end = datetime.fromtimestamp(stamp.timestamp() + minutes * 60).astimezone()
        if day == now.date():
            end = min(end, now.astimezone())
        series.append({
            "label": stamp.strftime("%H:%M"),
            "range": f"{stamp.strftime('%H:%M %Z')}–{end.strftime('%H:%M %Z')}",
            "total": total, "models": cost_breakdown(total, models),
        })
    return {"date": day.isoformat(), "series": series,
            "unpriced": usage.unpriced, "error": ""}


class UsageGraphServer:
    """An on-demand, loopback-only view of the last successful usage snapshot."""

    def __init__(self, refresh, project=""):
        self.refresh = refresh
        self.project = project
        self._lock = threading.Lock()
        self._snapshot = None
        self._payload = None
        self._status = "Waiting for usage…"
        self._refreshing = False
        self._server = None
        self._thread = None

    def set_usage(self, snapshot):
        payload = snapshot_payload(snapshot, self.project)
        with self._lock:
            self._snapshot, self._payload = snapshot, payload

    def set_status(self, message, refreshing=False):
        with self._lock:
            self._status, self._refreshing = message, refreshing

    def start(self):
        if self._server is not None:
            return self.url
        assets = {
            "": ("text/html; charset=utf-8", (ASSET_ROOT / "index.html").read_bytes()),
            "chart.js": ("text/javascript; charset=utf-8", (ASSET_ROOT / "chart.js").read_bytes()),
            "chart.css": ("text/css; charset=utf-8", (ASSET_ROOT / "chart.css").read_bytes()),
        }
        owner = self
        prefix = "/" + secrets.token_urlsafe(24) + "/"

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                pass  # Do not put the private URL or usage data in access logs.

            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def respond(self, status, body=b"", content_type="text/plain; charset=utf-8"):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; "
                                 "style-src 'self'; img-src data:; connect-src 'self'; frame-ancestors 'none'; "
                                 "base-uri 'none'; form-action 'none'")
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def route(self):
                parsed = urlsplit(self.path)
                if (self.headers.get("Host") != owner.authority
                        or self.headers.get("Origin") not in (None, owner.origin)
                        or not parsed.path.startswith(prefix)):
                    self.respond(403)
                    return None
                return parsed.path[len(prefix):], parse_qs(parsed.query)

            def do_GET(self):
                route = self.route()
                if route is None:
                    return
                path, query = route
                if path in assets:
                    content_type, body = assets[path]
                    self.respond(200, body, content_type)
                    return
                with owner._lock:
                    snapshot, payload = owner._snapshot, owner._payload
                    status, refreshing = owner._status, owner._refreshing
                if path == "data":
                    data = {"usage": payload, "status": status, "refreshing": refreshing}
                elif path == "day" and snapshot is not None:
                    try:
                        data = day_payload(snapshot, date.fromisoformat(query["date"][0]),
                                           int(query["interval"][0]))
                    except (KeyError, ValueError, IndexError):
                        self.respond(400)
                        return
                else:
                    self.respond(404)
                    return
                self.respond(200, json.dumps(data, allow_nan=False).encode(), "application/json")

            def do_POST(self):
                route = self.route()
                if route is None:
                    return
                if (route[0] != "refresh" or self.headers.get("Origin") != owner.origin
                        or self.headers.get("Content-Type") != "application/json"
                        or self.headers.get("Content-Length", "0") != "0"):
                    self.respond(403)
                    return
                owner.refresh()
                self.respond(202)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.authority = f"127.0.0.1:{server.server_port}"
        self.origin = f"http://{self.authority}"
        self.url = self.origin + prefix
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever,
                                        kwargs={"poll_interval": 0.1}, daemon=True)
        self._thread.start()
        return self.url

    def close(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join(timeout=2)
            self._server = self._thread = None
