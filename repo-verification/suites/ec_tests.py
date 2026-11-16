"""
Executed tests for Columbus SLA codes EC2, EC3 and EC8.

The point of this harness is not to produce three green ticks. It is to show
which of the City's own service-level measures can actually be computed from
what the operators publish today, and to make the platform refuse to report a
number it cannot support. A measure that returns "not assessable, and here is
the reason" is worth more to the City than a measure that returns a figure
nobody can defend when an operator disputes it.
"""
# Paths are resolved from this package, so the suite runs from a clone.
import pathlib as _pathlib
PKG_ROOT = _pathlib.Path(__file__).resolve().parent.parent
SUITE_DIR = PKG_ROOT / "suites"
OUT_ROOT = PKG_ROOT / "out"


import json
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import shape, Point
from shapely.ops import unary_union
from shapely.strtree import STRtree

SNAP = PKG_ROOT / "snapshots"
OUT = OUT_ROOT / "sla"
OUT.mkdir(parents=True, exist_ok=True)

OPERATORS = ["veo", "spin"]


def captures():
    stamps = sorted(p.name for p in SNAP.iterdir()
                    if p.is_dir() and p.name.startswith("20"))
    return stamps


def parse_stamp(s):
    return datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def load_vehicles(stamp, op):
    p = SNAP / stamp / op / "free_bike_status.json"
    if not p.exists():
        return []
    return json.loads(p.read_text())["data"]["bikes"]


def load_types(stamp, op):
    p = SNAP / stamp / op / "vehicle_types.json"
    if not p.exists():
        return {}
    types = json.loads(p.read_text())["data"]["vehicle_types"]
    return {str(t["vehicle_type_id"]): t for t in types}


# --------------------------------------------------------------- EC2 fleet mix
# GBFS v2.3 added scooter_standing and scooter_seated to the form_factor
# enumeration precisely so an agency could tell them apart. If an operator
# publishes neither, the distinction does not exist in the data, and a fleet-mix
# figure that claims to make it is invented.
SEATED_AWARE = {"scooter_standing", "scooter_seated"}


def test_ec2(stamp):
    findings = {}
    assessable = True
    reasons = []

    for op in OPERATORS:
        types = load_types(stamp, op)
        vehicles = load_vehicles(stamp, op)
        if not types or not vehicles:
            reasons.append(f"{op}: no vehicle_types or free_bike_status in capture")
            assessable = False
            continue

        counts = {}
        for v in vehicles:
            tid = str(v.get("vehicle_type_id"))
            ff = types.get(tid, {}).get("form_factor", "unmapped")
            counts[ff] = counts.get(ff, 0) + 1

        forms_used = {t.get("form_factor") for t in types.values()}
        distinguishes = bool(forms_used & SEATED_AWARE)
        if not distinguishes:
            assessable = False
            reasons.append(
                f"{op}: publishes form_factor values {sorted(forms_used)}; neither "
                f"scooter_standing nor scooter_seated is used, so seated and "
                f"stand-up scooters are not distinguishable")

        # Type ids that differ in no published attribute other than the id.
        signature = {}
        for tid, t in types.items():
            key = (t.get("form_factor"), t.get("propulsion_type"),
                   t.get("max_range_meters"))
            signature.setdefault(key, []).append(tid)
        ambiguous = {str(k): v for k, v in signature.items() if len(v) > 1}

        unmapped = counts.get("unmapped", 0)
        if unmapped:
            assessable = False
            reasons.append(f"{op}: {unmapped} vehicles carry a vehicle_type_id "
                           f"absent from vehicle_types.json")

        findings[op] = {
            "by_form_factor": counts,
            "form_factors_published": sorted(forms_used),
            "distinguishes_seated_vs_standing": distinguishes,
            "indistinguishable_type_ids": ambiguous,
            "types_with_no_vehicles_in_field": sorted(
                tid for tid in types
                if not any(str(v.get("vehicle_type_id")) == tid for v in vehicles)),
            "unmapped_vehicles": unmapped,
        }

    return {
        "code": "EC2",
        "requirement": "Fleet size and vehicle mix",
        "verdict": "ASSESSABLE" if assessable else "NOT ASSESSABLE",
        "reasons": reasons,
        "findings": findings,
        "capture": stamp,
    }


# ------------------------------------------------ EC3 distribution across zones
def load_zones(stamp):
    """Policy polygons from the City's published geofencing feed."""
    for op in ("spin", "veo"):
        p = SNAP / stamp / op / "geofencing_zones.json"
        if p.exists():
            feats = json.loads(p.read_text())["data"]["geofencing_zones"]["features"]
            return feats, op
    return [], None


def test_ec3(stamp):
    feats, src = load_zones(stamp)
    if not feats:
        return {"code": "EC3", "verdict": "NOT ASSESSABLE",
                "reasons": ["no geofencing_zones in capture"], "capture": stamp}

    polys = [shape(f["geometry"]) for f in feats]
    tree = STRtree(polys)
    coverage = unary_union(polys)

    rows = []
    for op in OPERATORS:
        vehicles = load_vehicles(stamp, op)
        if not vehicles:
            continue
        inside = 0
        for v in vehicles:
            if v.get("lat") is None or v.get("lon") is None:
                continue
            p = Point(v["lon"], v["lat"])
            # STRtree narrows the candidate polygons; contains() decides.
            if any(polys[i].contains(p) for i in tree.query(p)):
                inside += 1
        total = len(vehicles)
        rows.append({
            "operator": op,
            "vehicles_observed": total,
            "inside_policy_geography": inside,
            "share_pct": round(100 * inside / total, 2) if total else 0,
        })

    return {
        "code": "EC3",
        "requirement": "Distribution and rebalancing across policy geography",
        "verdict": "ASSESSABLE",
        # Every element the plan's pass condition demands, stated on the result.
        "geography_version": stamp,
        "geography_source_feed": f"{src} geofencing_zones.json",
        "zone_count": len(feats),
        "observation_period": f"single capture at {parse_stamp(stamp).isoformat()}",
        "denominator": "vehicles present in the operator's free_bike_status feed at capture",
        "rows": rows,
        "caveat": ("A single capture measures distribution at one instant. An SLA "
                   "measured over a period needs repeated captures across that "
                   "period; the denominator above states which was used."),
        "equity_geography_note": ("Opportunity-zone or equity-area boundaries are a "
                                  "City layer and are not published in the operator "
                                  "feeds. The test runs against the City's own "
                                  "geofencing polygons until that layer is supplied."),
        "capture": stamp,
    }


# --------------------------------------------------- EC8 parking time limit
# The hard part of EC8 is evidentiary, not arithmetic. Two observations of a
# vehicle in the same place do not establish that it sat there continuously:
# it may have been ridden away and returned. Snapshot data can therefore
# produce a CANDIDATE, never a proven violation. Saying so is the difference
# between a report an operator can dispute and one it cannot.
MOVE_TOLERANCE_M = 15.0


def metres_between(lat1, lon1, lat2, lon2):
    """Equirectangular approximation. At these distances the error is centimetres."""
    import math
    mean_lat = math.radians((lat1 + lat2) / 2)
    dx = math.radians(lon2 - lon1) * math.cos(mean_lat) * 6_371_000
    dy = math.radians(lat2 - lat1) * 6_371_000
    return math.hypot(dx, dy)


def identifier_stability(first, last, op):
    """Can this operator's vehicles be followed from one capture to the next?

    GBFS v2.x recommends rotating bike_id when a trip ends, to stop a rider
    being followed between trips. The privacy goal is sound, and the cost falls
    on the agency: a rotated identifier cannot be joined across captures, so no
    dwell time, idle time or rebalancing measure can be computed from GBFS for
    that operator. MDS device_id is stable by design and is where that join has
    to happen. Reporting this as "zero vehicles matched" would read as a broken
    pipeline; it is a property of the feed, and the City should see it as one.
    """
    a = {v["bike_id"] for v in load_vehicles(first, op) if v.get("bike_id")}
    b = {v["bike_id"] for v in load_vehicles(last, op) if v.get("bike_id")}
    if not a or not b:
        return {"operator": op, "verdict": "UNKNOWN", "overlap": 0, "overlap_pct": 0.0}
    overlap = len(a & b)
    pct = 100 * overlap / min(len(a), len(b))

    def uuid_version(sample):
        # Position 15 of a canonical UUID carries its version digit.
        for x in sample:
            if len(x) == 36 and x[14].isdigit():
                return int(x[14])
        return None

    ver = uuid_version(list(b)[:50])
    if overlap == 0:
        verdict = "ROTATING \u2014 NOT JOINABLE ACROSS CAPTURES"
    elif pct < 25:
        verdict = "MOSTLY ROTATING"
    else:
        verdict = "STABLE \u2014 JOINABLE"
    return {
        "operator": op,
        "verdict": verdict,
        "overlap": overlap,
        "overlap_pct": round(pct, 2),
        "identifier_form": f"UUID v{ver}" if ver else "non-UUID",
        "consequence": ("Dwell time, idle time and rebalancing cannot be computed "
                        "from GBFS for this operator; MDS device_id is required."
                        if overlap == 0 else
                        "Vehicles can be followed between captures using GBFS alone."),
    }


def test_ec8(stamps, threshold_hours=24.0):
    if len(stamps) < 2:
        return {"code": "EC8", "verdict": "NOT ASSESSABLE",
                "reasons": ["a parking duration needs at least two captures; "
                            "one capture is in hand"],
                "captures_available": len(stamps)}

    first, last = stamps[0], stamps[-1]
    elapsed_h = (parse_stamp(last) - parse_stamp(first)).total_seconds() / 3600.0

    results = []
    stability = [identifier_stability(first, last, op) for op in OPERATORS]
    for op in OPERATORS:
        a = {v["bike_id"]: v for v in load_vehicles(first, op) if v.get("bike_id")}
        b = {v["bike_id"]: v for v in load_vehicles(last, op) if v.get("bike_id")}
        both = set(a) & set(b)

        stationary, moved, bad_coords = 0, 0, 0
        candidates = []
        for vid in both:
            va, vb = a[vid], b[vid]
            if None in (va.get("lat"), va.get("lon"), vb.get("lat"), vb.get("lon")):
                bad_coords += 1
                continue
            d = metres_between(va["lat"], va["lon"], vb["lat"], vb["lon"])
            if d <= MOVE_TOLERANCE_M:
                stationary += 1
                if len(candidates) < 8:
                    candidates.append({
                        "vehicle": vid[:12],
                        "lat": round(vb["lat"], 5),
                        "lng": round(vb["lon"], 5),
                        "displacement_m": round(d, 1),
                        "observed_gap_h": round(elapsed_h, 1),
                        "disabled": bool(vb.get("is_disabled")),
                    })
            else:
                moved += 1

        results.append({
            "operator": op,
            "in_first_capture": len(a),
            "in_last_capture": len(b),
            "present_in_both": len(both),
            "appeared": len(set(b) - set(a)),
            "disappeared": len(set(a) - set(b)),
            "unmoved_within_tolerance": stationary,
            "moved": moved,
            "unusable_coordinates": bad_coords,
            "candidates": candidates,
            "identifier": next(x for x in stability if x["operator"] == op),
        })

    exceeds = elapsed_h >= threshold_hours
    unjoinable = [x["operator"] for x in stability if x["overlap"] == 0]
    return {
        "code": "EC8",
        "requirement": "Parking time limit",
        # The honest verdict: the computation runs, but the evidence class is
        # candidate, not violation.
        "verdict": ("ASSESSABLE AS CANDIDATES ONLY"
                    if not unjoinable else
                    "PARTIAL \u2014 CANDIDATES FOR SOME OPERATORS, NOT ASSESSABLE FOR "
                    + ", ".join(o.upper() for o in unjoinable)),
        "operators_not_joinable": unjoinable,
        "threshold_hours": threshold_hours,
        "first_capture": first,
        "last_capture": last,
        "elapsed_hours": round(elapsed_h, 2),
        "window_covers_threshold": exceeds,
        "move_tolerance_m": MOVE_TOLERANCE_M,
        "rows": results,
        "identifier_stability": stability,
        "why_not_a_violation": (
            "Two observations establish position at two instants, not continuous "
            "occupancy. A vehicle rented and returned to the same rack between "
            "captures is indistinguishable from one that never moved. Proving the "
            "element requires either a capture interval shorter than the threshold "
            "or MDS trip records showing no trip began on that device in the window."),
        "what_closes_it": (
            "MDS Trips for the device over the same window. That is the direct "
            "operational reason the City needs credentialed MDS access, and it is "
            "the join this platform is built to make."),
    }


def main():
    stamps = captures()
    ec2 = test_ec2(stamps[-1])
    ec3 = test_ec3(stamps[-1])
    ec8 = test_ec8(stamps)

    print("COLUMBUS SLA CODE TESTS — EXECUTED")
    print("=" * 70)
    print(f"Captures in hand: {len(stamps)} — {', '.join(stamps)}\n")

    print(f"EC2 — {ec2['requirement']}: {ec2['verdict']}")
    for r in ec2["reasons"]:
        print(f"    reason: {r}")
    for op, f in ec2["findings"].items():
        print(f"    {op}: {f['by_form_factor']}")
        if f["indistinguishable_type_ids"]:
            print(f"      type ids identical in every published attribute: "
                  f"{list(f['indistinguishable_type_ids'].values())}")
        if f["types_with_no_vehicles_in_field"]:
            print(f"      declared types with no vehicles in the field: "
                  f"{f['types_with_no_vehicles_in_field']}")

    print(f"\nEC3 — {ec3['requirement']}: {ec3['verdict']}")
    print(f"    geography {ec3['geography_version']} from {ec3['geography_source_feed']}, "
          f"{ec3['zone_count']} zones")
    print(f"    period: {ec3['observation_period']}")
    for r in ec3["rows"]:
        print(f"    {r['operator']}: {r['inside_policy_geography']} of "
              f"{r['vehicles_observed']} inside policy geography ({r['share_pct']}%)")

    print(f"\nEC8 — {ec8['requirement']}: {ec8['verdict']}")
    print(f"    window {ec8['first_capture']} → {ec8['last_capture']} "
          f"= {ec8['elapsed_hours']} h (threshold {ec8['threshold_hours']} h, "
          f"covered: {ec8['window_covers_threshold']})")
    for st in ec8["identifier_stability"]:
        print(f"    identifiers {st['operator']}: {st['verdict']} "
              f"({st['overlap']} matched, {st['identifier_form']})")
    for r in ec8["rows"]:
        print(f"    {r['operator']}: {r['present_in_both']} present in both; "
              f"{r['unmoved_within_tolerance']} unmoved within "
              f"{ec8['move_tolerance_m']} m; {r['moved']} moved; "
              f"{r['appeared']} appeared; {r['disappeared']} disappeared")
    print(f"    evidence class: {ec8['why_not_a_violation'][:96]}...")

    json.dump({"ec2": ec2, "ec3": ec3, "ec8": ec8},
              open(OUT / "ec_results.json", "w"), indent=1)
    print(f"\nWritten: {OUT / 'ec_results.json'}")


if __name__ == "__main__":
    main()
