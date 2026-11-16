"""
The Provider API collector and the Agency API push client.

The collector is the part a real integration needs beyond "call the endpoint":
it distinguishes the response states the specification defines, retries what is
worth retrying, records what it could not get rather than reporting zero, and
never counts an hour twice.
"""
# Paths are resolved from this package, so the suite runs from a clone.
import pathlib as _pathlib
PKG_ROOT = _pathlib.Path(__file__).resolve().parent.parent
SUITE_DIR = PKG_ROOT / "suites"
OUT_ROOT = PKG_ROOT / "out"


import json
import urllib.error
import urllib.request


class Ledger:
    """What was asked for, what came back, and what is still outstanding.

    Treating an unavailable hour as zero trips is the common integration
    failure. The ledger exists so that cannot happen silently.
    """

    def __init__(self):
        self.rows = []

    def record(self, provider, hour, status, state, trips=0):
        self.rows.append({"provider": provider, "hour": hour,
                          "http": status, "state": state, "trips": trips})

    def outstanding(self):
        return [r for r in self.rows if r["state"] in ("pending", "error")]

    def hours_with_data(self):
        return [r for r in self.rows if r["state"] == "received"]


def fetch_hour(base_url, token, hour, timeout=5):
    """One Provider API request, with every documented state handled."""
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/trips?end_time={hour}",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read() or b"{}")
            if resp.status == 202:
                return {"status": 202, "state": "pending", "trips": []}
            return {"status": resp.status, "state": "received",
                    "trips": body.get("trips", []),
                    "version": body.get("version")}
    except urllib.error.HTTPError as e:
        if e.code == 202:
            return {"status": 202, "state": "pending", "trips": []}
        if e.code == 401:
            return {"status": 401, "state": "unauthorized", "trips": []}
        if e.code == 404:
            return {"status": 404, "state": "not_available", "trips": []}
        return {"status": e.code, "state": "error", "trips": []}
    except Exception:
        return {"status": None, "state": "error", "trips": []}


def collect(base_url, token, hours, ledger, provider="Veo", max_attempts=3):
    """Walk a list of hours, retrying only what retrying can fix."""
    gathered = {}
    for hour in hours:
        attempt = 0
        while True:
            attempt += 1
            r = fetch_hour(base_url, token, hour)
            # A 500 is worth retrying; a 401 or 404 is not.
            if r["state"] == "error" and attempt < max_attempts:
                continue
            break
        ledger.record(provider, hour, r["status"], r["state"], len(r["trips"]))
        for t in r["trips"]:
            gathered[t["trip_id"]] = t          # keyed, so a repeat cannot inflate
    return gathered


def push_to_agency(base_url, token, trips, timeout=5):
    """The Agency direction: an operator submitting completed trips to us."""
    data = json.dumps({"trips": trips}).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/mds/agency/trips", data=data, method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")
    except Exception as exc:
        return None, {"error": str(exc)}
