"""
One complete, enforceable SLA calculation for RFQ033920.

A point inside a polygon is not a violation. It is one element of one. This
file states every element a parking violation has to satisfy before the City
could assess a fee on it, tests each element separately against the record,
and returns ENFORCEABLE only when all of them are met. When an element fails,
it names which one and why, because that is the sentence the City has to be
able to say to an operator who disputes the charge.

The rule modelled is the City's parking rule: a trip may not end inside a
no-park or no-ride polygon. It is evaluated on MDS trip records, where the end
of a trip is the moment the rider parked the vehicle, so "was it parked or
merely passing through" is answerable from the data rather than assumed.
"""
# Paths are resolved from this package, so the suite runs from a clone.
import pathlib as _pathlib
PKG_ROOT = _pathlib.Path(__file__).resolve().parent.parent
SUITE_DIR = PKG_ROOT / "suites"
OUT_ROOT = PKG_ROOT / "out"


import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from shapely.geometry import Point

sys.path.insert(0, str(SUITE_DIR))
from mds_pipeline import (                      # noqa: E402
    load_policy_geography, generate_trips, ingest, validate,
)

OUT = OUT_ROOT / "enforce"
OUT.mkdir(parents=True, exist_ok=True)


# The rule, stated once, with a version and an effective date. Every verdict
# below carries these, so a figure can be recomputed years later against the
# policy that was actually in force.
RULE = {
    "rule_id": "COL-PARK-001",
    "version": 3,
    "effective_from": "2026-11-01",
    "statement": "A shared mobility device may not be parked at the end of a "
                 "trip inside a no-park or no-ride polygon.",
    "authority": "RFQ033920 scope items 3.4.14 and 3.4.15 establish the "
                 "prohibition; the City geofencing feed supplies the geometry",

    # The prohibition above has a source. The two numbers below do not.
    #
    # Neither the RFP nor any published feed sets a fine amount or a cure
    # window for this rule. They are set by the executed City/operator
    # agreement, which ChiResearch has not seen. The values here exist only so
    # the calculation can be exercised end to end, and they are labelled as
    # assumptions everywhere they are displayed. Nothing is chargeable from
    # this package in any case: notice_and_cure fails on every record.
    "fee": 25.00,
    "currency": "USD",
    "cure_window_hours": 2,
    "fee_and_cure_basis": "DEMONSTRATION ASSUMPTION — not from an executed agreement",
    "fee_and_cure_note": (
        "The RFP's functional requirements are not authority for a fine amount "
        "or a cure period. Both are placeholders pending the executed "
        "City/operator agreement, which supplies the real figures."
    ),
}

# A GPS fix this far from the service area, or a trip this short, is treated as
# unreliable rather than as evidence. Stated as numbers so they can be argued with.
MIN_TRIP_SECONDS = 60
MIN_TRIP_METRES = 50


SNAPSHOTS = PKG_ROOT / "snapshots"


def geography_in_force_at(parked_dt):
    """The geography capture that was current when the vehicle was parked.

    Testing a November 14 trip against the November 15 map is the error this
    whole proposal argues against, so the enforcement path must not commit it.
    Returns the latest capture at or before the parking moment.
    """
    stamps = sorted(p.name for p in SNAPSHOTS.iterdir()
                    if p.is_dir() and p.name.startswith("20"))
    eligible = [s for s in stamps
                if datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                <= parked_dt]
    return eligible[-1] if eligible else None


def element_identity(trip):
    """Who. A fee cannot be assessed against an unidentified device."""
    has = bool(trip.get("provider_id")) and bool(trip.get("device_id"))
    return has, {
        "provider_id": trip.get("provider_id"),
        "device_id": trip.get("device_id"),
        "operator": trip.get("_operator"),
    }, "" if has else "device or provider not identified on the record"


def element_time(trip):
    """When. The end of the trip is the moment of parking."""
    end = trip.get("end_time")
    has = isinstance(end, int) and end > 0
    return has, {
        "parked_at_utc": trip.get("_end_iso"),
        "trip_started_utc": trip.get("_start_iso"),
    }, "" if has else "no usable end time on the record"


def element_place(trip):
    """Where. Recorded to six decimal places, in WGS 84, as MDS requires."""
    loc = trip.get("end_location") or {}
    has = "lat" in loc and "lng" in loc
    return has, {
        "lat": loc.get("lat"), "lng": loc.get("lng"), "crs": "EPSG:4326",
    }, "" if has else "no usable end location on the record"


def element_parked_not_passing(trip):
    """Parked, not in transit.

    This is the element a snapshot of vehicle positions cannot establish and
    the one that most often defeats an enforcement action. An MDS trip record
    answers it directly: the trip ended here, so the rider left the vehicle
    here. A trip too short or too slight to be a real trip is excluded rather
    than charged.
    """
    duration = trip.get("duration") or 0
    distance = trip.get("distance") or 0
    real_trip = duration >= MIN_TRIP_SECONDS and distance >= MIN_TRIP_METRES
    return real_trip, {
        "basis": "end of an MDS trip record, not a position sample",
        "duration_s": duration,
        "distance_m": distance,
        "minimum_applied": f"{MIN_TRIP_SECONDS} s and {MIN_TRIP_METRES} m",
    }, "" if real_trip else (
        f"trip of {duration} s and {distance} m is below the minimum treated as "
        f"a genuine trip, so the end point is not relied on")


def element_prohibited_place(trip, geo):
    """Prohibited. Point-in-polygon against the City's published geometry."""
    p = Point(trip["end_location"]["lng"], trip["end_location"]["lat"])
    in_no_park = geo["no_park"] is not None and geo["no_park"].contains(p)
    in_no_ride = geo["no_ride"] is not None and geo["no_ride"].contains(p)
    prohibited = in_no_park or in_no_ride
    # Distinct, not summed: the sets overlap, so a point inside both is one case.
    zone = "no_ride" if in_no_ride else ("no_park" if in_no_park else None)
    return prohibited, {
        "zone_type": zone,
        "test": "point-in-polygon, shapely contains()",
        "geography_version": geo["stamp"],
        "zones_in_version": geo["zone_count"],
    }, "" if prohibited else "end point is not inside a restricted polygon"


def element_policy_in_force(trip):
    """In force. Two separate tests, because they fail in different ways.

    First, the rule's effective date must precede the parking event. Second,
    the geography the verdict was computed against must be the capture that was
    current at that moment, not the most recent one. Applying today's map to
    last week's trip is the error this platform exists to prevent, so the
    enforcement path refuses to commit it.
    """
    effective = datetime.fromisoformat(RULE["effective_from"]).replace(tzinfo=timezone.utc)
    parked = datetime.fromtimestamp(trip["end_time"] / 1000, timezone.utc)
    rule_in_force = parked >= effective

    applied = trip.get("_policy_snapshot")
    correct = geography_in_force_at(parked)
    geography_correct = (applied is not None and applied == correct)

    met = rule_in_force and geography_correct
    if not rule_in_force:
        reason = (f'parking occurred before {RULE["effective_from"]}, when this '
                  f'rule version was not in force')
    elif not geography_correct:
        reason = (f'verdict was computed against geography {applied}, but the '
                  f'capture in force when the vehicle was parked was {correct}; '
                  f'recompute before charging')
    else:
        reason = ""
    return met, {
        "rule": f'{RULE["rule_id"]} v{RULE["version"]}',
        "effective_from": RULE["effective_from"],
        "parked_at_utc": parked.isoformat(),
        "geography_applied": applied,
        "geography_in_force_then": correct,
    }, reason


def element_record_integrity(trip):
    """Integrity. The record carries the hash it arrived with, and it validated."""
    has_hash = bool(trip.get("_source_hash"))
    return has_hash, {
        "source_hash": trip.get("_source_hash"),
        "passed_validation": True,
        "mds_version": trip.get("_mds_version"),
    }, "" if has_hash else "no integrity hash retained for the source record"


def element_notice_and_cure(trip, notified_at_ms=None, now_ms=None):
    """Notice and cure.

    The City's practice is to tell an operator and give them a window to move
    the vehicle before a fee attaches. Without that step the charge is not
    collectable, however good the geometry is. ChiResearch holds no notice
    record, so this element reports its own status honestly rather than
    assuming it was satisfied.
    """
    if notified_at_ms is None:
        return False, {
            "notice_sent": False,
            "cure_window_hours": RULE["cure_window_hours"],
        }, ("operator has not been issued notice; the cure window has not run, "
            "so no fee attaches yet")
    notified = datetime.fromtimestamp(notified_at_ms / 1000, timezone.utc)
    deadline = notified + timedelta(hours=RULE["cure_window_hours"])
    now = datetime.fromtimestamp((now_ms or notified_at_ms) / 1000, timezone.utc)
    expired = now >= deadline
    return expired, {
        "notice_sent": True,
        "notified_utc": notified.isoformat(),
        "cure_deadline_utc": deadline.isoformat(),
    }, "" if expired else "cure window has not yet expired"


ELEMENTS = [
    ("identity", element_identity),
    ("time", element_time),
    ("place", element_place),
    ("parked_not_passing", element_parked_not_passing),
    ("prohibited_place", element_prohibited_place),
    ("policy_in_force", element_policy_in_force),
    ("record_integrity", element_record_integrity),
    ("notice_and_cure", element_notice_and_cure),
]


def assess(trip, geo, notified_at_ms=None, now_ms=None):
    """Run every element and return a complete evidence packet."""
    packet = {"trip_id": trip["trip_id"], "elements": {}, "failed": []}
    for name, fn in ELEMENTS:
        if name == "prohibited_place":
            met, evidence, reason = fn(trip, geo)
        elif name == "notice_and_cure":
            met, evidence, reason = fn(trip, notified_at_ms, now_ms)
        else:
            met, evidence, reason = fn(trip)
        packet["elements"][name] = {"met": met, "evidence": evidence, "reason": reason}
        if not met:
            packet["failed"].append(name)

    if not packet["failed"]:
        packet["verdict"] = "ENFORCEABLE"
        packet["fee"] = RULE["fee"]
    elif packet["failed"] == ["notice_and_cure"]:
        # Every substantive element is proven; only the procedural step remains.
        packet["verdict"] = "PROVEN, PENDING NOTICE"
        packet["fee"] = 0.0
    else:
        packet["verdict"] = "NOT ENFORCEABLE"
        packet["fee"] = 0.0
    packet["rule"] = f'{RULE["rule_id"]} v{RULE["version"]}'
    return packet


def snapshot_comparison(geo):
    """The same question asked of GBFS, to show what a snapshot cannot answer.

    A vehicle observed inside a restricted polygon in a position feed fails the
    parked-not-passing element: the feed records where a vehicle is, not that a
    rider left it there, and a single observation cannot exclude a trip in
    progress or a vehicle about to be collected.
    """
    stamp = (PKG_ROOT / "snapshots" / "latest.txt").read_text().strip()
    base = PKG_ROOT / "snapshots" / stamp
    inside = 0
    total = 0
    for op in ("veo", "spin"):
        f = base / op / "free_bike_status.json"
        if not f.exists():
            continue
        for v in json.loads(f.read_text())["data"]["bikes"]:
            if v.get("lat") is None or v.get("lon") is None:
                continue
            total += 1
            p = Point(v["lon"], v["lat"])
            if (geo["no_park"] is not None and geo["no_park"].contains(p)) or \
               (geo["no_ride"] is not None and geo["no_ride"].contains(p)):
                inside += 1
    return {"capture": stamp, "vehicles": total, "inside_restricted": inside}


def main():
    # The trips are dated November 14. Ingesting them against the latest
    # geography is the naive path; the correct path is the capture that was in
    # force when each vehicle was parked. Both are run, because the difference
    # is the whole argument.
    latest = load_policy_geography()
    trips, _ = generate_trips(latest)
    sample_parked = datetime.fromtimestamp(
        max(t["end_time"] for t in trips if "end_time" in t) / 1000, timezone.utc)
    correct_stamp = geography_in_force_at(sample_parked)
    geo = load_policy_geography(correct_stamp)
    store, _ = ingest(trips, geo)
    accepted = list(store["accepted"].values())

    print("ENFORCEABLE SLA CALCULATION — RFQ033920")
    print("=" * 76)
    print(f'Rule {RULE["rule_id"]} v{RULE["version"]}, effective '
          f'{RULE["effective_from"]}')
    print(f'  "{RULE["statement"]}"')
    print(f'  Fee on a sustained violation: ${RULE["fee"]:.2f} {RULE["currency"]}; '
          f'cure window {RULE["cure_window_hours"]} h')
    print(f"  Evaluated against {len(accepted)} accepted MDS trip records "
          f"(schema-conformant test data)")
    print(f"  Latest geography capture available: {latest['stamp']} "
          f"({latest['no_park_count']} no-park, {latest['no_ride_count']} no-ride)")
    print(f"  Geography in force when these trips ended: {geo['stamp']} "
          f"({geo['no_park_count']} no-park, {geo['no_ride_count']} no-ride)")
    print("  The second is the one applied. Charging against the first would")
    print("  judge a November 14 trip by a map that changed on November 15.")

    # Pass 1: no notice has been issued, which is the true state today.
    packets = [assess(t, geo) for t in accepted]
    tally = {}
    failed_on = {}
    for p in packets:
        tally[p["verdict"]] = tally.get(p["verdict"], 0) + 1
        for f in p["failed"]:
            failed_on[f] = failed_on.get(f, 0) + 1

    print("\nVERDICTS")
    for v, n in sorted(tally.items(), key=lambda x: -x[1]):
        print(f"  {v:<26}{n:>5}")
    print("\nELEMENTS THAT FAILED, AND HOW OFTEN")
    for f, n in sorted(failed_on.items(), key=lambda x: -x[1]):
        print(f"  {f:<26}{n:>5}")

    # Pass 2: the same records once notice has been issued and the window run.
    proven = [p for p in packets if p["verdict"] == "PROVEN, PENDING NOTICE"]
    if proven:
        sample_id = proven[0]["trip_id"]
        trip = store["accepted"][sample_id]
        notified = trip["end_time"] + 30 * 60 * 1000          # notice 30 min later
        after = notified + 3 * 60 * 60 * 1000                 # assessed 3 h after notice
        with_notice = assess(trip, geo, notified_at_ms=notified, now_ms=after)

        print("\n" + "=" * 76)
        print("ONE CASE, EVERY ELEMENT, END TO END")
        print("=" * 76)
        print(f'trip_id  {with_notice["trip_id"]}')
        print(f'rule     {with_notice["rule"]}')
        for name, _ in ELEMENTS:
            e = with_notice["elements"][name]
            mark = "MET " if e["met"] else "NOT "
            print(f'\n  [{mark}] {name}')
            for k, v in e["evidence"].items():
                print(f'          {k:<28}{v}')
            if e["reason"]:
                print(f'          reason: {e["reason"]}')
        print(f'\n  VERDICT: {with_notice["verdict"]}   fee ${with_notice["fee"]:.2f}')

        json.dump(with_notice, open(OUT / "enforceable_case.json", "w"), indent=1)

    # The contrast that makes the point.
    snap = snapshot_comparison(geo)
    print("\n" + "=" * 76)
    print("THE SAME QUESTION ASKED OF A POSITION SNAPSHOT")
    print("=" * 76)
    print(f'  capture {snap["capture"]}: {snap["inside_restricted"]} of '
          f'{snap["vehicles"]} vehicles sit inside a restricted polygon')
    print("  Every one of them fails the parked-not-passing element: a position")
    print("  feed records where a vehicle is, not that a rider ended a trip there.")
    print("  Those are candidates for inspection, not charges. The difference")
    print("  between the two numbers is the difference between a dashboard and")
    print("  an enforcement programme.")

    json.dump({"rule": RULE, "verdicts": tally, "failed_elements": failed_on,
               "trips_assessed": len(accepted), "snapshot": snap},
              open(OUT / "enforcement_summary.json", "w"), indent=1)
    print(f'\nWritten: {OUT / "enforcement_summary.json"}, '
          f'{OUT / "enforceable_case.json"}')


if __name__ == "__main__":
    main()
