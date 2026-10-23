/* Optional developer UI check; uses an existing Playwright + Chromium install.
 * Input is a loopback server containing ONLY report_demo's fictional HTML.
 * No browser dependency is added to P1 or the end-user Windows environment.
 */
const {chromium} = require('playwright');
const fs = require('fs');
(async () => {
  const [input, output] = process.argv.slice(2);
  const url = new URL(input);
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname)) throw Error('Use fictional loopback preview only');
  fs.mkdirSync(output, {recursive:true});
  const browser = await chromium.launch({executablePath:process.env.P1_BROWSER_EXECUTABLE || '/usr/bin/chromium', headless:true, args:['--no-sandbox']});
  const results = [];
  try {
    for (const [name,width,height] of [['desktop',1440,1100],['iphone',390,844],['small',320,760],['wide-phone',430,932]]) {
      const context = await browser.newContext({viewport:{width,height},deviceScaleFactor:1,isMobile:width<600,hasTouch:width<600});
      const page = await context.newPage();
      const outbound = [], errors = [];
      page.on('request', r => {if(/^https?:/.test(r.url()) && new URL(r.url()).origin !== url.origin) outbound.push(r.url());});
      page.on('pageerror', e => errors.push(e.message));
      await page.goto(input);
      if (!await page.locator('.demo').isVisible()) throw Error('Only explicitly fictional screenshots allowed');
      await context.setOffline(true);
      const visible = await page.locator('main').innerText();
      const layout = await page.evaluate(() => ({width:innerWidth,scroll:document.documentElement.scrollWidth,advancedOpen:document.querySelector('#advanced').open}));
      if (layout.scroll > layout.width || layout.advancedOpen || outbound.length || errors.length) throw Error(JSON.stringify({name,layout,outbound,errors}));
      if (/response_sha256|null|\[\]/.test(visible)) throw Error('Raw fields visible outside advanced');
      if (name === 'desktop') await page.screenshot({path:output+'/desktop.png',fullPage:true});
      if (name === 'iphone') await page.screenshot({path:output+'/iphone-home.png'});
      await page.locator('nav a[href="#holdings"]').click();
      if (!page.url().endsWith('#holdings')) throw Error('Anchor navigation failed');
      // Disable smooth scrolling only for a deterministic screenshot.
      await page.locator('#holdings').evaluate(e => e.scrollIntoView({behavior:'instant'}));
      if (name === 'iphone') await page.screenshot({path:output+'/iphone-holdings.png'});
      await page.locator('#advanced>summary').click();
      if (!await page.locator('#advanced pre').first().isVisible()) throw Error('Details did not open');
      await page.locator('#advanced>summary').focus();
      await page.keyboard.press('Enter');
      if (await page.locator('#advanced').evaluate(e=>e.open)) throw Error('Keyboard close failed');
      results.push({name,...layout,externalRequests:outbound.length,pageErrors:errors.length,detailsAndKeyboard:true});
      await context.close();
    }
    console.log(JSON.stringify(results));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
