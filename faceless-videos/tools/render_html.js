// Render an HTML or SVG file to PNG with headless Chromium.
// usage: node render_html.js <in.html|in.svg> <out.png> <width> <height> [--transparent]
const path = require('path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

(async () => {
  const [input, out, w, h, flag] = process.argv.slice(2);
  const width = parseInt(w, 10);
  const height = parseInt(h, 10);
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width, height } });
  await page.goto('file://' + path.resolve(input));
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({
    path: out,
    omitBackground: flag === '--transparent',
    clip: { x: 0, y: 0, width, height },
  });
  await browser.close();
})();
