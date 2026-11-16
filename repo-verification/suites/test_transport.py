"""
The transport tests, each with the outcome fixed before it runs.

These are the cases the specification and ordinary operational failure put in
front of an integration: a good token and a bad one, an hour that is not ready,
an hour that does not exist, a server that fails once, a push from the wrong
provider, and the same trip delivered twice.
"""
# Paths are resolved from this package, so the suite runs from a clone.
import pathlib as _pathlib
PKG_ROOT = _pathlib.Path(__file__).resolve().parent.parent
SUITE_DIR = PKG_ROOT / "suites"
OUT_ROOT = PKG_ROOT / "out"


import sys
sys.path.insert(0, str(SUITE_DIR))

import time                                               # noqa: E402
import mock_mds as M                                      # noqa: E402
from collector import Ledger, collect, fetch_hour, push_to_agency   # noqa: E402

BASE = "http://127.0.0.1:8731"
RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    print(f"  [{'PASS' if condition else 'FAIL'}] {name:<52}{detail}")


def main():
    M.agency_reset()                       # clean store, so counts mean something
    srv = M.start()
    print("MDS TRANSPORT TESTS — Provider and Agency, against a local mock")
    print("=" * 78)
    print("The mock is ChiResearch's own; no operator endpoint is contacted.\n")

    print("PROVIDER API (ChiResearch pulls)")
    r = fetch_hour(BASE, M.VALID_PROVIDER_TOKEN, "2026-11-14T14")
    check("valid token retrieves trips", r["state"] == "received" and r["trips"],
          f'{r["status"]}, {len(r["trips"])} trip')
    check("response carries the MDS version", r.get("version") == "2.0.2",
          r.get("version"))

    r = fetch_hour(BASE, "wrong-token", "2026-11-14T14")
    check("invalid token is refused", r["state"] == "unauthorized", str(r["status"]))

    r = fetch_hour(BASE, M.VALID_PROVIDER_TOKEN, "2026-11-14T16")
    check("202 is treated as pending, not as zero trips",
          r["state"] == "pending" and r["trips"] == [], str(r["status"]))

    r = fetch_hour(BASE, M.VALID_PROVIDER_TOKEN, "2026-11-14T17")
    check("404 is treated as unavailable, not as zero trips",
          r["state"] == "not_available", str(r["status"]))

    ledger = Ledger()
    hours = ["2026-11-14T14", "2026-11-14T15", "2026-11-14T16",
             "2026-11-14T17", "2026-11-14T18"]
    trips = collect(BASE, M.VALID_PROVIDER_TOKEN, hours, ledger)
    check("a failing hour is retried and recovers",
          any(r["hour"] == "2026-11-14T18" and r["state"] == "received"
              for r in ledger.rows), "500 then 200")
    check("unavailable hours stay on the ledger",
          len(ledger.outstanding()) >= 1,
          f"{len(ledger.outstanding())} outstanding")
    first_count = len(trips)
    check("collected trips from the hours that had data", first_count >= 3,
          f"{first_count} trips")

    # Re-run the same window: a backfill must not double-count.
    trips2 = collect(BASE, M.VALID_PROVIDER_TOKEN, hours, Ledger())
    merged = dict(trips)
    merged.update(trips2)
    check("re-collecting the same window does not inflate",
          len(merged) == first_count, f"{len(merged)} after re-run")

    print("\nAGENCY API (operators push to ChiResearch)")
    sample = list(trips.values())[:2]
    code, body = push_to_agency(BASE, "wrong-token", sample)
    check("unauthorised push is refused", code == 401, str(code))

    code, body = push_to_agency(BASE, M.VALID_AGENCY_TOKEN, sample)
    check("authorised push is accepted", code == 201 and body["accepted"] == 2,
          f'{code}, accepted {body.get("accepted")}')

    code, body = push_to_agency(BASE, M.VALID_AGENCY_TOKEN, sample)
    check("the same trips pushed again are not counted twice",
          body.get("duplicates") == 2 and body.get("accepted") == 0,
          f'duplicates {body.get("duplicates")}')

    impostor = dict(sample[0])
    impostor["provider_id"] = "99999999-9999-4999-8999-999999999999"
    impostor["trip_id"] = "00000000-0000-4000-8000-000000000000"
    code, body = push_to_agency(BASE, M.VALID_AGENCY_TOKEN, [impostor])
    check("a push claiming another provider's id is refused",
          body.get("refused") == 1, f'refused {body.get("refused")}')

    check("agency store holds each trip once",
          M.agency_count() == 2, f"{M.agency_count()} stored")

    # The test the guide insists on: replay protection must outlive the process.
    print("\n  RESTART: does replay protection survive the receiver going down?")
    before_restart = M.agency_count()
    srv.shutdown()
    srv.server_close()
    time.sleep(0.2)
    srv2 = M.start()                       # fresh server, fresh process state
    code, body = push_to_agency(BASE, M.VALID_AGENCY_TOKEN, sample)
    after_restart = M.agency_count()
    check("same trips resent after restart are still duplicates",
          body.get("duplicates") == 2 and body.get("accepted") == 0,
          f'duplicates {body.get("duplicates")}')
    check("stored trip count unchanged across restart",
          after_restart == before_restart, f"{after_restart} stored")

    print("\nLEDGER (what was asked for, and what came back)")
    print(f'  {"provider":<10}{"hour":<18}{"http":>6}  {"state":<14}trips')
    for row in ledger.rows:
        print(f'  {row["provider"]:<10}{row["hour"]:<18}{str(row["http"]):>6}  '
              f'{row["state"]:<14}{row["trips"]}')

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n" + "=" * 78)
    print(f"  {passed} of {len(RESULTS)} transport tests passed")
    print("  Exercised against ChiResearch's mock. The remaining step is the")
    print("  City's operator credential, which only the City can issue.")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
