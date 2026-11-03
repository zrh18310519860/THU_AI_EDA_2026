const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const port = process.argv[2] || '8001';
  const run = process.argv[3];
  const out = process.argv[4] || 'F:/AI_EDA/stability-evidence-20261027/ui';
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  const page = await browser.newPage({ viewport: { width: 1560, height: 1050 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'networkidle' });
  await page.locator('[data-main-tab="agent"]').click();
  await page.locator('[data-project-id="rv32i5_ooo"]').click();
  await page.locator('#agent-nodes .agent-node').first().waitFor();
  if (run) {
    await page.locator(`tr[title="Load ${run}"]`).click();
    await page.locator('#agent-events .agent-event').last().waitFor();
  }
  await page.waitForFunction(() => !document.querySelector('#toast-stack .toast'));
  await page.locator('.agent-canvas-panel').screenshot({ path: path.join(out, 'agent-flow.png') });
  await page.locator('.agent-compare-panel').screenshot({ path: path.join(out, 'run-history.png') });
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({ path: path.join(out, 'workbench-desktop.png') });
  await page.locator('#agent-nodes .agent-node:not(.fixed-node)').first().click();
  await page.locator('#agent-run-config-dialog').screenshot({ path: path.join(out, 'run-configuration.png') });
  const mode = await page.locator('#agent-l3-mode').inputValue();
  if (mode !== 'fresh') throw new Error('Incorrect default L3 mode');
  if (await page.locator('#agent-l3-mode option').count() !== 1) throw new Error('Built-in reuse is still offered');
  await page.locator('#agent-run-config-close').click();
  const desktop = await page.evaluate(() => ({ width: innerWidth, bodyWidth: document.documentElement.scrollWidth }));
  const count = await page.locator('#agent-nodes .agent-node:not(.fixed-node)').count();
  await page.setViewportSize({ width: 430, height: 932 });
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({ path: path.join(out, 'workbench-mobile.png') });
  const mobile = await page.evaluate(() => ({ width: innerWidth, bodyWidth: document.documentElement.scrollWidth }));
  const requestSeen = new Promise(resolve => {
    page.route('**/api/agent/jobs', async route => {
      const body = route.request().postDataJSON();
      await route.fulfill({ status: 400, contentType: 'application/json',
                            body: JSON.stringify({ error: 'UI request capture only; no model call made.' }) });
      resolve(body);
    });
  });
  await page.locator('#agent-run-button').click();
  const normalRunRequest = await requestSeen;
  if (normalRunRequest.l3_mode !== 'fresh' || normalRunRequest.resume_from !== null)
    throw new Error('Normal Run Agent still seeds a previous implementation');
  const result = { nodes: count, desktop, mobile, errors, run, mode, normalRunRequest };
  fs.writeFileSync(path.join(out, 'ui-check.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
  await browser.close();
  if (errors.length || count !== 6) process.exitCode = 1;
})().catch(error => { console.error(error); process.exitCode = 1; });
