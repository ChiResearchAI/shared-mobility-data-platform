"""
EC8 end to end: source records -> vehicle state timeline -> idle duration ->
threshold -> determination, with every figure traceable.

The City's published report card states EC8 as: fewer than 10 percent of
vehicles idle beyond 120 hours, averaged over a 30-day window. Those are the
historical reported terms, not the executed 2027 contract, so the rule below
carries that status explicitly and enforcement stays disabled until the City
confirms the operative definition.

Two runs, because both are true and they say different things:
  A. a labelled synthetic 30-day fleet, which exercises the whole rule
  B. the real Columbus captures, which cannot reach 120 hours and must say so
"""
# Paths are resolved from this package, so the suite runs from a clone.
import pathlib as _pathlib
PKG_ROOT = _pathlib.Path(__file__).resolve().parent.parent
SUITE_DIR = PKG_ROOT / "suites"
OUT_ROOT = PKG_ROOT / "out"


import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

SNAPSHOTS = PKG_ROOT / "snapshots"
OUT = OUT_ROOT / "sla_ec8"
OUT.mkdir(parents=True, exist_ok=True)

RULE = {
    "rule_id": "EC8",
    "version": "report-card-2025",
    "rule_type": "fleet_idle_duration",
    "statement": "Fewer than 10 percent of vehicles idle beyond 120 hours, "
                 "averaged over a 30-day window.",
    "observation_window_days": 30,
    "idle_threshold_hours": 120,
    "maximum_fraction_over_threshold": 0.10,
    "denominator_definition": "requires_contract_confirmation",
    "source": "City of Columbus SLA Report Cards, May-December 2025",
    "status": "historical_reported_terms_not_executed_contract",
    "enforcement_enabled": False,
}

# A vehicle must be observed at least this often for a gap to count as idle
# rather than as missing data. Without it, a feed outage looks like a fleet
# standing still, which is the opposite of the truth.
MAX_GAP_HOURS = 12.0
MOVE_TOLERANCE_M = 15.0


def metres(lat1, lon1, lat2, lon2):
    import math
    mean_lat = math.radians((lat1 + lat2) / 2)
    dx = math.radians(lon2 - lon1) * math.cos(mean_lat) * 6_371_000
    dy = math.radians(lat2 - lat1) * 6_371_000
    return math.hypot(dx, dy)


def build_timeline(observations):
    """Turn position observations into parked / moved / gap segments.

    observations: list of (datetime, lat, lon), ascending.
    Returns segments, each stating what it is and why.
    """
    segments = []
    for earlier, later in zip(observations, observations[1:]):
        t0, lat0, lon0 = earlier
        t1, lat1, lon1 = later
        gap_h = (t1 - t0).total_seconds() / 3600.0
        moved_m = metres(lat0, lon0, lat1, lon1)

        if gap_h > MAX_GAP_HOURS:
            kind = "unobserved"          # cannot be claimed as idle
            reason = (f"{gap_h:.1f} h between observations exceeds the "
                      f"{MAX_GAP_HOURS:.0f} h maximum; treated as missing data")
        elif moved_m <= MOVE_TOLERANCE_M:
            kind = "idle"
            reason = f"moved {moved_m:.1f} m, within the {MOVE_TOLERANCE_M:.0f} m tolerance"
        else:
            kind = "moved"
            reason = f"moved {moved_m:.1f} m"
        segments.append({"from": t0, "to": t1, "hours": gap_h,
                         "kind": kind, "reason": reason})
    return segments


def longest_idle_run(segments):
    """The longest unbroken idle stretch, and the evidence behind it.

    An 'unobserved' segment breaks the run. A vehicle that vanished from the
    feed for a day has not been shown to be idle across that day.
    """
    best = {"hours": 0.0, "from": None, "to": None, "segments": 0}
    run_hours, run_start, run_count = 0.0, None, 0
    for seg in segments:
        if seg["kind"] == "idle":
            if run_start is None:
                run_start = seg["from"]
            run_hours += seg["hours"]
            run_count += 1
            if run_hours > best["hours"]:
                best = {"hours": run_hours, "from": run_start,
                        "to": seg["to"], "segments": run_count}
        else:
            run_hours, run_start, run_count = 0.0, None, 0
    return best


def assess_fleet(fleet, window_start, window_end):
    """Apply EC8 to a fleet and return the determination with its evidence.

    `fleet` maps vehicle_id -> list of (datetime, lat, lon).
    """
    window_hours = (window_end - window_start).total_seconds() / 3600.0
    per_vehicle, qualifying, over_threshold, excluded = [], 0, 0, 0

    for vid, obs in fleet.items():
        obs = sorted(obs, key=lambda o: o[0])
        segments = build_timeline(obs)
        covered = sum(s["hours"] for s in segments if s["kind"] != "unobserved")
        longest = longest_idle_run(segments)

        # A vehicle observed across too little of the window cannot carry a
        # 120-hour finding either way.
        eligible = covered >= RULE["idle_threshold_hours"]
        if not eligible:
            excluded += 1
            status = "excluded: insufficient observed coverage"
        else:
            qualifying += 1
            if longest["hours"] >= RULE["idle_threshold_hours"]:
                over_threshold += 1
                status = "over threshold"
            else:
                status = "under threshold"

        per_vehicle.append({
            "vehicle": vid[-18:],
            "observations": len(obs),
            "observed_hours": round(covered, 1),
            "longest_idle_hours": round(longest["hours"], 1),
            "idle_from": longest["from"].isoformat() if longest["from"] else None,
            "idle_to": longest["to"].isoformat() if longest["to"] else None,
            "status": status,
        })

    if qualifying == 0:
        determination = "NOT ASSESSABLE"
        reason = ("no vehicle was observed across enough of the window to "
                  "support a 120-hour finding")
        fraction = None
    else:
        fraction = over_threshold / qualifying
        determination = "PASS" if fraction < RULE["maximum_fraction_over_threshold"] else "FAIL"
        reason = (f"{over_threshold} of {qualifying} qualifying vehicles idle "
                  f"beyond {RULE['idle_threshold_hours']} h")

    return {
        "rule": f'{RULE["rule_id"]} ({RULE["version"]})',
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "window_hours": round(window_hours, 1),
        "window_required_days": RULE["observation_window_days"],
        "window_sufficient": window_hours >= RULE["observation_window_days"] * 24,
        "vehicles_in_fleet": len(fleet),
        "qualifying_denominator": qualifying,
        "excluded_insufficient_coverage": excluded,
        "over_threshold": over_threshold,
        "fraction_over_threshold": round(fraction, 4) if fraction is not None else None,
        "maximum_allowed": RULE["maximum_fraction_over_threshold"],
        "determination": determination,
        "reason": reason,
        "enforcement_enabled": RULE["enforcement_enabled"],
        "per_vehicle": per_vehicle,
    }


# ---------------------------------------------------- run A: synthetic fleet
def synthetic_fleet(seed=20261115):
    """A labelled 30-day fleet that exercises every branch of the rule.

    SYNTHETIC TEST DATA. Positions are invented. Its purpose is to drive the
    whole calculation, including the branches the real captures cannot reach.
    """
    rng = random.Random(seed)
    start = datetime(2026, 10, 16, tzinfo=timezone.utc)
    fleet = {}
    for i in range(60):
        vid = f"SYN-{i:03d}"
        lat, lon = 39.96 + rng.uniform(-0.03, 0.03), -83.00 + rng.uniform(-0.03, 0.03)
        obs, t = [], start
        # Every vehicle is observed every 6 hours for 30 days.
        if i < 7:
            idle_until = start + timedelta(hours=rng.uniform(130, 300))   # long idle
        elif i < 12:
            idle_until = start + timedelta(hours=rng.uniform(80, 115))    # under threshold
        else:
            idle_until = start                                            # moves normally
        while t <= start + timedelta(days=30):
            if t > idle_until:
                lat += rng.uniform(-0.004, 0.004)
                lon += rng.uniform(-0.004, 0.004)
            obs.append((t, round(lat, 6), round(lon, 6)))
            t += timedelta(hours=6)
        # Two vehicles drop out of the feed for two days, to prove a gap breaks
        # an idle run rather than extending it.
        if i in (58, 59):
            obs = [o for o in obs if not (start + timedelta(days=5) < o[0]
                                          < start + timedelta(days=7))]
        fleet[vid] = obs
    return fleet, start, start + timedelta(days=30)


# ------------------------------------------------- run B: the real captures
def real_fleet():
    """The actual Columbus captures ChiResearch holds."""
    stamps = sorted(p.name for p in SNAPSHOTS.iterdir()
                    if p.is_dir() and p.name.startswith("20"))
    fleet = {}
    for stamp in stamps:
        when = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        for op in ("veo", "spin"):
            f = SNAPSHOTS / stamp / op / "free_bike_status.json"
            if not f.exists():
                continue
            for v in json.loads(f.read_text())["data"]["bikes"]:
                if v.get("lat") is None or v.get("lon") is None:
                    continue
                fleet.setdefault(f'{op}:{v["bike_id"]}', []).append(
                    (when, v["lat"], v["lon"]))
    first = datetime.strptime(stamps[0], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    last = datetime.strptime(stamps[-1], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    return fleet, first, last, stamps


def report(title, result, note=""):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)
    if note:
        print(note)
        print()
    print(f'  rule                        {result["rule"]}')
    print(f'  window                      {result["window_start"][:16]} to '
          f'{result["window_end"][:16]}  ({result["window_hours"]} h)')
    print(f'  window long enough for the rule? {result["window_sufficient"]} '
          f'(needs {result["window_required_days"]} days)')
    print(f'  vehicles in fleet           {result["vehicles_in_fleet"]}')
    print(f'  qualifying denominator      {result["qualifying_denominator"]}')
    print(f'  excluded, thin coverage     {result["excluded_insufficient_coverage"]}')
    print(f'  idle beyond {RULE["idle_threshold_hours"]} h          '
          f'{result["over_threshold"]}')
    frac = result["fraction_over_threshold"]
    print(f'  fraction over threshold     '
          f'{"n/a" if frac is None else f"{frac:.2%}"}  '
          f'(maximum {result["maximum_allowed"]:.0%})')
    print(f'  DETERMINATION               {result["determination"]}')
    print(f'  reason                      {result["reason"]}')
    print(f'  enforcement enabled         {result["enforcement_enabled"]}')


def main():
    print("EC8 — COMPLETE SOURCE-TO-DETERMINATION CHAIN")
    print(f'Rule as published: "{RULE["statement"]}"')
    print(f'Status: {RULE["status"]}')
    print(f'Denominator: {RULE["denominator_definition"]}')

    fleet, ws, we = synthetic_fleet()
    a = assess_fleet(fleet, ws, we)
    report("RUN A — LABELLED SYNTHETIC 30-DAY FLEET", a,
           "SYNTHETIC TEST DATA. Positions invented to exercise every branch\n"
           "of the rule, including the ones the real captures cannot reach.")
    print("\n  sample of the per-vehicle evidence:")
    print(f'    {"vehicle":<22}{"obs":>5}{"observed h":>12}{"longest idle":>14}  status')
    for row in sorted(a["per_vehicle"], key=lambda r: -r["longest_idle_hours"])[:5]:
        print(f'    {row["vehicle"]:<22}{row["observations"]:>5}'
              f'{row["observed_hours"]:>12}{row["longest_idle_hours"]:>14}  {row["status"]}')
    gaps = [r for r in a["per_vehicle"] if "excluded" in r["status"]]
    print(f'\n  excluded for thin coverage: {len(gaps)}')
    dropouts = [r for r in a["per_vehicle"] if r["vehicle"].endswith(("058","059"))]
    print("  the two vehicles that dropped out of the feed for two days:")
    for row in dropouts:
        print(f'    {row["vehicle"]:<22}observed {row["observed_hours"]} h, '
              f'longest idle {row["longest_idle_hours"]} h — {row["status"]}')
    print("  the gap breaks the idle run rather than extending it, which is the")
    print("  difference between a parked fleet and a feed outage.")

    rfleet, rs, rend, stamps = real_fleet()
    b = assess_fleet(rfleet, rs, rend)
    report("RUN B — THE REAL COLUMBUS CAPTURES", b,
           f'Captures held: {len(stamps)} ({", ".join(stamps)}).\n'
           f'Real published GBFS data, no synthetic positions.')
    print("\n  Why this is the honest answer: the rule needs a 30-day window and")
    print("  a 120-hour idle run. Two captures 32 hours apart cannot produce")
    print("  either. Reporting a pass here would be the error, not a courtesy.")

    json.dump({"rule": RULE,
               "run_a_synthetic": {k: v for k, v in a.items() if k != "per_vehicle"},
               "run_b_real": {k: v for k, v in b.items() if k != "per_vehicle"}},
              open(OUT / "ec8_determination.json", "w"), indent=1)
    print(f'\nWritten: {OUT / "ec8_determination.json"}')


if __name__ == "__main__":
    main()
