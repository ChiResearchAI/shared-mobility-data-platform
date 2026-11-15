# Columbus Shared Mobility Data Platform — working prototype

A single-page prototype that ingests published shared-micromobility feeds for
Columbus, Ohio and reports fleet deployment, parking compliance against published
policy, service-level coverage, and feed data quality.

It is one HTML file with no build step, no dependencies, and no network calls.
Open `index.html` in any browser, or serve it from GitHub Pages.

## What it shows

| View | What it answers |
|---|---|
| **Overview** | How many devices each operator has deployed, by type and status |
| **Map & enforcement** | Where idle vehicles are, against the permitted service area and the restricted polygons. Scroll or pinch to zoom, drag to pan, click a vehicle to inspect it and log an enforcement action. Markers hold their on-screen size as you zoom, so dense blocks separate instead of merging. |
| **Compliance** | What share of idle vehicles sit inside a no-park or no-ride zone, measured against a random-placement baseline |
| **SLA coverage** | Each of the City's own SLA codes, and what each one needs in order to be measured |
| **Data quality** | 17 checks run against the captured feeds, with every failure listed |
| **Export** | CSV of the current filtered view, plus the SHA-256 manifest of every captured feed |

## Data

Built on the public GBFS feeds published by the operators serving Columbus,
captured as a point-in-time snapshot. Feed locations come from the MobilityData
GBFS systems registry. Service-level thresholds are quoted from the City of
Columbus Service Level Agreement Report Cards.

Every feed response was hashed on capture and the hashes ship with the page, so
a third party can confirm they are analysing the same bytes.

## How the compliance figure is computed

Each idle vehicle's published position is projected to UTM zone 17N and tested
for containment against the restricted polygons. Reserved vehicles are excluded:
a reserved vehicle is mid-trip, so its position is not a parking event.

The no-park and no-ride polygon sets overlap, so a vehicle can sit inside both.
The restricted count is therefore a count of **distinct** vehicles, not the sum
of the two zone counts — the page shows both numbers so the difference is visible
rather than implied.

The percentage is compared against a baseline: the share of the permitted service
area that the restricted polygons cover. If vehicles were scattered at random,
roughly that share would fall inside them. The gap between the measured rate and
the baseline is the signal; the raw count is not, because it scales with how much
ground the zones happen to cover.

## Accessibility

The prototype targets WCAG 2.2 Level AA, which is the standard public-sector
digital services are held to.

- Audited with axe-core across all six views in both light and dark mode: zero
  violations.
- Every colour pair carrying text clears 4.5:1. Status colours ship as separate
  graphic and ink tokens, because a fill that reads well is not always legible
  as text.
- Nothing is encoded by colour alone. Verdicts carry a word, zones carry a
  label, operators carry a name.
- Every interactive element is keyboard reachable with a visible 3px focus ring,
  verified on each tab stop in both modes.
- Map markers are a few pixels across, which fails the WCAG target-size minimum
  on its own. The restricted-zone worklist below the map is the accessible path
  to the same action, and is the more practical one for enforcement work.
- Views are links with their own URLs, so the back button and shared links work.
- The categorical palette is validated for colour-vision deficiency separation
  (worst adjacent pair ΔE 24.7 protan in light, 26.8 in dark).

## Scope

This is a working prototype on a point-in-time snapshot. It is not deployed
production software. Enforcement actions recorded in the page are local to the
browser session and are not transmitted anywhere.

MDS endpoints are not public, so nothing here uses MDS. Several SLA requirements
cannot be measured from public feeds at all; the SLA view marks which ones and
says what each would need.

---

ChiResearch Technologies, LLC
