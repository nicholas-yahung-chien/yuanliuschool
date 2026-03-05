#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';
import { PNG } from 'pngjs';
import pixelmatch from 'pixelmatch';

function arg(name, def) {
  const idx = process.argv.indexOf(`--${name}`);
  if (idx === -1) return def;
  return process.argv[idx + 1] ?? def;
}

function parseCsvRoutes(csvText) {
  const lines = csvText.split(/\r?\n/).filter(Boolean);
  if (lines.length < 2) return [];
  const header = lines[0].split(',');
  const routeIdx = header.indexOf('route');
  if (routeIdx < 0) return [];
  const routes = [];
  for (let i = 1; i < lines.length; i += 1) {
    const cols = lines[i].split(',');
    if (cols[routeIdx]) routes.push(cols[routeIdx]);
  }
  return [...new Set(routes)].sort();
}

function slug(route) {
  if (route === '/') return 'index';
  return route.replace(/^\//, '').replace(/[\\/:*?"<>|]/g, '_');
}

function sampleRoutes(routes, n) {
  if (routes.length <= n) return routes;
  const picked = [];
  for (let i = 0; i < n; i += 1) {
    const idx = Math.floor((i * routes.length) / n);
    picked.push(routes[idx]);
  }
  return [...new Set(picked)];
}

async function capture(page, url, filePath) {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.addStyleTag({
    content: `
      * { animation: none !important; transition: none !important; }
      [data-aid="FOOTER_COOKIE_BANNER_RENDERED"],
      [data-aid="MESSAGING_FAB"],
      [id^="freemium-ad-"],
      .widget-popup,
      .widget-cookie-banner,
      .widget-messaging {
        display: none !important;
        visibility: hidden !important;
      }
    `,
  });
  await page.waitForTimeout(1200);
  await page.screenshot({ path: filePath, fullPage: false });
}

function diffRatio(aPath, bPath, outPath) {
  const imgA = PNG.sync.read(fs.readFileSync(aPath));
  const imgB = PNG.sync.read(fs.readFileSync(bPath));

  const width = Math.min(imgA.width, imgB.width);
  const height = Math.min(imgA.height, imgB.height);

  const cropA = new PNG({ width, height });
  const cropB = new PNG({ width, height });

  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const i = (y * width + x) << 2;
      const ia = (y * imgA.width + x) << 2;
      const ib = (y * imgB.width + x) << 2;
      cropA.data[i] = imgA.data[ia];
      cropA.data[i + 1] = imgA.data[ia + 1];
      cropA.data[i + 2] = imgA.data[ia + 2];
      cropA.data[i + 3] = imgA.data[ia + 3];
      cropB.data[i] = imgB.data[ib];
      cropB.data[i + 1] = imgB.data[ib + 1];
      cropB.data[i + 2] = imgB.data[ib + 2];
      cropB.data[i + 3] = imgB.data[ib + 3];
    }
  }

  const diff = new PNG({ width, height });
  const changed = pixelmatch(cropA.data, cropB.data, diff.data, width, height, {
    threshold: 0.12,
    includeAA: true,
  });

  fs.writeFileSync(outPath, PNG.sync.write(diff));

  const areaA = imgA.width * imgA.height;
  const areaB = imgB.width * imgB.height;
  const maxArea = Math.max(areaA, areaB);
  const overlapArea = width * height;
  const sizePenalty = maxArea - overlapArea;
  return {
    diffRatio: overlapArea === 0 ? 1 : changed / overlapArea,
    sizeDeltaRatio: maxArea === 0 ? 1 : sizePenalty / maxArea,
  };
}

async function main() {
  const sourceBase = arg('source-base', 'https://yuanliuschool.com');
  const targetBase = arg('target-base', 'http://127.0.0.1:4173');
  const pagesCsv = arg('pages-csv', 'site/_meta/pages.csv');
  const outputDir = arg('output-dir', 'reports/visual');
  const sampleSize = Number(arg('sample', '10'));
  const maxDiff = Number(arg('max-diff-ratio', '0.2'));
  const maxSizeDelta = Number(arg('max-size-delta-ratio', '0.6'));

  const csv = fs.readFileSync(pagesCsv, 'utf-8');
  const routes = sampleRoutes(parseCsvRoutes(csv), sampleSize);

  const devices = [
    { name: 'desktop', viewport: { width: 1440, height: 2200 } },
    { name: 'mobile', viewport: { width: 390, height: 844 } },
  ];

  fs.mkdirSync(outputDir, { recursive: true });

  const browser = await chromium.launch({ headless: true });
  const results = [];

  try {
    for (const device of devices) {
      const srcDir = path.join(outputDir, 'source', device.name);
      const tgtDir = path.join(outputDir, 'target', device.name);
      const diffDir = path.join(outputDir, 'diff', device.name);
      fs.mkdirSync(srcDir, { recursive: true });
      fs.mkdirSync(tgtDir, { recursive: true });
      fs.mkdirSync(diffDir, { recursive: true });

      for (const route of routes) {
        const name = slug(route);
        const srcPath = path.join(srcDir, `${name}.png`);
        const tgtPath = path.join(tgtDir, `${name}.png`);
        const diffPath = path.join(diffDir, `${name}.png`);

        const context = await browser.newContext({ viewport: device.viewport, locale: 'zh-TW' });
        const page = await context.newPage();

        try {
          await capture(page, `${sourceBase}${route}`, srcPath);
          await capture(page, `${targetBase}${route}`, tgtPath);
          const ratio = diffRatio(srcPath, tgtPath, diffPath);
          const status = ratio.diffRatio > maxDiff || ratio.sizeDeltaRatio > maxSizeDelta ? 'fail' : 'pass';
          results.push({
            route,
            device: device.name,
            diffRatio: ratio.diffRatio,
            sizeDeltaRatio: ratio.sizeDeltaRatio,
            status,
          });
          console.log(
            `${device.name} ${route} diff=${ratio.diffRatio.toFixed(4)} sizeDelta=${ratio.sizeDeltaRatio.toFixed(4)}`
          );
        } catch (err) {
          results.push({ route, device: device.name, diffRatio: 1, status: 'error', error: String(err) });
          console.error(`${device.name} ${route} error`, err.message || err);
        } finally {
          await context.close();
        }
      }
    }
  } finally {
    await browser.close();
  }

  const failed = results.filter((r) => r.status !== 'pass');
  const report = {
    sourceBase,
    targetBase,
    sampleSize: routes.length,
    maxDiffRatio: maxDiff,
    maxSizeDeltaRatio: maxSizeDelta,
    totalChecks: results.length,
    failed: failed.length,
    results,
  };

  fs.writeFileSync(path.join(outputDir, 'visual-report.json'), JSON.stringify(report, null, 2));

  const lines = [
    '# Visual Comparison Report',
    '',
    `- Source: ${sourceBase}`,
    `- Target: ${targetBase}`,
    `- Sample routes: ${routes.length}`,
    `- Total checks (desktop+mobile): ${results.length}`,
    `- Failed: ${failed.length}`,
    `- Threshold: ${maxDiff}`,
    '',
    '| Route | Device | Diff Ratio | Size Delta | Status |',
    '|---|---|---:|---:|---|',
  ];

  for (const r of results) {
    lines.push(
      '| `' +
        r.route +
        '` | ' +
        r.device +
        ' | ' +
        r.diffRatio.toFixed(4) +
        ' | ' +
        (r.sizeDeltaRatio ?? 0).toFixed(4) +
        ' | ' +
        r.status +
        ' |'
    );
  }

  fs.writeFileSync(path.join(outputDir, 'visual-report.md'), `${lines.join('\n')}\n`);

  if (failed.length > 0) process.exit(1);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
