# Accessibility audit

`audit.js` runs axe-core against every view of the prototype, in both light and
dark mode, and reports violations per page state.

```
npm install axe-core puppeteer-core
node audit.js
```

It needs Node and a Chrome or Chromium binary; set `executablePath` in
`audit.js` if yours is not at `/usr/bin/google-chrome`. It loads the prototype
from a local path — point `PAGE` at your copy of `index.html`.

`axe_results.txt` is the output of the run on 16 November 2026: ten views, two
colour modes, twenty page states, zero violations against the wcag2a, wcag2aa,
wcag21a, wcag21aa and wcag22aa rule sets.

The dark-mode pass is verified to have actually switched mode rather than
silently auditing light twice: the body background moves from #f9f9f7 to
#0d0d0d and the root `data-theme` attribute changes.
