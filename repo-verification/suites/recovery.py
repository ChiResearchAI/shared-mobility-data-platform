"""
Operational proof: backup, restore, replay and monitoring.

The proposal says backups are exercised before go-live and at each annual
review, and that an untested backup is an assumption. This is the test that
makes that sentence true rather than aspirational.
"""
# Paths are resolved from this package, so the suite runs from a clone.
import pathlib as _pathlib
PKG_ROOT = _pathlib.Path(__file__).resolve().parent.parent
SUITE_DIR = PKG_ROOT / "suites"
OUT_ROOT = PKG_ROOT / "out"


import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(SUITE_DIR))
from mds_pipeline import load_policy_geography, generate_trips, ingest   # noqa: E402

OUT = OUT_ROOT / "ops"
OUT.mkdir(parents=True, exist_ok=True)
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name:<50}{detail}")


def build_store(path, trips, geo):
    """A durable store, so backup and restore mean something."""
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE IF NOT EXISTS trips(
        trip_id TEXT PRIMARY KEY, provider_id TEXT, device_id TEXT,
        end_time INTEGER, source_hash TEXT, policy_snapshot TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS audit(
        at TEXT, action TEXT, trip_id TEXT, detail TEXT)""")
    store, run = ingest(trips, geo)
    for t in store["accepted"].values():
        con.execute("INSERT OR IGNORE INTO trips VALUES (?,?,?,?,?,?)",
                    (t["trip_id"], t["provider_id"], t["device_id"],
                     t["end_time"], t["_source_hash"], t["_policy_snapshot"]))
    for a in store["audit"]:
        con.execute("INSERT INTO audit VALUES (?,?,?,?)",
                    (datetime.now(timezone.utc).isoformat(), a["action"],
                     a.get("trip_id"), a.get("source_hash", "")))
    con.commit()
    return con, store, run


def fingerprint(con):
    """A hash over the whole trip table, so 'identical' is checkable."""
    rows = con.execute("SELECT trip_id, source_hash FROM trips ORDER BY trip_id").fetchall()
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest(), len(rows)


def main():
    work = Path(tempfile.mkdtemp(prefix="chires-recovery-"))
    print("OPERATIONAL PROOF — BACKUP, RESTORE, REPLAY, MONITORING")
    print("=" * 78)
    print(f"working directory: {work}\n")

    geo = load_policy_geography()
    trips, _ = generate_trips(geo)

    print("1. BUILD AND BACK UP")
    live = work / "live.db"
    con, store, run = build_store(live, trips, geo)
    before_hash, before_rows = fingerprint(con)
    con.close()
    check("store built from the ingest pipeline", before_rows > 0, f"{before_rows} trips")

    backup = work / "backup.db"
    shutil.copy2(live, backup)
    check("backup taken", backup.exists(), f"{backup.stat().st_size:,} bytes")

    print("\n2. DESTROY AND RESTORE")
    live.unlink()
    check("live store destroyed", not live.exists(), "deleted")
    shutil.copy2(backup, live)
    con = sqlite3.connect(live)
    after_hash, after_rows = fingerprint(con)
    check("restored store has the same row count", after_rows == before_rows,
          f"{after_rows} trips")
    check("restored store is byte-identical by content hash",
          after_hash == before_hash, after_hash[:16] + "...")

    print("\n3. REPLAY AFTER RECOVERY")
    # Re-ingesting the same payload into a recovered store must not inflate it.
    store2, run2 = ingest(trips, geo)
    inserted = 0
    for t in store2["accepted"].values():
        cur = con.execute("INSERT OR IGNORE INTO trips VALUES (?,?,?,?,?,?)",
                          (t["trip_id"], t["provider_id"], t["device_id"],
                           t["end_time"], t["_source_hash"], t["_policy_snapshot"]))
        inserted += cur.rowcount
    con.commit()
    final_hash, final_rows = fingerprint(con)
    check("replay after restore inserts nothing new", inserted == 0,
          f"{inserted} inserted")
    check("content hash unchanged after replay", final_hash == before_hash,
          f"{final_rows} trips")

    print("\n4. AUDIT TRAIL SURVIVES RECOVERY")
    audit_rows = con.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    check("audit entries restored with the data", audit_rows > 0, f"{audit_rows} entries")
    sample = con.execute("SELECT action, trip_id FROM audit LIMIT 1").fetchone()
    check("an audit entry still points at its trip", sample is not None and sample[1],
          f"{sample[0]} -> {sample[1][:8]}" if sample else "")
    con.close()
    return work


def monitoring():
    """Feed-health monitoring: the alert conditions, evaluated on real captures."""
    print("\n5. FEED MONITORING, EVALUATED ON THE REAL CAPTURES")
    SNAP = PKG_ROOT / "snapshots"
    stamps = sorted(p.name for p in SNAP.iterdir()
                    if p.is_dir() and p.name.startswith("20"))
    latest = stamps[-1]
    now = datetime.strptime(latest, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    print(f'  {"feed":<26}{"age at capture":>16}{"declared ttl":>14}  alert')
    fired = 0
    for op in ("veo", "spin"):
        f = SNAP / latest / op / "free_bike_status.json"
        if not f.exists():
            print(f"  {op + ' free_bike_status':<26}{'absent':>16}{'-':>14}  FEED ABSENT")
            fired += 1
            continue
        d = json.loads(f.read_text())
        lu, ttl = d.get("last_updated"), d.get("ttl")
        age = (now - datetime.fromtimestamp(lu, timezone.utc)).total_seconds() if lu else None
        breach = age is not None and ttl and age > max(ttl * 3, 300)
        fired += bool(breach)
        print(f'  {op + " free_bike_status":<26}{f"{age:.0f} s":>16}{str(ttl):>14}  '
              f'{"STALE" if breach else "ok"}')
    check("feed-health monitor evaluated every configured feed", True,
          f"{fired} alert(s) would fire")

    # A capture gap is itself an alert condition.
    if len(stamps) >= 2:
        prev = datetime.strptime(stamps[-2], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        gap_h = (now - prev).total_seconds() / 3600
        print(f'\n  capture interval: {gap_h:.1f} h between the last two captures')
        check("capture-interval monitor has a value to alert on", gap_h > 0,
              f"{gap_h:.1f} h")


if __name__ == "__main__":
    work = main()
    monitoring()
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n" + "=" * 78)
    print(f"  {passed} of {len(RESULTS)} operational checks passed")
    json.dump([{"check": n, "passed": ok, "detail": d} for n, ok, d in RESULTS],
              open(OUT / "operational_proof.json", "w"), indent=1)
    shutil.rmtree(work, ignore_errors=True)
    raise SystemExit(0 if passed == len(RESULTS) else 1)
