"""
One command that runs every suite, hashes everything it touched, and writes a
summary from the actual results.

The summary is generated from what the runs returned, never from the figures
printed in the proposal. If the two disagree, the proposal is what changes.
"""
# Paths are resolved from this package, so the runner works from a clone.
import pathlib as _pathlib
PKG_ROOT = _pathlib.Path(__file__).resolve().parent
SUITE_DIR = PKG_ROOT / "suites"
OUT_ROOT = PKG_ROOT / "out"


import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

OUT = OUT_ROOT
OUT.mkdir(parents=True, exist_ok=True)

SUITES = [
    ("MDS validation and reproduction", str(SUITE_DIR / "verify_mds.py"),
     r"(\d+) passed, (\d+) failed, (\d+) cases"),
    ("MDS Provider/Agency transport", str(SUITE_DIR / "test_transport.py"),
     r"(\d+) of (\d+) transport tests passed"),
    ("OneView 311 simulator", str(SUITE_DIR / "adapter.py"),
     r"(\d+) of (\d+) adapter tests passed"),
    ("Operational: backup, restore, monitoring", str(SUITE_DIR / "recovery.py"),
     r"(\d+) of (\d+) operational checks passed"),
    ("Eight-element enforcement", str(SUITE_DIR / "enforce.py"), None),
    ("EC8 source-to-determination", str(SUITE_DIR / "ec8.py"), None),
    ("Columbus SLA codes EC2/EC3/EC8", str(SUITE_DIR / "ec_tests.py"), None),
    ("Ten-test verification sequence", str(SUITE_DIR / "verify.py"), None),
]

SOURCES = [
    str(SUITE_DIR / "mds_pipeline.py"),
    str(SUITE_DIR / "verify_mds.py"),
    str(SUITE_DIR / "mock_mds.py"),
    str(SUITE_DIR / "collector.py"),
    str(SUITE_DIR / "test_transport.py"),
    str(SUITE_DIR / "enforce.py"),
    str(SUITE_DIR / "ec8.py"),
    str(SUITE_DIR / "adapter.py"),
    str(SUITE_DIR / "recovery.py"),
    str(SUITE_DIR / "ec_tests.py"),
    str(SUITE_DIR / "verify.py"),
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(path):
    r = subprocess.run([sys.executable, "-I", path], capture_output=True,
                       text=True, cwd="/tmp", timeout=600)
    return r.returncode, r.stdout + r.stderr


def main():
    started = datetime.now(timezone.utc)
    print("CHIRESEARCH TECHNICAL VERIFICATION PACKAGE")
    print("=" * 78)
    print(f"run started   {started.isoformat()}")
    print(f"python        {platform.python_version()} on {platform.system()}")
    print(f"suites        {len(SUITES)}")
    print()

    import re
    results, all_ok = [], True
    for name, path, pattern in SUITES:
        code, output = run(path)
        detail = ""
        if pattern:
            m = re.search(pattern, output)
            if m:
                g = [int(x) for x in m.groups()]
                detail = (f"{g[0]} passed of {g[2]}" if len(g) == 3
                          else f"{g[0]} of {g[1]} passed")
        ok = code == 0
        all_ok &= ok
        results.append({"suite": name, "script": path, "exit_code": code,
                        "passed": ok, "detail": detail,
                        "source_sha256": sha(path)})
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<42}{detail}")
        (OUT / (Path(path).stem + "_output.txt")).write_text(output)

    manifest = {
        "generated_utc": started.isoformat(),
        "python_version": platform.python_version(),
        "all_suites_passed": all_ok,
        "sources": [{"path": p, "bytes": Path(p).stat().st_size,
                     "sha256": sha(p)} for p in SOURCES],
        "suites": results,
        "data_provenance": {
            "policy_geography": "real, published Columbus GBFS geofencing feed",
            "gbfs_vehicle_positions": "real, published Veo and Spin feeds",
            "mds_trip_records": "SYNTHETIC, schema-conformant, generated locally",
            "oneview_311_cases": "SYNTHETIC, ChiResearch simulator",
            "ec8_30_day_fleet": "SYNTHETIC, labelled in the output",
            "operator_credentials": "none held; no operator endpoint contacted",
        },
    }
    (OUT / "execution_manifest.json").write_text(json.dumps(manifest, indent=1))

    lines = [
        "# ChiResearch RFQ033920 - Verification Summary",
        "",
        f"Generated {started.isoformat()} from actual suite output, not from "
        "figures quoted in the proposal.",
        "",
        "| Suite | Result | Detail |",
        "|---|---|---|",
    ]
    for r in results:
        lines.append(f'| {r["suite"]} | {"PASS" if r["passed"] else "FAIL"} '
                     f'| {r["detail"] or "see output"} |')
    lines += [
        "",
        "## Data provenance",
        "",
        "| Input | Nature |",
        "|---|---|",
    ]
    for k, v in manifest["data_provenance"].items():
        lines.append(f"| {k} | {v} |")
    lines += [
        "",
        "## Scope boundary",
        "",
        "- **Tested:** MDS schema and domain validation, Provider and Agency",
        "  transport against a conformant mock including replay protection",
        "  across a process restart, policy versioning, the eight-element",
        "  enforcement gate, EC8 end to end, a simulated OneView 311 workflow,",
        "  and backup/restore with audit-trail survival.",
        "- **Not tested, pending authorization:** live Veo and Spin MDS",
        "  credentials, the City-approved OneView interface, Department of",
        "  Technology security review, and any production enforcement authority.",
        "",
        "No bearer token, operator credential or resident information appears in",
        "this package.",
    ]
    (OUT / "verification_summary.md").write_text("\n".join(lines) + "\n")

    print()
    print("=" * 78)
    print(f"  {'ALL SUITES PASSED' if all_ok else 'SOME SUITES FAILED'}")
    print(f"  manifest  {OUT / 'execution_manifest.json'}")
    print(f"  summary   {OUT / 'verification_summary.md'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
