// Re-run the accessibility audit across every view the prototype now has,
// in both colour modes, so the claim in the README matches the app.
const puppeteer = require('puppeteer-core');
const fs = require('fs');
const AXE = fs.readFileSync(require.resolve('axe-core'), 'utf8');

const VIEWS = ['overview','map','compliance','sla','dq','mds',
               'enforce','oneview','verify','export'];
const PAGE = 'file:///home/claude/demo/index.html';

(async () => {
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome',
    args: ['--no-sandbox','--disable-gpu'],
  });
  let totalViolations = 0;
  const rows = [];

  for (const mode of ['light','dark']) {
    for (const view of VIEWS) {
      const page = await browser.newPage();
      await page.setViewport({ width: 1280, height: 1000 });
      await page.goto(PAGE + '#' + view, { waitUntil: 'networkidle0' });
      if (mode === 'dark') {
        // The page keeps its mode on the root element; flip it and re-render.
        await page.evaluate(() => {
          const btn = Array.from(document.querySelectorAll('button'))
            .find(b => /dark|light/i.test(b.textContent));
          if (btn) btn.click();
        });
      }
      await new Promise(r => setTimeout(r, 400));
      await page.evaluate(AXE);
      const res = await page.evaluate(async () => await window.axe.run(document, {
        runOnly: { type: 'tag', values: ['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa'] }
      }));
      const n = res.violations.length;
      totalViolations += n;
      rows.push([mode, view, n, res.violations.map(v => v.id).join(', ')]);
      await page.close();
    }
  }
  await browser.close();

  console.log('mode    view        violations  ids');
  for (const [m, v, n, ids] of rows) {
    console.log(`${m.padEnd(7)} ${v.padEnd(11)} ${String(n).padStart(10)}  ${ids}`);
  }
  console.log('\nviews audited: ' + VIEWS.length + ' in two modes = ' + rows.length + ' page states');
  console.log('total violations: ' + totalViolations);
})();
