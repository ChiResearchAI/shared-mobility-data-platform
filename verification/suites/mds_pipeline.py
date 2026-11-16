"""
MDS 2.0 trip-data demonstration for City of Columbus RFQ033920.

The trip records this produces are SCHEMA-CONFORMANT TEST DATA, generated
locally. They are not live Veo or Spin trip data: MDS endpoints are credentialed
between an operator and an agency, and ChiResearch holds no such credential.
What the pipeline below demonstrates is the handling, not the possession.

Field names, types and required/optional status follow the MDS Trips data type.
Required: provider_id, device_id, trip_id, start_time, end_time, start_location,
end_location, duration, distance. Locations are GPS objects carrying lat and lng
as decimal degrees in WGS 84.
"""
# Paths are resolved from this package, so the suite runs from a clone.
import pathlib as _pathlib
PKG_ROOT = _pathlib.Path(__file__).resolve().parent.parent
SUITE_DIR = PKG_ROOT / "suites"
OUT_ROOT = PKG_ROOT / "out"


import hashlib
import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shapely.geometry import shape, Point
from shapely.ops import unary_union

SNAPSHOTS = PKG_ROOT / "snapshots"
OUT = OUT_ROOT / "mds"
OUT.mkdir(parents=True, exist_ok=True)

# Stable provider UUIDs for the test fixture. Real MDS provider_ids are issued
# and registered; these are fixtures and are labelled as such everywhere.
PROVIDERS = {
    "Veo":  "11111111-1111-4111-8111-111111111111",
    "Spin": "22222222-2222-4222-8222-222222222222",
}

MDS_VERSION_INGESTED = "2.0.2"   # the version the City's RFP names
MDS_VERSION_CURRENT  = "2.1.0"   # OMF's preferred release since July 2026

# A parking_category of 'invalid' is the operator's own assertion that the trip
# ended badly parked. It is in the spec, and it is a second, independent signal
# against the City's own polygon test.
PARKING_CATEGORIES = ["corral", "curb", "rack", "space", "dock", "other_valid", "invalid"]


def load_policy_geography(stamp=None):
    """The City's policy polygons, taken from the published geofencing feed.

    Pass a capture stamp to load the geography as it stood at that moment. With
    no stamp the latest capture is used, which is right for live reporting and
    wrong for recomputing a past event.
    """
    if stamp is None:
        stamp = (SNAPSHOTS / "latest.txt").read_text().strip()
    raw = json.loads((SNAPSHOTS / stamp / "spin" / "geofencing_zones.json").read_text())
    features = raw["data"]["geofencing_zones"]["features"]

    no_park, no_ride, service = [], [], []
    for f in features:
        geom = shape(f["geometry"])
        rules = (f.get("properties") or {}).get("rules") or []
        service.append(geom)
        for r in rules:
            ride_allowed = r.get("ride_allowed")
            through_allowed = r.get("ride_through_allowed")
            # The City's own encoding: riding barred but passage permitted is a
            # no-park zone; both barred is a no-ride zone.
            if ride_allowed is False and through_allowed is True:
                no_park.append(geom)
            elif ride_allowed is False and through_allowed is False:
                no_ride.append(geom)
    return {
        "service": unary_union(service) if service else None,
        "no_park": unary_union(no_park) if no_park else None,
        "no_ride": unary_union(no_ride) if no_ride else None,
        "stamp": stamp,
        "zone_count": len(features),
        "no_park_count": len(no_park),
        "no_ride_count": len(no_ride),
    }


# ---------------------------------------------------------------- generation

def generate_trips(geo, n=400, seed=20261114):
    """Build schema-conformant MDS Trip records over the City's real geography.

    Deliberately includes records that must be REJECTED, so the validator has
    something to catch: a missing required field, a bad timestamp order, a
    negative distance, a location outside the service area, and an unknown
    vehicle type. A pipeline that only ever sees clean data proves nothing.
    """
    rng = random.Random(seed)
    minx, miny, maxx, maxy = geo["service"].bounds
    base = datetime(2026, 11, 14, 6, 0, tzinfo=timezone.utc)

    # A device pool, so a device can take several trips in the day. Without this
    # the utilisation KPI is one trip per device and says nothing.
    device_pool = {
        "Veo":  [str(uuid.UUID(int=rng.getrandbits(128), version=4)) for _ in range(95)],
        "Spin": [str(uuid.UUID(int=rng.getrandbits(128), version=4)) for _ in range(48)],
    }

    def random_point_in_service():
        for _ in range(500):
            p = Point(rng.uniform(minx, maxx), rng.uniform(miny, maxy))
            if geo["service"].contains(p):
                return p
        return Point((minx + maxx) / 2, (miny + maxy) / 2)

    import math

    def trip_endpoint(start_p):
        """A destination a realistic distance from the origin, inside the service area.

        Scooter trips are short. Drawing both endpoints uniformly across the
        service area would give a mean trip of ten miles, which is not a scooter
        trip and would discredit the fixture. Distance is drawn lognormal with a
        median near 1.2 km and a long tail, then projected along a random bearing.
        """
        for _ in range(60):
            metres = min(rng.lognormvariate(math.log(1200), 0.72), 8000)
            bearing = rng.uniform(0, 2 * math.pi)
            # Degrees per metre at Columbus's latitude (~40N).
            d_lat = (metres * math.cos(bearing)) / 111_320
            d_lng = (metres * math.sin(bearing)) / (111_320 * math.cos(math.radians(39.96)))
            cand = Point(start_p.x + d_lng, start_p.y + d_lat)
            if geo["service"].contains(cand):
                return cand, int(metres)
        return start_p, 0

    trips = []
    for i in range(n):
        operator = "Veo" if rng.random() < 0.69 else "Spin"
        start_p = random_point_in_service()
        end_p, distance = trip_endpoint(start_p)
        start = base + timedelta(minutes=rng.randint(0, 16 * 60))
        # Riding speed around 11-16 km/h, plus a short stop-and-start allowance.
        speed_mps = rng.uniform(3.1, 4.4)
        duration = int(distance / speed_mps) + rng.randint(25, 160)
        end = start + timedelta(seconds=duration)

        trip = {
            "provider_id": PROVIDERS[operator],
            "device_id": rng.choice(device_pool[operator]),
            "trip_id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
            "start_time": int(start.timestamp() * 1000),
            "end_time": int(end.timestamp() * 1000),
            "start_location": {"lat": round(start_p.y, 6), "lng": round(start_p.x, 6)},
            "end_location": {"lat": round(end_p.y, 6), "lng": round(end_p.x, 6)},
            "duration": duration,
            "distance": max(distance, 50),
            "publication_time": int((end + timedelta(minutes=rng.randint(1, 55))).timestamp() * 1000),
            "trip_type": "rider",
            "parking_category": rng.choices(
                PARKING_CATEGORIES, weights=[12, 46, 10, 10, 4, 12, 6], k=1)[0],
            "vehicle_type": rng.choice(["scooter", "bicycle", "moped"]),
        }
        trips.append(trip)

    # Five deliberate defects, one of each kind the validator must catch.
    defects = []
    d = dict(trips[0]); d["trip_id"] = str(uuid.uuid4()); del d["distance"]
    defects.append(("missing required field: distance", d))
    d = dict(trips[1]); d["trip_id"] = str(uuid.uuid4())
    d["end_time"] = d["start_time"] - 60000
    defects.append(("end_time precedes start_time", d))
    d = dict(trips[2]); d["trip_id"] = str(uuid.uuid4()); d["distance"] = -500
    defects.append(("negative distance", d))
    d = dict(trips[3]); d["trip_id"] = str(uuid.uuid4())
    d["end_location"] = {"lat": 0.0, "lng": 0.0}
    defects.append(("end_location outside the permitted service area", d))
    d = dict(trips[4]); d["trip_id"] = str(uuid.uuid4()); d["vehicle_type"] = "hovercraft"
    defects.append(("vehicle_type not in the mode's enumeration", d))
    trips.extend(t for _, t in defects)

    # A fixture has to exercise every classification path, not only the paths
    # random placement happens to hit. No-ride polygons are few and small, so
    # trips that end inside one are placed deliberately.
    if geo["no_ride"] is not None:
        nr_minx, nr_miny, nr_maxx, nr_maxy = geo["no_ride"].bounds
        placed = 0
        attempts = 0
        while placed < 4 and attempts < 4000:
            attempts += 1
            cand = Point(rng.uniform(nr_minx, nr_maxx), rng.uniform(nr_miny, nr_maxy))
            if not geo["no_ride"].contains(cand):
                continue
            donor = dict(trips[20 + placed])
            donor["trip_id"] = str(uuid.UUID(int=rng.getrandbits(128), version=4))
            donor["end_location"] = {"lat": round(cand.y, 6), "lng": round(cand.x, 6)}
            trips.append(donor)
            placed += 1

    # Two exact duplicates, to prove idempotent ingestion later.
    trips.append(dict(trips[10]))
    trips.append(dict(trips[11]))

    rng.shuffle(trips)
    return trips, [label for label, _ in defects]


# ---------------------------------------------------------------- validation

REQUIRED = ["provider_id", "device_id", "trip_id", "start_time", "end_time",
            "start_location", "end_location", "duration", "distance"]

# Outcome classes. A record that fails the schema, a record whose values are
# impossible, and a valid record that simply falls outside what the City asked
# ChiResearch to analyse are three different things. Collapsing them into
# "rejected" loses the distinction the City needs when an operator asks why a
# trip is missing from a count.
SCHEMA_INVALID = "SCHEMA_INVALID"        # the record is not a conformant MDS trip
DOMAIN_INVALID = "DOMAIN_INVALID"        # conformant, but the values cannot be true
OUT_OF_SCOPE = "OUT_OF_SCOPE"            # valid and retained, outside this analysis
ACCEPTED = "ACCEPTED"

# A coordinate this far from the programme is a corrupt fix, not a trip to a
# neighbouring municipality. Columbus sits near 39.96 N, 83.00 W.
PLAUSIBLE_LAT = (38.5, 41.5)
PLAUSIBLE_LNG = (-84.5, -81.5)
VEHICLE_TYPES = {"bicycle", "cargo_bicycle", "car", "scooter", "moped", "other"}


def classify(trip, geo):
    """Return (outcome_class, reasons).

    Separating the classes matters downstream: a schema-invalid record never
    enters the store, while an out-of-scope record is kept and excluded from a
    particular calculation, so the City can still see it and ask about it.
    """
    reasons = validate(trip, geo)
    if not reasons:
        return ACCEPTED, []
    if any(r.startswith(("MISSING_REQUIRED", "BAD_GPS")) for r in reasons):
        return SCHEMA_INVALID, reasons
    if any(r.startswith("OUTSIDE_SERVICE_AREA") for r in reasons):
        # Valid coordinates outside the service polygon are out of scope.
        # Implausible coordinates are a bad fix, which is a domain failure.
        for field in ("start_location", "end_location"):
            loc = trip.get(field) or {}
            lat, lng = loc.get("lat"), loc.get("lng")
            if lat is None or lng is None:
                continue
            if not (PLAUSIBLE_LAT[0] <= lat <= PLAUSIBLE_LAT[1]
                    and PLAUSIBLE_LNG[0] <= lng <= PLAUSIBLE_LNG[1]):
                return DOMAIN_INVALID, reasons + [f"IMPLAUSIBLE_POSITION:{field}"]
        return OUT_OF_SCOPE, reasons
    return DOMAIN_INVALID, reasons


def validate(trip, geo):
    """Return a list of reasons this record cannot be accepted. Empty means valid.

    Every rejection carries a reason code. A record the City cannot see the
    reason for is a record the City cannot argue with an operator about.
    """
    reasons = []
    for field in REQUIRED:
        if field not in trip or trip[field] is None:
            reasons.append(f"MISSING_REQUIRED:{field}")
    if reasons:
        return reasons

    for loc_field in ("start_location", "end_location"):
        loc = trip[loc_field]
        if not isinstance(loc, dict) or "lat" not in loc or "lng" not in loc:
            reasons.append(f"BAD_GPS:{loc_field}")
        elif not (-90 <= loc["lat"] <= 90 and -180 <= loc["lng"] <= 180):
            reasons.append(f"GPS_OUT_OF_RANGE:{loc_field}")

    if not reasons:
        if trip["end_time"] <= trip["start_time"]:
            reasons.append("TIME_ORDER:end_time<=start_time")
        if trip["distance"] < 0:
            reasons.append("NEGATIVE_DISTANCE")
        if trip["duration"] < 0:
            reasons.append("NEGATIVE_DURATION")
        vt = trip.get("vehicle_type")
        if vt is not None and vt not in VEHICLE_TYPES:
            reasons.append(f"UNKNOWN_VEHICLE_TYPE:{vt}")
        if geo["service"] is not None:
            for loc_field in ("start_location", "end_location"):
                p = Point(trip[loc_field]["lng"], trip[loc_field]["lat"])
                if not geo["service"].contains(p):
                    reasons.append(f"OUTSIDE_SERVICE_AREA:{loc_field}")
    return reasons


def record_hash(trip):
    """Stable identity for a record, so the same trip delivered twice is one trip."""
    canonical = json.dumps(trip, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def ingest(trips, geo, store=None):
    """Validate, deduplicate and normalise. Returns the store and a run report.

    `store` is passed back in on a second run to prove that re-ingesting the
    same payload does not inflate anything.
    """
    if store is None:
        store = {"accepted": {}, "rejected": [], "audit": []}

    counts = {"received": 0, "accepted": 0, "rejected": 0, "duplicate": 0}
    reason_tally = {}

    for trip in trips:
        counts["received"] += 1
        reasons = validate(trip, geo)
        if reasons:
            counts["rejected"] += 1
            for r in reasons:
                reason_tally[r] = reason_tally.get(r, 0) + 1
            store["rejected"].append({"trip_id": trip.get("trip_id"), "reasons": reasons})
            continue

        h = record_hash(trip)
        if trip["trip_id"] in store["accepted"]:
            counts["duplicate"] += 1
            store["audit"].append({"trip_id": trip["trip_id"], "action": "duplicate_ignored",
                                   "source_hash": h})
            continue

        normalised = dict(trip)
        normalised["_operator"] = next(
            (name for name, pid in PROVIDERS.items() if pid == trip["provider_id"]), "unknown")
        normalised["_start_iso"] = datetime.fromtimestamp(
            trip["start_time"] / 1000, timezone.utc).isoformat()
        normalised["_end_iso"] = datetime.fromtimestamp(
            trip["end_time"] / 1000, timezone.utc).isoformat()
        normalised["_source_hash"] = h
        normalised["_mds_version"] = MDS_VERSION_INGESTED
        normalised["_policy_snapshot"] = geo["stamp"]

        store["accepted"][trip["trip_id"]] = normalised
        counts["accepted"] += 1
        store["audit"].append({"trip_id": trip["trip_id"], "action": "accepted",
                               "source_hash": h, "policy_snapshot": geo["stamp"]})

    return store, {"counts": counts, "reasons": reason_tally}


# ------------------------------------------------------------ KPI and policy

def compute_kpis(store):
    """Trip counts, durations, distances, provider shares — RFP 3.5.2 and 3.5.3."""
    trips = list(store["accepted"].values())
    total = len(trips)
    by_op = {}
    for t in trips:
        op = t["_operator"]
        b = by_op.setdefault(op, {"trips": 0, "metres": 0, "seconds": 0, "devices": set()})
        b["trips"] += 1
        b["metres"] += t["distance"]
        b["seconds"] += t["duration"]
        b["devices"].add(t["device_id"])

    rows = []
    for op, b in sorted(by_op.items()):
        rows.append({
            "operator": op,
            "trips": b["trips"],
            "share_pct": round(100 * b["trips"] / total, 2) if total else 0,
            "devices_used": len(b["devices"]),
            "trips_per_device": round(b["trips"] / len(b["devices"]), 2) if b["devices"] else 0,
            "mean_duration_s": round(b["seconds"] / b["trips"]) if b["trips"] else 0,
            "mean_distance_m": round(b["metres"] / b["trips"]) if b["trips"] else 0,
            "vehicle_miles": round(b["metres"] / 1609.344, 1),
        })
    return {"total_trips": total, "by_operator": rows}


# Policy rules are versioned and carry an effective date, so a figure can always
# be recomputed against the policy that was in force when the trip happened.
POLICY = {
    "policy_id": "COL-PARK-001",
    "version": 3,
    "effective_from": "2026-11-01",
    "statement": "A trip may not end inside a no-park or no-ride polygon.",
    "source_snapshot": None,   # filled at evaluation time
}


def evaluate_policy(store, geo):
    """Classify every accepted trip's endpoints and return a verdict with evidence."""
    policy = dict(POLICY)
    policy["source_snapshot"] = geo["stamp"]

    results = {"compliant": 0, "ends_in_no_park": 0, "ends_in_no_ride": 0,
               "starts_in_no_ride": 0, "operator_flagged_invalid": 0}
    exceptions = []

    for t in store["accepted"].values():
        end_p = Point(t["end_location"]["lng"], t["end_location"]["lat"])
        start_p = Point(t["start_location"]["lng"], t["start_location"]["lat"])

        in_no_park = geo["no_park"] is not None and geo["no_park"].contains(end_p)
        in_no_ride = geo["no_ride"] is not None and geo["no_ride"].contains(end_p)
        started_no_ride = geo["no_ride"] is not None and geo["no_ride"].contains(start_p)

        if started_no_ride:
            results["starts_in_no_ride"] += 1
        if t.get("parking_category") == "invalid":
            results["operator_flagged_invalid"] += 1

        # Distinct, not summed: the polygon sets overlap, so a trip ending inside
        # both must count once.
        if in_no_park or in_no_ride:
            if in_no_park:
                results["ends_in_no_park"] += 1
            if in_no_ride:
                results["ends_in_no_ride"] += 1
            exceptions.append({
                "trip_id": t["trip_id"],
                "operator": t["_operator"],
                "device_id": t["device_id"],
                "ended_at": t["_end_iso"],
                "lat": t["end_location"]["lat"],
                "lng": t["end_location"]["lng"],
                "zone": "no_ride" if in_no_ride else "no_park",
                "operator_parking_category": t.get("parking_category"),
                "policy": f'{policy["policy_id"]} v{policy["version"]}',
                "source_hash": t["_source_hash"],
                "policy_snapshot": t["_policy_snapshot"],
            })
        else:
            results["compliant"] += 1

    distinct = len(exceptions)
    total = len(store["accepted"])
    return {
        "policy": policy,
        "counts": results,
        "distinct_violations": distinct,
        "violation_rate_pct": round(100 * distinct / total, 2) if total else 0,
        "exceptions": exceptions,
    }


# ----------------------------------------------------------------- exporting

def export_csv(rows, path, columns):
    import csv
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def export_geojson(exceptions, path):
    """Violations as GeoJSON so the City can open the evidence in its own GIS."""
    fc = {"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [e["lng"], e["lat"]]},
        "properties": {k: v for k, v in e.items() if k not in ("lat", "lng")},
    } for e in exceptions]}
    Path(path).write_text(json.dumps(fc, indent=1))
    return path


def main():
    geo = load_policy_geography()
    trips, defect_labels = generate_trips(geo)

    print("MDS 2.0 TRIP-DATA DEMONSTRATION")
    print("=" * 68)
    print("MDS schema-conformant TEST DATA — not live Veo or Spin trip data.")
    print(f"Policy geography: real, from the {geo['stamp']} capture "
          f"({geo['zone_count']} zones: {geo['no_park_count']} no-park, "
          f"{geo['no_ride_count']} no-ride).")
    print(f"Ingested as MDS {MDS_VERSION_INGESTED}; OMF's preferred release is "
          f"{MDS_VERSION_CURRENT} (July 2026).\n")

    store, run1 = ingest(trips, geo)
    print("RUN 1 — first ingestion")
    for k, v in run1["counts"].items():
        print(f"  {k:<12}{v:>6}")
    print("  rejections by reason:")
    for reason, n in sorted(run1["reasons"].items(), key=lambda x: -x[1]):
        print(f"    {n:>3}  {reason}")

    # Idempotency: the same payload again must change nothing.
    before = len(store["accepted"])
    store, run2 = ingest(trips, geo, store)
    after = len(store["accepted"])
    print(f"\nRUN 2 — identical payload re-ingested")
    print(f"  accepted before {before}, after {after} — "
          f"{'NO INFLATION' if before == after else '*** INFLATED ***'}")
    print(f"  duplicates ignored: {run2['counts']['duplicate']}")

    kpis = compute_kpis(store)
    print(f"\nKPIs over {kpis['total_trips']} accepted trips")
    print(f"  {'operator':<8}{'trips':>7}{'share':>8}{'devices':>9}"
          f"{'trips/dev':>11}{'mean s':>8}{'mean m':>8}{'veh-miles':>11}")
    for r in kpis["by_operator"]:
        print(f"  {r['operator']:<8}{r['trips']:>7}{r['share_pct']:>7.1f}%"
              f"{r['devices_used']:>9}{r['trips_per_device']:>11}"
              f"{r['mean_duration_s']:>8}{r['mean_distance_m']:>8}{r['vehicle_miles']:>11}")

    pol = evaluate_policy(store, geo)
    print(f"\nPolicy {pol['policy']['policy_id']} v{pol['policy']['version']} "
          f"(effective {pol['policy']['effective_from']})")
    print(f"  “{pol['policy']['statement']}”")
    for k, v in pol["counts"].items():
        print(f"    {k:<28}{v:>5}")
    print(f"    {'distinct violations':<28}{pol['distinct_violations']:>5}"
          f"  ({pol['violation_rate_pct']}% of trips)")

    # Threshold change, re-run: same data, different authorised rule version.
    strict = dict(POLICY); strict["version"] = 4
    strict["statement"] = ("A trip may not end inside a no-park or no-ride polygon, "
                           "nor start inside a no-ride polygon.")
    strict_count = pol["counts"]["starts_in_no_ride"] + pol["distinct_violations"]
    print(f"\n  Re-run under {strict['policy_id']} v{strict['version']} "
          f"(adds trip origins): {strict_count} violations "
          f"({round(100*strict_count/kpis['total_trips'],2)}%) — same records, "
          f"recomputed against a different authorised policy version.")

    c = export_csv(pol["exceptions"], OUT / "mds_violations.csv",
                   ["trip_id", "operator", "device_id", "ended_at", "zone",
                    "operator_parking_category", "policy", "source_hash", "policy_snapshot"])
    g = export_geojson(pol["exceptions"], OUT / "mds_violations.geojson")
    k = export_csv(kpis["by_operator"], OUT / "mds_kpis.csv",
                   ["operator", "trips", "share_pct", "devices_used", "trips_per_device",
                    "mean_duration_s", "mean_distance_m", "vehicle_miles"])
    a = export_csv(store["audit"], OUT / "mds_audit_log.csv",
                   ["trip_id", "action", "source_hash", "policy_snapshot"])
    print(f"\nExports: {c.name}, {g.name}, {k.name}, {a.name}")

    sample = pol["exceptions"][0] if pol["exceptions"] else None
    if sample:
        print("\nLineage for one flagged trip:")
        for key in ("trip_id", "operator", "device_id", "ended_at", "zone",
                    "operator_parking_category", "policy", "policy_snapshot", "source_hash"):
            print(f"  {key:<28}{sample[key]}")

    json.dump({"run1": run1, "run2": run2, "kpis": kpis,
               "policy": {kk: vv for kk, vv in pol.items() if kk != "exceptions"},
               "exception_count": len(pol["exceptions"])},
              open(OUT / "mds_run_report.json", "w"), indent=1)


if __name__ == "__main__":
    main()
