# Columbus Shared Mobility Data Platform — working prototype

A single-page prototype that ingests published shared-micromobility feeds for
Columbus, Ohio and reports fleet deployment, parking compliance against
published policy, service-level coverage, feed data quality, trip-level MDS
processing, the enforcement assessment that sits in front of any penalty, and
311 case handling.

It is one HTML file with no build step, no dependencies, and no network calls.
Open `index.html` in any browser, or serve it from GitHub Pages.

## What it shows

| View | What it answers |
|---|---|
| **Overview** | How many devices each operator has deployed, by type and status |
| **Map & enforcement** | Where idle vehicles are, against the permitted service area and the restricted polygons. Selecting a vehicle runs it through the eight-element enforcement assessment and reports which elements are satisfied and which are not. Markers hold their on-screen size as you zoom, so dense blocks separate instead of merging. |
| **Compliance** | What share of idle vehicles sit inside a no-park or no-ride zone, measured against a random-placement baseline |
| **SLA coverage** | Each of the City's own SLA codes, and what each one needs in order to be measured |
| **Data quality** | 17 checks run against the captured feeds, with every failure listed |
| **Trip data (MDS)** | 411 records received, 404 accepted, 5 rejected each with a named reason code, 2 duplicates ignored; re-ingesting the identical payload adds nothing; operator trip KPIs; lineage carried on every flagged trip |
| **Enforcement** | The eight-element gate applied to 404 trip endings: 76 clear the substantive elements, 328 are refused with the failing element named, and **nothing is chargeable**. EC8 computed from source to determination, with enforcement deliberately disabled |
| **311 (OneView)** | 21 service requests delivered, 4 cases created, 9 repeats ignored, 2 rejected, with the full audit trail in order |
| **Verification** | All eight test suites with their pass counts and the SHA-256 digest of each source file that ran |
| **Export** | CSV of the current filtered view, plus the SHA-256 manifest of every captured feed |

## Reproducing the numbers

`verification/` holds the test suites, both captured snapshots, and a runner.

```
python3 verification/run_all.py
```

No arguments, no network access, no credentials. It reports 22 of 22 MDS
validation cases, 16 of 16 transport tests including replay protection across a
receiver restart, 10 of 10 OneView simulator tests, and 11 of 11 operational
checks, and writes a manifest recording the digest of every file that ran. See
`verification/README.md`.

## Enforcement: what the gate refuses

A device inside a restricted polygon is a spatial match. It is not a violation
and it is never chargeable on position alone.

Selecting such a vehicle produces an **inspection candidate**, not a penalty.
The fine action stays unavailable until all eight elements are satisfied, and
the view names the ones outstanding. On this dataset four are satisfied —
place, prohibited place, policy in force, record integrity — and four are not:
identity and time cannot be established from GBFS, a single snapshot cannot
separate a parked vehicle from one passing through, and no notice has been
served. `notice_and_cure` fails on every case in the set, which is the gate
working as intended.

The prohibition has a source: RFQ033920 scope items 3.4.14 and 3.4.15, with the
geometry from the City's published feed. The fee and the cure window do not, and
are labelled as demonstration assumptions wherever they appear — those come from
the executed City and operator agreement, which ChiResearch has not seen.

## Data

Built on the public GBFS feeds published by the operators serving Columbus,
captured as point-in-time snapshots. Feed locations come from the MobilityData
GBFS systems registry. Service-level thresholds are quoted from the City of
Columbus Service Level Agreement Report Cards.

Every feed response was hashed on capture and the hashes ship with the page, so
a third party can confirm they are analysing the same bytes.

MDS endpoints are credentialed and ChiResearch holds no operator credential, so
**no operator trip data is used here and none could be**. The MDS trip records
are generated locally and are schema-conformant test data; the Provider and
Agency transport tests run against a conformant mock. The policy geography the
trips are tested against is the City's real published feed. The 311 service
requests are invented and run against a labelled OneView simulator;
ChiResearch holds no Granicus OneView credential.

## How the compliance figure is computed

Each idle vehicle's published position is projected to UTM zone 17N and tested
for containment against the restricted polygons. Reserved vehicles are
excluded: a reserved vehicle is mid-trip, so its position is not a parking
event.

The no-park and no-ride polygon sets overlap, so a vehicle can sit inside both.
The restricted count is therefore a count of **distinct** vehicles, not the sum
of the two zone counts — the page shows both numbers so the difference is
visible rather than implied.

The percentage is compared against a baseline: the share of the permitted
service area that the restricted polygons cover. If vehicles were scattered at
random, roughly that share would fall inside them. The gap between the measured
rate and the baseline is the signal; the raw count is not, because it scales
with how much ground the zones happen to cover.

## Accessibility

The prototype targets WCAG 2.2 Level AA, which is the standard public-sector
digital services are held to.

- Audited with axe-core across **all ten views in both light and dark mode —
  twenty page states, zero violations** (re-run 16 November 2026 after the
  enforcement, MDS, 311 and verification views were added).
- Every colour pair carrying text clears 4.5:1. Status colours ship as separate
  graphic and ink tokens, because a fill that reads well is not always legible
  as text.
- Nothing is encoded by colour alone. Verdicts carry a word, zones carry a
  label, operators carry a name.
- Every interactive element is keyboard reachable with a visible 3px focus
  ring, verified on each tab stop in both modes.
- Map markers are a few pixels across, which fails the WCAG target-size minimum
  on its own. The restricted-zone worklist below the map is the accessible path
  to the same action, and is the more practical one for enforcement work.
- Views are links with their own URLs, so the back button and shared links work.
- The categorical palette is validated for colour-vision deficiency separation
  (worst adjacent pair ΔE 24.7 protan in light, 26.8 in dark).
- Status verdicts carry a glyph as well as a word, so they survive red-green
  colour blindness, greyscale printing and forced-colors mode. The positive
  status is teal rather than green: deuteranopia collapses green and red to
  nearly the same percept (ΔE 4.8), and teal separates them (ΔE 51.4).
- Verified by simulating protanopia, deuteranopia and tritanopia on the
  rendered pages, not by inspection.

## Scope

This is a working prototype on point-in-time snapshots. It is not deployed
production software. Inspection candidates recorded in the page are local to
the browser session and are not transmitted anywhere, and no penalty of any
kind is issued from it.

Several SLA requirements cannot be measured from public feeds at all; the SLA
view marks which ones and says what each would need.

---

ChiResearch Technologies, LLC
