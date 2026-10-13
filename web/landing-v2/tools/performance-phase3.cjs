/* Paired Phase 1B / frozen Phase 2 / Phase 3 loopback measurements. */
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs/promises');
const path = require('node:path');
const currentRoot = path.resolve(__dirname, '..');
const allowedArgs = new Set(['--phase3-only']);
for (const arg of process.argv.slice(2)) {
  if (!allowedArgs.has(arg)) throw new Error(`Unknown performance option: ${arg}`);
}
const phase3Only = process.env.V2_PERF_PHASE3_ONLY === '1' || process.argv.includes('--phase3-only');
const roots = {
  phase1b: process.env.V2_PHASE1B_ROOT || 'C:/Users/muzzo/.codex/worktrees/landing-v2-phase1b/whatsapp-ai-responder',
  phase2: process.env.V2_PHASE2_ROOT || 'C:/Users/muzzo/.codex/worktrees/landing-v2-phase-2/whatsapp-ai-responder',
  phase3: currentRoot,
};
const out = path.join(currentRoot, 'artifacts', 'phase3-review');
const widths = [1440, 1024, 768, 390, 360, 320];
const samples = 3;
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const median = values => [...values].sort((a,b) => a-b)[Math.floor(values.length / 2)];
const inlineScriptBytes = html => [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi)].filter(([,attrs]) => !/\bsrc\s*=/.test(attrs)).reduce((sum,[,,script]) => sum + Buffer.byteLength(script,'utf8'),0);
const scriptResources = () => performance.getEntriesByType('resource').filter(r => /\.(?:js|mjs)(?:\?|$)/.test(new URL(r.name).pathname)).map(r => ({url:r.name,bytes:r.encodedBodySize}));

async function launchPreview(root, label) {
  const projectRoot = path.basename(root) === 'landing-v2' ? root : path.join(root, 'web', 'landing-v2');
  const child = spawn(process.execPath, ['tools/preview.mjs'], { cwd: projectRoot, env: { ...process.env, MV2_PORT: '0' }, windowsHide: true, stdio: ['ignore','pipe','pipe'] });
  let logs = ''; child.stdout.on('data', chunk => logs += chunk); child.stderr.on('data', chunk => logs += chunk);
  for (let i=0;i<100;i++) { if(child.exitCode!==null) throw new Error(`${label} preview exited: ${logs}`); const match=logs.match(/Preview V2: http:\/\/127\.0\.0\.1:(\d+)/); if(match) return {child,url:`http://127.0.0.1:${match[1]}/landing-v2/`}; await wait(100); }
  child.kill(); throw new Error(`${label} preview startup timeout: ${logs}`);
}

async function measureViewInteractions(page, width) {
  const times = [];
  const order = ['inbox','bookings','reviews','knowledge','team','overview'];
  const names = {overview:'Panoramica',inbox:'Inbox',bookings:'Prenotazioni',reviews:'Recensioni',knowledge:'Knowledge Base',team:'Team'};
  const nav = page.locator('.mv2-ps-nav-button');
  const select = page.locator('#mv2-ps-view-select');
  for (const id of order) {
    const start = await page.evaluate(() => performance.now());
    if(width<=700) await select.selectOption(id); else await nav.filter({hasText:new RegExp(names[id])}).click();
    await page.getByRole('heading',{name:names[id],exact:true}).waitFor();
    times.push(await page.evaluate(t=>performance.now()-t,start));
  }
  return times.reduce((a,b)=>a+b,0)/times.length;
}

async function visibleHydration(page, selector, scrollSelector) {
  return page.evaluate(({selector,scrollSelector}) => new Promise((resolve,reject) => {
    const control=document.querySelector(selector); if(!control){reject(new Error(`Missing deferred control ${selector}`));return;}
    if(!control.disabled){resolve(0);return;}
    const started=performance.now();
    const observer=new MutationObserver(()=>{if(!control.disabled){clearTimeout(timeout);observer.disconnect();resolve(performance.now()-started);}});
    const timeout=setTimeout(()=>{observer.disconnect();reject(new Error(`${selector} did not hydrate after becoming visible`));},15000);
    observer.observe(control,{attributes:true,attributeFilter:['disabled']});
    document.querySelector(scrollSelector).scrollIntoView({behavior:'instant',block:'center'});
  }),{selector,scrollSelector});
}

async function captureVitals(page) {
  return page.evaluate(() => ({
    lcpMs:window.__lcpMs, layoutShiftSum:window.__layoutShiftSum,
    shifts:window.__shifts,
    resources:performance.getEntriesByType('resource').map(r=>({url:r.name,type:r.initiatorType,bytes:r.encodedBodySize})),
    scripts:performance.getEntriesByType('resource').filter(r=>/\.(?:js|mjs)(?:\?|$)/.test(new URL(r.name).pathname)).map(r=>({url:r.name,bytes:r.encodedBodySize})),
    styles:performance.getEntriesByType('resource').filter(r=>/\.css(?:\?|$)/.test(new URL(r.name).pathname)).map(r=>({url:r.name,bytes:r.encodedBodySize})),
    requests:performance.getEntriesByType('resource').length+1,
    navigation:performance.getEntriesByType('navigation')[0]?.toJSON(),
  }));
}

async function runVariant(browser, server, variant) {
  const widthsReport={};
  for(const width of widths){
    const runs=[];
    for(let sample=0;sample<samples;sample++){
      const context=await browser.newContext({viewport:{width,height:900},deviceScaleFactor:1,reducedMotion:'no-preference'});
      const page=await context.newPage();
      const cdp=await context.newCDPSession(page); await cdp.send('Emulation.setCPUThrottlingRate',{rate:4});
      await page.addInitScript(()=>{
        window.__lcpMs=0;window.__layoutShiftSum=0;window.__shifts=[];
        new PerformanceObserver(list=>{for(const e of list.getEntries())window.__lcpMs=e.startTime;}).observe({type:'largest-contentful-paint',buffered:true});
        new PerformanceObserver(list=>{for(const e of list.getEntries())if(!e.hadRecentInput){window.__layoutShiftSum+=e.value;window.__shifts.push({startTime:e.startTime,value:e.value});}}).observe({type:'layout-shift',buffered:true});
      });
      const response=await page.goto(server.url,{waitUntil:'networkidle'}); await page.evaluate(()=>document.fonts.ready); await wait(750);
      const html=await response.text(); const inlineBytes=inlineScriptBytes(html);
      const initial=await captureVitals(page);
      const initialJs=initial.scripts.reduce((n,r)=>n+r.bytes,0)+inlineBytes;
      let showcaseHydrationMs=null, storyHydrationMs=null, showcaseResponseMs=null, storyResponseMs=null;
      let afterShowcase=initial, afterStory=initial;
      if(variant==='phase1b'){
        showcaseHydrationMs=await visibleHydration(page,'.mv2-control-options button','.mv2-control-options');
        await page.locator('.mv2-control-options button').first().click();
        afterShowcase=afterStory=await captureVitals(page);
      }else{
        const showcaseControl=width<=700?'#mv2-ps-view-select':'.mv2-ps-nav-button';
        await page.locator('.mv2-ps-frame').scrollIntoViewIfNeeded();
        if(variant==='phase2'){
          await page.waitForFunction(()=>!document.querySelector('.mv2-ps-nav-button')?.disabled);
          showcaseHydrationMs=0;
        }else showcaseHydrationMs=await visibleHydration(page,showcaseControl,'.mv2-ps-frame');
        showcaseResponseMs=await measureViewInteractions(page,width);
        afterShowcase=await captureVitals(page);
        afterStory=afterShowcase;
        if([1440,390].includes(width)&&sample===0){await page.evaluate(()=>scrollTo({top:0,behavior:'instant'}));await wait(200);await page.locator(phase3Only?'.mv2-hero-stage':'#mv2-inizio').screenshot({path:path.join(out,phase3Only?`phase3-followup-hero-${width}.png`:`phase2-${variant}-hero-${width}.png`)});}
        if(variant==='phase3'){
          if(width===768&&sample===0) await page.screenshot({path:path.join(out,phase3Only?'phase3-followup-tablet-768.png':'phase3-tablet-768.png')});
          storyHydrationMs=await visibleHydration(page,'#mv2-story-scenario','.mv2-story-demo');
          await page.waitForLoadState('networkidle'); await wait(750);
          afterStory=await captureVitals(page);
          const next=page.getByRole('button',{name:'Avvia la demo'});
          await next.click(); for(let i=0;i<4;i++) await page.getByRole('button',{name:'Passaggio successivo'}).click();
          await page.locator('#mv2-story-scenario').selectOption('unavailable'); await page.getByRole('button',{name:'Avvia la demo'}).click(); await page.getByRole('button',{name:'Passaggio successivo'}).click();
          await page.locator('#mv2-story-scenario').selectOption('team'); await page.getByRole('button',{name:'Avvia la demo'}).click(); await page.getByRole('button',{name:'Passaggio successivo'}).click();
          storyResponseMs=await page.evaluate(()=>performance.now());
        }
      }
      await wait(250);
      const final=await captureVitals(page);
      const size=s=>s.reduce((n,r)=>n+r.bytes,0);
      const seen=new Set(afterShowcase.scripts.map(r=>r.url));
      const storyScripts=variant==='phase3'?afterStory.scripts.filter(r=>!seen.has(r.url)):[];
      const run={
        lcpMs:initial.lcpMs,initialLayoutShiftSum:initial.layoutShiftSum,postInteractionLayoutShiftSum:final.layoutShiftSum,shiftEntries:initial.shifts,
        htmlBytes:Number((await response.allHeaders())['content-length']||Buffer.byteLength(html)),
        cssBytes:size(initial.styles),initialExternalJsBytes:size(initial.scripts),inlineJsBytes:inlineBytes,initialJsBytesIncludingInline:initialJs,
        showcaseJsBytes:size(afterShowcase.scripts),storyIslandJsBytes:size(storyScripts),storyIslandScriptResources:storyScripts,
        totalInteractionJsBytes:size(final.scripts)+inlineBytes,initialRequestCount:initial.requests,afterShowcaseRequestCount:afterShowcase.requests,afterStoryRequestCount:afterStory.requests,totalInteractionRequestCount:final.requests,
        showcaseHydrationMs,storyHydrationMs,showcaseResponseMs,storyScenarioCount:variant==='phase3'?3:0,
        requestResources:final.resources,
      };
      if([1440,768,390].includes(width)&&sample===0&&variant==='phase3'){
        await page.evaluate(()=>scrollTo({top:0,behavior:'instant'}));await wait(200);
        await page.screenshot({path:path.join(out,phase3Only?`phase3-followup-${width}-final-page.png`:`phase3-${width}-final-page.png`)});
      }
      runs.push(run); await context.close();
    }
    const numeric=['lcpMs','initialLayoutShiftSum','postInteractionLayoutShiftSum','htmlBytes','cssBytes','initialExternalJsBytes','inlineJsBytes','initialJsBytesIncludingInline','showcaseJsBytes','storyIslandJsBytes','totalInteractionJsBytes','initialRequestCount','afterShowcaseRequestCount','afterStoryRequestCount','totalInteractionRequestCount','showcaseHydrationMs','storyHydrationMs','showcaseResponseMs'];
    const medians=Object.fromEntries(numeric.map(key=>[key,median(runs.map(run=>run[key]??0))]));
    widthsReport[width]={runs,median:medians};
  }
  return {url:server.url,widths:widthsReport};
}

(async()=>{
  await fs.mkdir(out,{recursive:true});let servers=[];let browser;
  try{
    const selectedRoots = phase3Only ? {phase3:roots.phase3} : roots;
    for(const [name,root] of Object.entries(selectedRoots))servers.push([name,await launchPreview(root,name)]);
    browser=await chromium.launch({headless:true});
    const results={capturedAt:new Date().toISOString(),engine:browser.version(),conditions:{headless:true,viewportWidths:widths,viewportHeight:900,deviceScaleFactor:1,cpuThrottleRate:4,freshContextPerSample:true,samplesPerWidth:samples,await:'networkidle + document.fonts.ready + 750ms quiet',encoding:'loopback Content-Length / Resource Timing encodedBodySize including inline scripts separately, not gzip or field INP'},...(phase3Only?{phase3:await runVariant(browser,servers[0][1],'phase3')}:{phase1b:await runVariant(browser,servers[0][1],'phase1b'),phase2:await runVariant(browser,servers[1][1],'phase2'),phase3:await runVariant(browser,servers[2][1],'phase3')})};
    if(!phase3Only) results.comparison=Object.fromEntries(widths.map(width=>[width,Object.fromEntries(['lcpMs','initialLayoutShiftSum','postInteractionLayoutShiftSum','htmlBytes','cssBytes','initialJsBytesIncludingInline','storyIslandJsBytes','totalInteractionJsBytes','initialRequestCount','afterStoryRequestCount','totalInteractionRequestCount','storyHydrationMs'].map(key=>[key,{phase1b:results.phase1b.widths[width].median[key],phase2:results.phase2.widths[width].median[key],phase3:results.phase3.widths[width].median[key]}]))]));
    const report=path.join(out,phase3Only?'performance-phase3-followup.json':'performance-phase3.json');await fs.writeFile(report,JSON.stringify(results,null,2));
    console.log(JSON.stringify({engine:results.engine,conditions:results.conditions,comparison:results.comparison,report},null,2));
  }finally{if(browser)await browser.close();for(const [,server] of servers)server.child.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
