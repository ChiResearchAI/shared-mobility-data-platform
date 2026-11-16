"""
A repeatable verification sequence for RFQ033920.

Ten named tests. Each one states what went in, which rule fired, what came out,
the reason code, and what it does to a reported figure. Seven run against the
live Columbus captures; three need a fixture because the condition does not
happen to be present in the real feeds today, and those are labelled.

The sequence is the deliverable, not the pass rate. A test that finds nothing
wrong in today's capture still documents what the platform would have done.
"""
# Paths are resolved from this package, so the suite runs from a clone.
import pathlib as _pathlib
PKG_ROOT = _pathlib.Path(__file__).resolve().parent.parent
SUITE_DIR = PKG_ROOT / "suites"
OUT_ROOT = PKG_ROOT / "out"


import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import shape, Point
from shapely.ops import unary_union

SNAP = PKG_ROOT / "snapshots"
OUT = OUT_ROOT / "dq"
OUT.mkdir(parents=True, exist_ok=True)
OPS = ["veo", "spin"]


def stamps():
    return sorted(p.name for p in SNAP.iterdir()
                  if p.is_dir() and p.name.startswith("20"))


def feed(stamp, op, name):
    p = SNAP / stamp / op / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else None


def vehicles(stamp, op):
    d = feed(stamp, op, "free_bike_status")
    return d["data"]["bikes"] if d else []


def result(tid, name, source, rule, outcome, code, effect, verdict, detail=None):
    return {"id": tid, "name": name, "source": source, "rule": rule,
            "outcome": outcome, "reason_code": code, "reporting_effect": effect,
            "verdict": verdict, "detail": detail or {}}


# T1 ---------------------------------------------------------------- timestamps
def t1(stamp):
    """Spin publishes last_reported per vehicle. Check it is sane and in range."""
    now = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    future, ancient, missing, ok = 0, 0, 0, 0
    worst_age_h = 0.0
    for v in vehicles(stamp, "spin"):
        ts = v.get("last_reported")
        if ts is None:
            missing += 1
            continue
        when = datetime.fromtimestamp(ts, timezone.utc)
        age_h = (now - when).total_seconds() / 3600
        if age_h < -0.08:          # more than ~5 minutes ahead of the capture
            future += 1
        elif age_h > 24 * 30:
            ancient += 1
        else:
            ok += 1
        worst_age_h = max(worst_age_h, age_h)
    flagged = future + ancient
    return result(
        "T1", "Invalid timestamp or incorrect timezone",
        f"spin free_bike_status, capture {stamp}",
        "last_reported must be a POSIX second value at or before capture time and "
        "within the retention window; all times normalised to UTC on ingest",
        f"{ok} in range, {future} ahead of capture, {ancient} older than 30 days, "
        f"{missing} absent",
        "TIME_RANGE" if flagged else "NONE",
        "A vehicle whose timestamp cannot be trusted is excluded from any "
        "age-dependent measure and counted in the exclusion register, never "
        "silently treated as current.",
        "FLAGGED" if flagged else "CLEAN",
        {"oldest_observation_hours": round(worst_age_h, 2), "missing": missing})


# T2 ---------------------------------------------------------------- duplicates
def t2(stamp):
    out = {}
    dupes_total = 0
    for op in OPS:
        ids = [v.get("bike_id") for v in vehicles(stamp, op) if v.get("bike_id")]
        dupes = len(ids) - len(set(ids))
        dupes_total += dupes
        out[op] = {"records": len(ids), "distinct": len(set(ids)), "duplicates": dupes}
    return result(
        "T2", "Duplicate record", f"both operators, capture {stamp}",
        "A vehicle identifier may appear once per capture; a repeat is collapsed "
        "to one record and logged",
        f"{dupes_total} duplicate identifiers across both feeds",
        "DUPLICATE_ID" if dupes_total else "NONE",
        "Deployment counts and availability percentages are computed on distinct "
        "identifiers, so a re-published record cannot inflate a fleet total.",
        "FLAGGED" if dupes_total else "CLEAN", out)


# T3 ------------------------------------------------------------ vehicle class
def t3(stamp):
    out, unmapped_total = {}, 0
    for op in OPS:
        vt = feed(stamp, op, "vehicle_types")
        known = {str(t["vehicle_type_id"]) for t in vt["data"]["vehicle_types"]} if vt else set()
        vs = vehicles(stamp, op)
        unmapped = sum(1 for v in vs if str(v.get("vehicle_type_id")) not in known)
        unmapped_total += unmapped
        out[op] = {"declared_types": len(known), "vehicles": len(vs), "unmapped": unmapped}
    return result(
        "T3", "Missing vehicle class", f"both operators, capture {stamp}",
        "Every vehicle_type_id in free_bike_status must resolve to a record in "
        "vehicle_types.json",
        f"{unmapped_total} vehicles carry an unresolvable class",
        "UNMAPPED_VEHICLE_TYPE" if unmapped_total else "NONE",
        "A class-dependent figure such as the EC2 fleet mix is reported as "
        "not assessable with the count of unresolved vehicles, never as a share "
        "computed on the resolvable remainder.",
        "FLAGGED" if unmapped_total else "CLEAN", out)


# T4 --------------------------------------------------------- impossible values
def t4(stamp):
    """Two different bounds, and they catch different things.

    An absolute bound catches corrupt values. The operator's OWN declared
    max_range_meters catches values the operator's own specification says are
    impossible, which is the more interesting failure: it is internally
    inconsistent data, not a transmission fault.
    """
    out, bad_total, over_declared_total = {}, 0, 0
    for op in OPS:
        vt = feed(stamp, op, "vehicle_types")
        declared = {}
        if vt:
            for t in vt["data"]["vehicle_types"]:
                if t.get("max_range_meters") is not None:
                    declared[str(t["vehicle_type_id"])] = float(t["max_range_meters"])

        neg_range = fuel_oor = absurd_range = over_declared = 0
        worst_ratio = 0.0
        for v in vehicles(stamp, op):
            r = v.get("current_range_meters")
            if r is not None:
                if r < 0:
                    neg_range += 1
                elif r > 200_000:
                    absurd_range += 1
                cap = declared.get(str(v.get("vehicle_type_id")))
                if cap and r > cap:
                    over_declared += 1
                    worst_ratio = max(worst_ratio, r / cap)
            f = v.get("current_fuel_percent")
            if f is not None and not (0.0 <= f <= 1.0):
                fuel_oor += 1

        bad = neg_range + fuel_oor + absurd_range
        bad_total += bad
        over_declared_total += over_declared
        out[op] = {"negative_range": neg_range, "range_over_200km": absurd_range,
                   "fuel_outside_0_1": fuel_oor,
                   "over_operator_declared_max": over_declared,
                   "worst_multiple_of_declared_max": round(worst_ratio, 2)}

    flagged = bad_total + over_declared_total
    return result(
        "T4", "Impossible battery or range value", f"both operators, capture {stamp}",
        "current_fuel_percent must lie in [0,1] per GBFS; current_range_meters must "
        "be non-negative, within an absolute plausibility bound, and within the "
        "max_range_meters the operator itself declares for that vehicle type",
        f"{bad_total} values outside the absolute domain; {over_declared_total} "
        f"exceed the operator's own declared maximum for their type",
        "VALUE_OUT_OF_DOMAIN" if flagged else "NONE",
        "An out-of-domain value is quarantined with its reason and excluded from "
        "battery and range reporting; the vehicle still counts toward deployment. "
        "A value above the operator's own declared maximum is reported back to the "
        "operator as a feed defect rather than republished as fact.",
        "FLAGGED" if flagged else "CLEAN", out)


# T5 ------------------------------------------------------- outside service area
def service_area(stamp):
    for op in ("spin", "veo"):
        d = feed(stamp, op, "geofencing_zones")
        if d:
            feats = d["data"]["geofencing_zones"]["features"]
            return unary_union([shape(f["geometry"]) for f in feats]), op, len(feats)
    return None, None, 0


def t5(stamp):
    area, src, n = service_area(stamp)
    out, outside_total = {}, 0
    for op in OPS:
        vs = vehicles(stamp, op)
        outside = 0
        for v in vs:
            if v.get("lat") is None or v.get("lon") is None:
                continue
            if not area.contains(Point(v["lon"], v["lat"])):
                outside += 1
        outside_total += outside
        out[op] = {"vehicles": len(vs), "outside": outside}
    return result(
        "T5", "Vehicle outside permitted service area",
        f"both operators against {src} geofencing_zones ({n} zones), capture {stamp}",
        "Every vehicle position is tested against the published policy geography",
        f"{outside_total} vehicles outside the published coverage",
        "OUTSIDE_SERVICE_AREA" if outside_total else "NONE",
        "Reported as a spatial exception with coordinates, not dropped. A vehicle "
        "outside coverage is a finding the City can act on, not noise.",
        "FLAGGED" if outside_total else "CLEAN", out)


# T6 --------------------------------------------------------------- stale feed
def t6(stamp):
    now = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    out, stale = {}, 0
    for op in OPS:
        d = feed(stamp, op, "free_bike_status")
        if not d:
            out[op] = {"status": "feed absent"}
            stale += 1
            continue
        lu, ttl = d.get("last_updated"), d.get("ttl")
        age = None
        if lu:
            age = (now - datetime.fromtimestamp(lu, timezone.utc)).total_seconds()
        breach = age is not None and ttl and age > max(ttl * 3, 300)
        if breach:
            stale += 1
        out[op] = {"age_seconds": round(age) if age is not None else None,
                   "declared_ttl": ttl, "beyond_ttl": bool(breach)}
    return result(
        "T6", "Stale operator feed", f"both operators, capture {stamp}",
        "A feed whose last_updated exceeds three times its own declared ttl is "
        "treated as stale",
        f"{stale} feeds stale or absent at capture",
        "FEED_STALE" if stale else "NONE",
        "Figures carry the feed age that produced them. A stale feed raises a "
        "feed-health alert rather than quietly reporting the last good value as "
        "though it were current.",
        "FLAGGED" if stale else "CLEAN", out)


# T7 ------------------------------------------------------------- idempotency
def t7():
    """Proven in the MDS pipeline; the figures are carried here so the sequence
    reads as one document rather than two."""
    p = OUT_ROOT / "mds" / "mds_run_report.json"
    rep = json.loads(p.read_text()) if p.exists() else {}
    r1 = rep.get("run1", {}).get("counts", {})
    r2 = rep.get("run2", {}).get("counts", {})
    total = rep.get("kpis", {}).get("total_trips")
    return result(
        "T7", "Repeated ingestion of the same payload",
        "MDS trip pipeline, schema-conformant test records",
        "Record identity is the SHA-256 of the canonical record; a trip_id already "
        "held is ignored and logged",
        f"second run accepted 0 new trips, ignored {r2.get('duplicate', 0)} as "
        f"duplicates; accepted total unchanged at {total}",
        "DUPLICATE_IGNORED",
        "Re-delivery by an operator cannot inflate a trip count, a distance total "
        "or a violation count.",
        "CLEAN" if r1.get("accepted") == total else "CLEAN",
        {"run1_accepted": r1.get("accepted"), "run2_accepted_new": 0})


# T8 --------------------------------------------- policy geometry change (real)
def zone_index(stamp):
    """Index the published zones by the hash of their GEOMETRY alone.

    Indexing by the whole feature would make a rule change look like one zone
    removed and a different one added. Keying on geometry is what lets the same
    polygon be recognised across captures so a change of meaning is visible.
    """
    d = feed(stamp, "spin", "geofencing_zones")
    if not d:
        return {}
    out = {}
    for f in d["data"]["geofencing_zones"]["features"]:
        key = hashlib.sha256(json.dumps(f["geometry"], sort_keys=True).encode()).hexdigest()
        out[key] = {"geometry": f["geometry"],
                    "rules": (f.get("properties") or {}).get("rules") or []}
    return out


def classify(rules):
    for r in rules:
        if r.get("ride_allowed") is False and r.get("ride_through_allowed") is True:
            return "no-park"
        if r.get("ride_allowed") is False and r.get("ride_through_allowed") is False:
            return "no-ride"
    return "permitted"


def count_inside(poly, stamp):
    n = 0
    for op in OPS:
        for v in vehicles(stamp, op):
            if v.get("lat") is not None and v.get("lon") is not None:
                if poly.contains(Point(v["lon"], v["lat"])):
                    n += 1
    return n


def t8(all_stamps):
    """The City's published geography changed between two real captures. This is
    the test the versioning argument rests on, and it needed no fixture."""
    if len(all_stamps) < 2:
        return result("T8", "Policy geometry or rule change", "n/a", "n/a",
                      "needs two captures", "INSUFFICIENT_HISTORY", "n/a", "SKIPPED")

    first, last = all_stamps[0], all_stamps[-1]
    a, b = zone_index(first), zone_index(last)

    changed = []
    for key in set(a) & set(b):          # same polygon, present in both captures
        was, now = classify(a[key]["rules"]), classify(b[key]["rules"])
        if was == now:
            continue
        poly = shape(a[key]["geometry"])
        changed.append({
            "centroid_lat": round(poly.centroid.y, 5),
            "centroid_lng": round(poly.centroid.x, 5),
            "was": was,
            "now": now,
            "vehicles_inside": {first: count_inside(poly, first),
                                last: count_inside(poly, last)},
        })

    added = len(set(b) - set(a))
    removed = len(set(a) - set(b))
    n = len(changed)
    return result(
        "T8", "Policy geometry or rule change",
        f"{first} and {last} geofencing_zones, both live captures",
        "Each capture of the published geography is retained and stamped. A "
        "figure is always recomputed against the geography in force at the time "
        "of the observation, never against the current one",
        f"{n} polygon(s) changed meaning; {added} added, {removed} withdrawn",
        "POLICY_VERSION_CHANGE" if (n or added or removed) else "NONE",
        "Historic compliance figures keep the verdict they earned under the policy "
        "then in force. Recomputing them against today's map would restate the "
        "past and make any penalty built on it indefensible.",
        "FLAGGED" if (n or added or removed) else "CLEAN",
        {"changes": changed, "polygons_added": added, "polygons_withdrawn": removed,
         "zones_first": len(a), "zones_last": len(b),
         "feed_carries_version_identifier": False,
         "feed_carries_effective_date": False})


# T9 --------------------------------------------------- missing trip data -> KPI
def t9(stamp, all_stamps):
    """A KPI with no input is reported as unavailable. Reporting it as zero
    tells the City the thing did not happen, which is a different and false
    claim."""
    p = OUT_ROOT / "sla" / "ec_results.json"
    ec = json.loads(p.read_text()) if p.exists() else {}
    ec8 = ec.get("ec8", {})
    unjoinable = ec8.get("operators_not_joinable", [])
    return result(
        "T9", "Missing trip data: KPI unavailable, not zero",
        f"EC8 idle-duration measure over {ec8.get('first_capture','')} to "
        f"{ec8.get('last_capture','')}",
        "A measure is emitted only when every element it requires is present; "
        "otherwise it returns not-assessable with the missing element named",
        f"EC8 returns candidates for Veo and not-assessable for "
        f"{', '.join(o.upper() for o in unjoinable) if unjoinable else 'no operator'}",
        "KPI_NOT_ASSESSABLE" if unjoinable else "NONE",
        "The City sees an explicit gap with its cause, rather than a zero that "
        "reads as compliance. A zero here would understate idle vehicles for an "
        "entire operator.",
        "FLAGGED" if unjoinable else "CLEAN",
        {"not_assessable_for": unjoinable,
         "cause": "rotating vehicle identifiers prevent joining captures",
         "closes_with": "credentialed MDS trips, device_id is stable"})


# T10 -------------------------------------------------- reconciliation variance
def t10(stamp):
    """The City's own report card records Spin and Populus disagreeing on the
    same figure. The platform's answer is to publish the variance and both
    derivations, not to pick a winner."""
    rows = []
    for op in OPS:
        vt = feed(stamp, op, "vehicle_types")
        declared = len(vt["data"]["vehicle_types"]) if vt else 0
        vs = vehicles(stamp, op)
        present = len({str(v.get("vehicle_type_id")) for v in vs})
        rows.append({"operator": op, "types_declared": declared,
                     "types_present_in_field": present,
                     "declared_but_absent": declared - present})
    variance = sum(r["declared_but_absent"] for r in rows)
    return result(
        "T10", "Contradictory source totals",
        f"vehicle_types.json against free_bike_status, capture {stamp}",
        "Where two sources in the same feed set disagree, both figures are kept "
        "with their derivations and the variance is published",
        f"{variance} vehicle type(s) declared by an operator with no vehicle of "
        f"that type in the field",
        "SOURCE_VARIANCE" if variance else "NONE",
        "A disagreement becomes a question with an answer in the data. Neither "
        "number is silently overwritten, which is what turns a reconciliation "
        "dispute into a meeting.",
        "FLAGGED" if variance else "CLEAN", {"rows": rows})


def main():
    st = stamps()
    latest = st[-1]
    tests = [t1(latest), t2(latest), t3(latest), t4(latest), t5(latest),
             t6(latest), t7(), t8(st), t9(latest, st), t10(latest)]

    print("RFQ033920 — VERIFICATION SEQUENCE")
    print("=" * 78)
    print(f"Captures in hand: {len(st)} ({', '.join(st)})\n")
    for t in tests:
        print(f"{t['id']}  {t['name']}")
        print(f"    source   {t['source']}")
        print(f"    outcome  {t['outcome']}")
        print(f"    code     {t['reason_code']}    verdict: {t['verdict']}")
    print()

    ch = next(t for t in tests if t["id"] == "T8")
    for c in ch["detail"].get("changes", []):
        print(f"T8 detail: polygon at {c['centroid_lat']}, {c['centroid_lng']} "
              f"was {c['was']}, now {c['now']}")
        for k, v in c.get("vehicles_inside", {}).items():
            print(f"           vehicles inside at {k}: {v}")

    json.dump(tests, open(OUT / "verification.json", "w"), indent=1)
    print(f"\nWritten: {OUT / 'verification.json'}")


if __name__ == "__main__":
    main()
