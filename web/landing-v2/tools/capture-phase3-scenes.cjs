/* Captures full-section, rendered scene evidence and a short manual walkthrough. */
const { chromium } = require('playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const out = path.join(__dirname, '..', 'artifacts', 'phase3-review');
const url = process.env.V2_QA_URL || 'http://127.0.0.1:4176/landing-v2/';
if (!['127.0.0.1','localhost','[::1]'].includes(new URL(url).hostname)) throw new Error('Capture accepts loopback previews only.');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function show(page, scenario) {
  await page.locator('#mv2-story-scenario').selectOption(scenario);
  const count = scenario === 'available' ? 4 : 1;
  await page.getByRole('button', { name: 'Avvia la demo' }).click();
  for (let i=0;i<count;i++) {
    await page.getByRole('button', { name: 'Passaggio successivo' }).click();
    await pause(350);
  }
}

(async()=>{
  await fs.mkdir(out,{recursive:true});
  const browser=await chromium.launch({headless:true});
  try {
    for(const width of [1440,390]){
      const page=await browser.newPage({viewport:{width,height:1000},deviceScaleFactor:1});
      await page.goto(url,{waitUntil:'networkidle'});await page.evaluate(()=>document.fonts.ready);
      const demo=page.locator('.mv2-story-demo');await demo.scrollIntoViewIfNeeded();
      await page.waitForFunction(()=>!document.querySelector('#mv2-story-scenario')?.disabled);
      for(const scenario of ['available','unavailable','team']){
        if(scenario==='available') await page.locator('#mv2-story-scenario').selectOption(scenario);
        await show(page,scenario);
        await page.locator('#mv2-percorso').screenshot({path:path.join(out,`phase3-followup-story-${scenario}-${width}-full.png`)});
      }
      await page.close();
    }
    const context=await browser.newContext({viewport:{width:1280,height:1000},recordVideo:{dir:out,size:{width:1280,height:1000}}});
    const page=await context.newPage();const video=page.video();
    await page.goto(url,{waitUntil:'networkidle'});await page.evaluate(()=>document.fonts.ready);
    await page.locator('.mv2-story-demo').scrollIntoViewIfNeeded();await page.waitForFunction(()=>!document.querySelector('#mv2-story-scenario')?.disabled);
    for(const scenario of ['available','unavailable','team']){await show(page,scenario);await pause(600);}
    await context.close();
    await video.saveAs(path.join(out,'phase3-followup-story-walkthrough.webm'));
  } finally { await browser.close(); }
  console.log(`Full story captures saved in ${out}`);
})().catch(error=>{console.error(error);process.exit(1);});
