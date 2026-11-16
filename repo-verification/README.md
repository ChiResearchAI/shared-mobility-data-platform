# Verification package — Columbus shared micromobility data platform

Every technical figure in the ChiResearch proposal for City of Columbus
RFQ033920 is produced by the code in this folder. One command runs all of it.

```
python3 run_all.py
```

No arguments, no network access, no credentials. The suites read the captured
feeds in `snapshots/` and write their results to `out/`, including a manifest
that records the SHA-256 digest of every source file that ran.

Python 3.11 or later. `shapely` is the only third-party dependency.

## What runs, and what it reports

| Suite | Reports |
|---|---|
| `suites/verify_mds.py` | MDS validation and reproduction — **22 of 22** |
| `suites/test_transport.py` | MDS Provider and Agency transport — **16 of 16**, including replay protection across a receiver restart |
| `suites/adapter.py` | OneView 311 simulator — **10 of 10** |
| `suites/recovery.py` | Backup, restore and monitoring — **11 of 11** |
| `suites/enforce.py` | The eight-element enforcement assessment |
| `suites/ec8.py` | EC8 from source to determination |
| `suites/ec_tests.py` | Columbus SLA codes EC2, EC3 and EC8 |
| `suites/verify.py` | The ten-test verification sequence |

`out/verification_summary.md` and `out/execution_manifest.json` are written
from what the runs actually returned, never from the figures quoted in the
proposal. If the two disagree, the proposal is what changes.

## What the data is, and what it is not

`snapshots/` holds two captures of the **public GBFS feeds** published by Veo
and Spin for Columbus, taken 32 hours apart on 14 and 15 November 2026. Those
are real.

The MDS trip records are **locally generated and schema-conformant test data**.
MDS is credentialed, and ChiResearch holds no operator credential, so no
operator trip data was used or could have been. The policy geography the trips
are tested against is the City's real published feed.

The 311 service requests are **invented**, run against a labelled OneView
simulator. ChiResearch holds no Granicus OneView credential.

## A difference you will see, and why it is the point

The proposal quotes the 14 November capture: 147 zones, 124 no-park, 20
no-ride. Run this package today and the latest capture reports 147 zones, 125
no-park, 19 no-ride.

One polygon changed meaning between the two captures. That is not a
discrepancy in the figures; it is the finding. A compliance number computed
against today's map would restate the past and make any penalty built on it
indefensible, which is why every capture is retained and stamped and every
figure is recomputed against the geography in force at the time of the
observation. The proposal's Figure 1 is that polygon.

## Enforcement: what the gate refuses

`suites/enforce.py` assesses 404 trip endings against rule COL-PARK-001 v3 and
returns 76 clearing the substantive elements, 328 refused, and **nothing
chargeable**. `notice_and_cure` fails on every case because no notice has been
served. That is the gate working as intended: a device inside a restricted
polygon is a spatial match, not a violation, and it is never chargeable on
position alone.
