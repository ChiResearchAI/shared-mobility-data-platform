"""
OneView-compatible 311 case adapter, run against a clearly labelled simulator.

Columbus runs OneView for 311. ChiResearch has no OneView credential and no
City-approved interface control document, so this talks to a local simulator,
not to the City. What it proves is the adapter: that complaints become cases
once, that updates land on the existing case, that a reopened or cancelled
request is handled rather than ignored, and that a retry cannot create a
second case or a second enforcement action.

The compliance engine never talks to a vendor API directly. It talks to the
internal case model below, so a OneView change does not reach the engine.
"""
# Paths are resolved from this package, so the suite runs from a clone.
import pathlib as _pathlib
PKG_ROOT = _pathlib.Path(__file__).resolve().parent.parent
SUITE_DIR = PKG_ROOT / "suites"
OUT_ROOT = PKG_ROOT / "out"


import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = OUT_ROOT / "oneview"
OUT.mkdir(parents=True, exist_ok=True)

# The City's published report card carries an ADA obstruction response
# standard. These are the historical reported terms, not the executed
# contract, and the clock below is versioned so the real one can replace it.
SLA = {
    "rule_id": "IP1",
    "version": "report-card-2025",
    "statement": "ADA obstruction reports receive a response within one hour "
                 "during specified daytime hours, with next-morning handling "
                 "for certain after-hours reports.",
    "response_target_minutes": 60,
    "daytime_start_hour": 7,
    "daytime_end_hour": 19,
    "status": "historical_reported_terms_not_executed_contract",
    "enforcement_enabled": False,
}


class CaseStore:
    """The internal case model. OneView stays the system of record for the
    service request; this holds the linked compliance case."""

    def __init__(self):
        self.cases = {}          # external_request_id -> case
        self.audit = []

    def _log(self, external_id, action, detail=""):
        self.audit.append({"at": datetime.now(timezone.utc).isoformat(),
                           "external_request_id": external_id,
                           "action": action, "detail": detail})

    def ingest(self, event):
        """Insert or update from a OneView-shaped event. Idempotent by
        (external_request_id, last_updated_at)."""
        ext = event.get("external_request_id")
        if not ext:
            self._log(None, "rejected", "no external_request_id")
            return "rejected"

        revision = event.get("last_updated_at")
        existing = self.cases.get(ext)

        if existing is None:
            if event.get("latitude") is None or event.get("longitude") is None:
                status = "manual_review"
                detail = "no coordinates on the service request"
            elif event.get("operator") in (None, "", "unidentified"):
                status = "unassigned"
                detail = "no operator identified; no automatic enforcement"
            else:
                status = "open"
                detail = ""
            self.cases[ext] = {
                "external_request_id": ext,
                "category": event.get("category"),
                "received_at": event.get("received_at"),
                "latitude": event.get("latitude"),
                "longitude": event.get("longitude"),
                "operator": event.get("operator"),
                "device_id": event.get("device_id"),
                "oneview_status": event.get("status"),
                "case_status": status,
                "revision": revision,
                "revisions_seen": [revision],
                "notified_at": None,
                "resolved_at": None,
            }
            self._log(ext, "case_created", detail or status)
            return "created"

        if revision in existing["revisions_seen"]:
            self._log(ext, "duplicate_ignored", f"revision {revision}")
            return "duplicate"

        # A later revision updates the case; history is kept, never overwritten.
        existing["revisions_seen"].append(revision)
        existing["revision"] = revision
        previous = existing["oneview_status"]
        existing["oneview_status"] = event.get("status", previous)
        if event.get("status") == "cancelled":
            existing["case_status"] = "cancelled"
        elif event.get("status") == "reopened":
            existing["case_status"] = "open"
            existing["resolved_at"] = None
        for field in ("latitude", "longitude", "operator", "device_id", "category"):
            if event.get(field) not in (None, "", "unidentified"):
                existing[field] = event[field]
        if existing["case_status"] == "unassigned" and existing.get("operator"):
            existing["case_status"] = "open"
        self._log(ext, "case_updated", f"{previous} -> {existing['oneview_status']}")
        return "updated"

    def notify(self, ext, when):
        c = self.cases.get(ext)
        if not c or c["case_status"] in ("cancelled", "manual_review", "unassigned"):
            return False
        if c["notified_at"]:
            self._log(ext, "notify_skipped", "already notified")
            return False
        c["notified_at"] = when.isoformat()
        self._log(ext, "operator_notified", when.isoformat())
        return True

    def resolve(self, ext, when):
        c = self.cases.get(ext)
        if not c:
            return False
        c["resolved_at"] = when.isoformat()
        if c["case_status"] != "cancelled":
            c["case_status"] = "resolved"
        self._log(ext, "resolution_recorded", when.isoformat())
        return True


def assess_response_clock(case):
    """The IP1 clock, with each element stated.

    Receiving a complaint, notifying an operator, physically clearing the
    obstruction and administratively closing the 311 case happen at different
    times. The clock measures the one the rule names and says which.
    """
    if case["case_status"] in ("manual_review", "unassigned", "cancelled"):
        return {"determination": "NOT ASSESSABLE",
                "reason": f'case is {case["case_status"]}',
                "elements": {}}
    if not case["received_at"]:
        return {"determination": "NOT ASSESSABLE",
                "reason": "no receipt time on the service request", "elements": {}}

    received = datetime.fromisoformat(case["received_at"].replace("Z", "+00:00"))
    in_daytime = SLA["daytime_start_hour"] <= received.hour < SLA["daytime_end_hour"]
    if in_daytime:
        deadline = received + timedelta(minutes=SLA["response_target_minutes"])
        basis = f'{SLA["response_target_minutes"]} minutes from receipt, daytime'
    else:
        nxt = (received + timedelta(days=1)).replace(
            hour=SLA["daytime_start_hour"], minute=0, second=0, microsecond=0)
        deadline = nxt
        basis = "after hours: next morning handling"

    if not case["notified_at"]:
        return {"determination": "NOT ASSESSABLE",
                "reason": "operator has not been notified; the clock has no stop",
                "elements": {"received": case["received_at"], "deadline": deadline.isoformat(),
                             "basis": basis}}

    notified = datetime.fromisoformat(case["notified_at"])
    met = notified <= deadline
    return {
        "determination": "MET" if met else "MISSED",
        "reason": f'notified {(notified - received).total_seconds()/60:.0f} min after receipt',
        "elements": {
            "rule": f'{SLA["rule_id"]} ({SLA["version"]})',
            "received": received.isoformat(),
            "basis": basis,
            "deadline": deadline.isoformat(),
            "notified": notified.isoformat(),
            "resolved": case["resolved_at"],
            "enforcement_enabled": SLA["enforcement_enabled"],
        },
    }


def simulator_events():
    """A labelled OneView simulator. These are invented service requests."""
    base = datetime(2026, 11, 14, 14, 20, tzinfo=timezone.utc)
    iso = lambda t: t.isoformat().replace("+00:00", "Z")
    return [
        ("new complaint", {
            "source_system": "columbus_oneview_SIMULATOR",
            "external_request_id": "TEST-SR-0001",
            "category": "shared_mobility_obstruction",
            "received_at": iso(base), "latitude": 39.9612, "longitude": -82.9988,
            "operator": "Veo", "device_id": "dev-1", "status": "received",
            "priority": "accessibility", "last_updated_at": iso(base)}),
        ("same complaint repeated", {
            "source_system": "columbus_oneview_SIMULATOR",
            "external_request_id": "TEST-SR-0001",
            "category": "shared_mobility_obstruction",
            "received_at": iso(base), "latitude": 39.9612, "longitude": -82.9988,
            "operator": "Veo", "device_id": "dev-1", "status": "received",
            "priority": "accessibility", "last_updated_at": iso(base)}),
        ("complaint updated", {
            "source_system": "columbus_oneview_SIMULATOR",
            "external_request_id": "TEST-SR-0001", "status": "in_progress",
            "last_updated_at": iso(base + timedelta(minutes=20))}),
        ("missing coordinates", {
            "source_system": "columbus_oneview_SIMULATOR",
            "external_request_id": "TEST-SR-0002",
            "category": "shared_mobility_obstruction",
            "received_at": iso(base), "latitude": None, "longitude": None,
            "operator": "Spin", "status": "received",
            "last_updated_at": iso(base)}),
        ("no identifiable operator", {
            "source_system": "columbus_oneview_SIMULATOR",
            "external_request_id": "TEST-SR-0003",
            "category": "shared_mobility_obstruction",
            "received_at": iso(base), "latitude": 39.97, "longitude": -83.01,
            "operator": "unidentified", "status": "received",
            "last_updated_at": iso(base)}),
        ("request cancelled", {
            "source_system": "columbus_oneview_SIMULATOR",
            "external_request_id": "TEST-SR-0003", "status": "cancelled",
            "last_updated_at": iso(base + timedelta(hours=1))}),
        ("after-hours complaint", {
            "source_system": "columbus_oneview_SIMULATOR",
            "external_request_id": "TEST-SR-0004",
            "category": "shared_mobility_obstruction",
            "received_at": iso(base.replace(hour=23)), "latitude": 39.95,
            "longitude": -82.99, "operator": "Veo", "device_id": "dev-4",
            "status": "received", "last_updated_at": iso(base.replace(hour=23))}),
        ("late update arrives", {
            "source_system": "columbus_oneview_SIMULATOR",
            "external_request_id": "TEST-SR-0004", "status": "reopened",
            "last_updated_at": iso(base + timedelta(days=2))}),
        ("no external id", {
            "source_system": "columbus_oneview_SIMULATOR",
            "category": "shared_mobility_obstruction", "status": "received",
            "last_updated_at": iso(base)}),
    ]


def main():
    print("ONEVIEW 311 ADAPTER — AGAINST A LABELLED SIMULATOR")
    print("=" * 78)
    print("Columbus runs OneView for 311. ChiResearch holds no OneView credential")
    print("and no City-approved interface document, so every record below comes")
    print("from ChiResearch's own simulator. No City system is contacted.\n")

    store = CaseStore()
    expected = {
        "new complaint": "created",
        "same complaint repeated": "duplicate",
        "complaint updated": "updated",
        "missing coordinates": "created",
        "no identifiable operator": "created",
        "request cancelled": "updated",
        "after-hours complaint": "created",
        "late update arrives": "updated",
        "no external id": "rejected",
    }
    passed = failed = 0
    print(f'  {"event":<28}{"expected":<12}{"got":<12}verdict')
    for name, event in simulator_events():
        got = store.ingest(event)
        ok = got == expected[name]
        passed += ok
        failed += (not ok)
        print(f'  {name:<28}{expected[name]:<12}{got:<12}{"PASS" if ok else "FAIL"}')

    # Retrying the whole batch must not create a second case or a second action.
    before = len(store.cases)
    for _, event in simulator_events():
        store.ingest(event)
    after = len(store.cases)
    ok = before == after
    passed += ok
    failed += (not ok)
    print(f'\n  [{"PASS" if ok else "FAIL"}] replaying the whole batch creates no '
          f'new case ({before} before, {after} after)')

    print("\n  CASE STATES")
    print(f'  {"request":<16}{"operator":<14}{"oneview":<14}{"case":<16}')
    for ext, c in sorted(store.cases.items()):
        print(f'  {ext:<16}{str(c["operator"]):<14}{str(c["oneview_status"]):<14}'
              f'{c["case_status"]:<16}')

    print("\n  IP1 RESPONSE CLOCK")
    # Notify on the daytime case inside the window, and on the after-hours one late.
    store.notify("TEST-SR-0001", datetime(2026, 11, 14, 14, 50, tzinfo=timezone.utc))
    store.resolve("TEST-SR-0001", datetime(2026, 11, 14, 15, 40, tzinfo=timezone.utc))
    store.notify("TEST-SR-0004", datetime(2026, 11, 15, 11, 0, tzinfo=timezone.utc))
    for ext in ("TEST-SR-0001", "TEST-SR-0002", "TEST-SR-0003", "TEST-SR-0004"):
        r = assess_response_clock(store.cases[ext])
        print(f'\n   {ext}: {r["determination"]} — {r["reason"]}')
        for k, v in r["elements"].items():
            print(f'       {k:<22}{v}')

    print(f'\n  AUDIT TRAIL — {len(store.audit)} entries, newest five:')
    for a in store.audit[-5:]:
        print(f'    {a["action"]:<22}{str(a["external_request_id"]):<16}{a["detail"]}')

    print("\n" + "=" * 78)
    print(f"  {passed} of {passed + failed} adapter tests passed")
    print("  What remains is the City-approved interface control document and a")
    print("  OneView service account, neither of which ChiResearch can self-issue.")

    json.dump({"sla": SLA, "cases": store.cases, "audit": store.audit},
              open(OUT / "oneview_cases.json", "w"), indent=1)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
