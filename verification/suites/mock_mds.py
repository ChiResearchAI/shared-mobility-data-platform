"""
A local MDS mock: a Provider API server and an Agency API receiver.

Nothing here talks to Veo or Spin. The point is to exercise the transport —
bearer tokens, the 200/202/401/404 states the specification defines, retries,
replay protection — against a server that behaves like the specification says
an operator's will. The handling was already proven; this proves the plumbing
around it, and leaves only the credential outstanding.
"""
# Paths are resolved from this package, so the suite runs from a clone.
import pathlib as _pathlib
PKG_ROOT = _pathlib.Path(__file__).resolve().parent.parent
SUITE_DIR = PKG_ROOT / "suites"
OUT_ROOT = PKG_ROOT / "out"


import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

VALID_PROVIDER_TOKEN = "test-provider-token-not-a-real-credential"
VALID_AGENCY_TOKEN = "test-agency-token-not-a-real-credential"
KNOWN_PROVIDER_ID = "11111111-1111-4111-8111-111111111111"

# Hours the mock will answer for, and how. Mirrors what a real operator does:
# a recent hour may not be ready yet, and an hour before the operator launched
# does not exist.
HOUR_BEHAVIOUR = {
    "2026-11-14T14": "ok",            # data present
    "2026-11-14T15": "ok",
    "2026-11-14T16": "pending",       # 202, not ready yet
    "2026-11-14T17": "not_found",     # 404, provider not operating
    "2026-11-14T18": "flaky",         # fails once, then succeeds
}

_flaky_hits = {}
AGENCY_LOG = []

# Replay protection has to survive a restart, or it is not replay protection:
# an in-memory set forgets everything the moment the receiver is recycled, and
# the operator's next retransmission is counted as a new trip. The uniqueness
# constraint lives in the database.
AGENCY_DB = Path(os.environ.get("CHIRES_AGENCY_DB", "/tmp/chires_agency.db"))


def agency_db():
    con = sqlite3.connect(AGENCY_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS received_trips(
        provider_id TEXT NOT NULL,
        trip_id     TEXT NOT NULL,
        received_at TEXT NOT NULL,
        payload     TEXT NOT NULL,
        PRIMARY KEY (provider_id, trip_id))""")
    con.commit()
    return con


def agency_count():
    con = agency_db()
    n = con.execute("SELECT COUNT(*) FROM received_trips").fetchone()[0]
    con.close()
    return n


def agency_reset():
    if AGENCY_DB.exists():
        AGENCY_DB.unlink()
    agency_db().close()


def _trip(hour):
    start = datetime.strptime(hour, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    end = start.replace(minute=30)
    return {
        "provider_id": KNOWN_PROVIDER_ID,
        "device_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, "device-" + hour)),
        "trip_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, "trip-" + hour)),
        "start_time": int(start.timestamp() * 1000),
        "end_time": int(end.timestamp() * 1000),
        "start_location": {"lat": 39.9612, "lng": -82.9988},
        "end_location": {"lat": 39.9650, "lng": -83.0020},
        "duration": 1800,
        "distance": 1400,
        "vehicle_type": "scooter",
    }


class MockMDS(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass                            # keep the test output readable

    def _send(self, code, body=None):
        payload = json.dumps(body or {}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _bearer(self):
        auth = self.headers.get("Authorization", "")
        return auth[7:] if auth.startswith("Bearer ") else None

    # ------------------------------------------------ Provider API (we pull)
    def do_GET(self):
        if not self.path.startswith("/trips"):
            return self._send(404, {"error": "unknown endpoint"})
        if self._bearer() != VALID_PROVIDER_TOKEN:
            return self._send(401, {"error": "invalid or expired token"})

        hour = None
        if "end_time=" in self.path:
            hour = self.path.split("end_time=")[1].split("&")[0]
        behaviour = HOUR_BEHAVIOUR.get(hour, "ok")

        if behaviour == "pending":
            return self._send(202, {"version": "2.0.2"})
        if behaviour == "not_found":
            return self._send(404, {"error": "no data for that hour"})
        if behaviour == "flaky":
            _flaky_hits[hour] = _flaky_hits.get(hour, 0) + 1
            if _flaky_hits[hour] == 1:
                return self._send(500, {"error": "transient"})
        return self._send(200, {"version": "2.0.2", "trips": [_trip(hour)]})

    # --------------------------------------------- Agency API (they push us)
    def do_POST(self):
        if self.path != "/mds/agency/trips":
            return self._send(404, {"error": "unknown endpoint"})
        if self._bearer() != VALID_AGENCY_TOKEN:
            AGENCY_LOG.append(("rejected", "bad token"))
            return self._send(401, {"error": "invalid token"})

        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            AGENCY_LOG.append(("rejected", "malformed body"))
            return self._send(400, {"error": "malformed json"})

        trips = body.get("trips")
        if not isinstance(trips, list):
            AGENCY_LOG.append(("rejected", "no trips array"))
            return self._send(400, {"error": "trips array required"})

        accepted, duplicates, refused = 0, 0, 0
        con = agency_db()
        for t in trips:
            # The pusher must be the provider it claims to be.
            if t.get("provider_id") != KNOWN_PROVIDER_ID:
                refused += 1
                AGENCY_LOG.append(("rejected", "provider identity mismatch"))
                continue
            tid = t.get("trip_id")
            if not tid:
                refused += 1
                AGENCY_LOG.append(("rejected", "missing trip_id"))
                continue
            # The database decides, not a process-local set.
            cur = con.execute(
                "INSERT OR IGNORE INTO received_trips VALUES (?,?,?,?)",
                (t["provider_id"], tid,
                 datetime.now(timezone.utc).isoformat(), json.dumps(t)))
            if cur.rowcount == 0:
                duplicates += 1
                AGENCY_LOG.append(("duplicate", tid))
            else:
                accepted += 1
                AGENCY_LOG.append(("accepted", tid))
        con.commit()
        con.close()

        code = 201 if accepted else 200
        return self._send(code, {"accepted": accepted, "duplicates": duplicates,
                                 "refused": refused})


def start(port=8731):
    server = HTTPServer(("127.0.0.1", port), MockMDS)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    return server
