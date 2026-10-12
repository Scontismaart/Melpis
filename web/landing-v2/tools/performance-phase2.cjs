/* Compare the frozen Phase 1B tree with Phase 2 under the same local browser conditions. */
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs/promises');
const path = require('node:path');
const currentRoot = path.resolve(__dirname, '..');
const phase1bRoot = process.env.V2_PHASE1B_ROOT;
if (!phase1bRoot) throw new Error('Set V2_PHASE1B_ROOT to the clean Phase 1B worktree root.');
const out = path.join(currentRoot, 'artifacts', 'phase2-review');
const widths = [1440, 1024, 768, 390, 360, 320];
const samples = 3;
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const median = values => [...values].sort((a,b) => a-b)[Math.floor(values.length / 2)];
const inlineScriptBytes = html => [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi)].filter(([,attrs]) => !/\bsrc\s*=/.test(attrs)).reduce((sum,[,,script]) => sum + Buffer.byteLength(script,'utf8'),0);

async function launchPreview(root, label) {
  const projectRoot=path.basename(root)==='landing-v2'?root:path.join(root,'web','landing-v2');
  const child = spawn(process.execPath, ['tools/preview.mjs'], { cwd: projectRoot, env: { ...process.env, MV2_PORT: '0' }, windowsHide: true, stdio: ['ignore','pipe','pipe'] });
  let logs = '';
  child.stdout.on('data', chunk => logs += chunk); child.stderr.on('data', chunk => logs += chunk);
  for (let i=0;i<100;i++) { if (child.exitCode !== null) throw new Error(`${label} preview exited: ${logs}`); const match=logs.match(/Preview V2: http:\/\/127\.0\.0\.1:(\d+)/); if(match) return { child, url:`http://127.0.0.1:${match[1]}/landing-v2/` }; await wait(100); }
  child.kill(); throw new Error(`${label} preview startup timeout: ${logs}`);
}

function sampleSummary(runs) {
  const keys=['lcpMs','cls','htmlBytes','cssBytes','initialExternalJsBytes','initialJsBytesIncludingInline','postInteractionJsBytes','initialRequestCount','postInteractionRequestCount','hydrationFromNavigationMs','visibleToEnabledMs','meanViewResponseMs'];
  return Object.fromEntries(keys.map(key=>[key,median(runs.map(run=>run[key]))]));
}

async function runVariant(browser, server, variant) {
  const widthsReport={};
  for (const width of widths) {
    const runs=[];
    for (let sample=0;sample<samples;sample++) {
      const context=await browser.newContext({viewport:{width,height:900},deviceScaleFactor:1,reducedMotion:'no-preference'});
      const page=await context.newPage();
      const cdp=await context.newCDPSession(page);
      await cdp.send('Emulation.setCPUThrottlingRate',{rate:4});
      await page.addInitScript(()=>{
        window.__lcpMs=0; window.__cls=0; window.__shifts=[]; window.__readyMs=null;
        new PerformanceObserver(list=>{for(const entry of list.getEntries())window.__lcpMs=entry.startTime;}).observe({type:'largest-contentful-paint',buffered:true});
        new PerformanceObserver(list=>{for(const entry of list.getEntries())if(!entry.hadRecentInput){window.__cls+=entry.value;window.__shifts.push({startTime:entry.startTime,value:entry.value,sources:entry.sources.map(source=>({node:source.node?.tagName,className:typeof source.node?.className==='string'?source.node.className:'',id:source.node?.id||'',previousRect:source.previousRect,currentRect:source.currentRect}))});}}).observe({type:'layout-shift',buffered:true});
        const ready=()=>{const target=document.querySelector('.mv2-control-options button,.mv2-ps-nav-button');if(target&&!target.disabled){window.__readyMs=performance.now();return true;}return false;};
        const observer=new MutationObserver(()=>{if(ready())observer.disconnect();});observer.observe(document,{subtree:true,childList:true,attributes:true,attributeFilter:['disabled']});
        if(ready())observer.disconnect();
      });
      const response=await page.goto(server.url,{waitUntil:'networkidle'});
      await page.evaluate(()=>document.fonts.ready);
      await wait(750);
      const html=await response.text();
      const initial=await page.evaluate(()=>{
        const resources=performance.getEntriesByType('resource').map(r=>({url:r.name,type:r.initiatorType,bodyBytes:r.encodedBodySize}));
        const scripts=resources.filter(r=>/\.(?:js|mjs)(?:\?|$)/.test(new URL(r.url).pathname));
        const styles=resources.filter(r=>/\.css(?:\?|$)/.test(new URL(r.url).pathname));
        const nav=performance.getEntriesByType('navigation')[0];
        return {lcpMs:window.__lcpMs,cls:window.__cls,shiftEntries:window.__shifts,resources,scripts,styles,requestCount:resources.length+1,domContentLoadedMs:nav?.domContentLoadedEventEnd||0,loadMs:nav?.loadEventEnd||0,readyMs:window.__readyMs};
      });
      const htmlBytes=Number((await response.allHeaders())['content-length']||Buffer.byteLength(html));
      const inlineBytes=inlineScriptBytes(html);
      let visibleToEnabledMs=null,meanViewResponseMs=null;
      if(variant==='phase1b'){
        const button=page.locator('.mv2-control-options button').first();
        visibleToEnabledMs=await page.evaluate(()=>new Promise((resolve,reject)=>{
          const b=document.querySelector('.mv2-control-options button');
          if(!b){reject(new Error('Phase 1B controls are missing'));return;}
          const started=performance.now();
          if(!b.disabled){resolve(0);return;}
          const observer=new MutationObserver(()=>{if(!b.disabled){clearTimeout(timeout);observer.disconnect();resolve(performance.now()-started);}});
          const timeout=setTimeout(()=>{observer.disconnect();reject(new Error('Phase 1B controls did not hydrate within 15 seconds of becoming visible'));},15000);
          observer.observe(b,{attributes:true,attributeFilter:['disabled']});
          document.querySelector('.mv2-control-options').scrollIntoView({behavior:'instant',block:'center'});
        }));
        await button.click();
      } else {
        const selector=page.locator('#mv2-ps-view-select');
        const nav=page.locator('.mv2-ps-nav-button');
        await page.locator('.mv2-ps-frame').scrollIntoViewIfNeeded();
        await page.waitForFunction(()=>{const b=document.querySelector('.mv2-ps-nav-button');return b&&!b.disabled;});
        // All six Phase 2 controls hydrate above the fold during the same quiet interval.
        // After the user scrolls the showcase into view, controls are already enabled.
        visibleToEnabledMs=0;
        const times=[];
        for(const id of ['inbox','bookings','reviews','knowledge','team','overview']){
          const start=await page.evaluate(()=>performance.now());
          if(width<=700) await selector.selectOption(id); else await nav.filter({hasText:new RegExp(id==='overview'?'Panoramica':id==='inbox'?'Inbox':id==='bookings'?'Prenotazioni':id==='reviews'?'Recensioni':id==='knowledge'?'Knowledge Base':'Team')}).click();
          const title={overview:'Panoramica',inbox:'Inbox',bookings:'Prenotazioni',reviews:'Recensioni',knowledge:'Knowledge Base',team:'Team'}[id];
          await page.getByRole('heading',{name:title,exact:true}).waitFor();
          times.push(await page.evaluate(t=>performance.now()-t,start));
        }
        meanViewResponseMs=times.reduce((a,b)=>a+b,0)/times.length;
      }
      await wait(500);
      const after=await page.evaluate(()=>{
        const resources=performance.getEntriesByType('resource').map(r=>({url:r.name,type:r.initiatorType,bodyBytes:r.encodedBodySize}));
        const scripts=resources.filter(r=>/\.(?:js|mjs)(?:\?|$)/.test(new URL(r.url).pathname));
        return {scripts,requestCount:resources.length+1,cls:window.__cls,shiftEntries:window.__shifts};
      });
      const initialExternalJsBytes=initial.scripts.reduce((n,r)=>n+r.bodyBytes,0);
      const afterExternalJsBytes=after.scripts.reduce((n,r)=>n+r.bodyBytes,0);
      const run={lcpMs:initial.lcpMs,cls:initial.cls,shiftEntries:initial.shiftEntries,htmlBytes,cssBytes:initial.styles.reduce((n,r)=>n+r.bodyBytes,0),initialExternalJsBytes,initialJsBytesIncludingInline:initialExternalJsBytes+inlineBytes,postInteractionJsBytes:afterExternalJsBytes+inlineBytes,initialRequestCount:initial.requestCount,postInteractionRequestCount:after.requestCount,hydrationFromNavigationMs:initial.readyMs,visibleToEnabledMs,meanViewResponseMs,inlineJsBytes:inlineBytes,postInteractionCls:after.cls,postInteractionShiftEntries:after.shiftEntries};
      runs.push(run);
      if(sample===0 && [1440,768,390].includes(width)){await page.evaluate(()=>scrollTo({top:0,behavior:'instant'}));await wait(200);await page.screenshot({path:path.join(out,`phase2-${variant}-${width}-perf.png`)});}
      await context.close();
    }
    widthsReport[width]={runs,median:sampleSummary(runs)};
  }
  return {url:server.url,widths:widthsReport};
}

(async()=>{
  await fs.mkdir(out,{recursive:true});
  let baseServer,currentServer,browser;
  try{
    baseServer=await launchPreview(phase1bRoot,'Phase 1B');currentServer=await launchPreview(currentRoot,'Phase 2');
    browser=await chromium.launch({headless:true});
    const results={capturedAt:new Date().toISOString(),engine:browser.version(),conditions:{headless:true,viewportHeight:900,deviceScaleFactor:1,cpuThrottleRate:4,freshContextPerSample:true,samplesPerWidth:samples,await:'networkidle + document.fonts.ready + 750 ms quiet',encoding:'loopback body bytes via Content-Length and Resource Timing, not wire estimates'},phase1b:await runVariant(browser,baseServer,'phase1b'),phase2:await runVariant(browser,currentServer,'phase2')};
    results.comparison=Object.fromEntries(widths.map(width=>{
      const before=results.phase1b.widths[width].median,after=results.phase2.widths[width].median;
      return [width,Object.fromEntries(['lcpMs','cls','htmlBytes','cssBytes','initialExternalJsBytes','initialJsBytesIncludingInline','postInteractionJsBytes','initialRequestCount','postInteractionRequestCount','hydrationFromNavigationMs','visibleToEnabledMs','meanViewResponseMs'].map(key=>[key,{phase1b:before[key],phase2:after[key],delta:typeof before[key]==='number'&&typeof after[key]==='number'?after[key]-before[key]:null}]))];
    }));
    const file=path.join(out,'performance-phase2.json');await fs.writeFile(file,JSON.stringify(results,null,2));
    console.log(JSON.stringify({engine:results.engine,conditions:results.conditions,comparison:results.comparison,report:file},null,2));
  }finally{if(browser)await browser.close();baseServer?.child.kill();currentServer?.child.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
