/* Narrow source trace for post-interaction layout shifts at 390px. */
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs/promises');
const path = require('node:path');
const out = path.join(__dirname, '..', 'artifacts', 'phase3-review');
const roots = {
  phase2: process.env.V2_PHASE2_ROOT || 'C:/Users/muzzo/.codex/worktrees/landing-v2-phase-2/whatsapp-ai-responder',
  phase3: path.resolve(__dirname, '..'),
};
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function start(root,label){
  const project=path.basename(root)==='landing-v2'?root:path.join(root,'web','landing-v2');
  const child=spawn(process.execPath,['tools/preview.mjs'],{cwd:project,env:{...process.env,MV2_PORT:'0'},windowsHide:true,stdio:['ignore','pipe','pipe']});
  let text='';child.stdout.on('data',chunk=>text+=chunk);child.stderr.on('data',chunk=>text+=chunk);
  for(let i=0;i<100;i++){const match=text.match(/Preview V2: http:\/\/127\.0\.0\.1:(\d+)/);if(match)return{child,url:`http://127.0.0.1:${match[1]}/landing-v2/`};if(child.exitCode!==null)throw new Error(`${label}: ${text}`);await pause(100)}
  child.kill();throw new Error(`${label} preview timeout: ${text}`);
}
async function mark(page,name){await page.evaluate(stage=>{window.__stage=stage},name)}
async function snapshot(page,name){
  await page.evaluate(stage=>{window.__stage=stage},name);
  await pause(750);
  return page.evaluate(stage=>({stage,at:performance.now(),scrollY,scrollHeight:document.documentElement.scrollHeight,layoutShiftSum:window.__layoutShiftSum,entries:window.__layoutShifts.filter(item=>item.stage===stage)}),name);
}
async function run(browser,server,label){
  const context=await browser.newContext({viewport:{width:390,height:900},deviceScaleFactor:1,reducedMotion:'no-preference'});
  const page=await context.newPage();const cdp=await context.newCDPSession(page);await cdp.send('Emulation.setCPUThrottlingRate',{rate:4});
  await page.addInitScript(()=>{
    window.__stage='initial';window.__layoutShiftSum=0;window.__layoutShifts=[];window.__lcpMs=0;
    new PerformanceObserver(list=>{for(const entry of list.getEntries())window.__lcpMs=entry.startTime}).observe({type:'largest-contentful-paint',buffered:true});
    new PerformanceObserver(list=>{for(const entry of list.getEntries()){if(entry.hadRecentInput)continue;window.__layoutShiftSum+=entry.value;window.__layoutShifts.push({stage:window.__stage,startTime:entry.startTime,value:entry.value,sources:entry.sources.map(source=>({tag:source.node?.tagName,id:source.node?.id||'',classes:typeof source.node?.className==='string'?source.node.className:'',previous:source.previousRect,current:source.currentRect}))})}}).observe({type:'layout-shift',buffered:true});
  });
  const response=await page.goto(server.url,{waitUntil:'networkidle'});await page.evaluate(()=>document.fonts.ready);await pause(750);
  const html=await response.text();
  const stages=[];await mark(page,'initial-quiet');stages.push(await snapshot(page,'initial-quiet'));
  await mark(page,'showcase-visible');
  await page.locator('.mv2-ps-frame').scrollIntoViewIfNeeded();
  await page.waitForFunction(()=>!document.querySelector('.mv2-ps-nav-button')?.disabled);
  stages.push(await snapshot(page,'showcase-visible'));
  const labels={overview:'Panoramica',inbox:'Inbox',bookings:'Prenotazioni',reviews:'Recensioni',knowledge:'Knowledge Base',team:'Team'};
  await mark(page,'showcase-interacted');
  for(const id of ['inbox','bookings','reviews','knowledge','team','overview']){
    if(label==='phase3')await page.locator('#mv2-ps-view-select').selectOption(id);else await page.locator('#mv2-ps-view-select').selectOption(id);
    await page.getByRole('heading',{name:labels[id],exact:true}).waitFor();
  }
  stages.push(await snapshot(page,'showcase-interacted'));
  if(label==='phase3'){
    await mark(page,'story-visible-hydrated');
    await page.locator('.mv2-story-demo').scrollIntoViewIfNeeded();
    await page.waitForFunction(()=>!document.querySelector('#mv2-story-scenario')?.disabled);
    stages.push(await snapshot(page,'story-visible-hydrated'));
    await mark(page,'story-positive');
    await page.getByRole('button',{name:'Avvia la demo'}).click();for(let i=0;i<4;i++)await page.getByRole('button',{name:'Passaggio successivo'}).click();
    stages.push(await snapshot(page,'story-positive-confirmed'));
    await mark(page,'story-unavailable');
    await page.locator('#mv2-story-scenario').selectOption('unavailable');await page.getByRole('button',{name:'Avvia la demo'}).click();await page.getByRole('button',{name:'Passaggio successivo'}).click();
    stages.push(await snapshot(page,'story-unavailable'));
    await mark(page,'story-team');
    await page.locator('#mv2-story-scenario').selectOption('team');await page.getByRole('button',{name:'Avvia la demo'}).click();await page.getByRole('button',{name:'Passaggio successivo'}).click();
    stages.push(await snapshot(page,'story-team'));
  }
  const result=await page.evaluate(()=>({lcpMs:window.__lcpMs,layoutShiftSum:window.__layoutShiftSum,entries:window.__layoutShifts}));
  await context.close();return{label,url:server.url,htmlBytes:Number(response.headers()['content-length']||Buffer.byteLength(html)),stages,result};
}
(async()=>{
  await fs.mkdir(out,{recursive:true});const servers=[];let browser;
  try{for(const [label,root] of Object.entries(roots))servers.push([label,await start(root,label)]);browser=await chromium.launch({headless:true});
    const report={capturedAt:new Date().toISOString(),engine:browser.version(),conditions:{width:390,height:900,dpr:1,cpuThrottleRate:4,samples:1,quietAfterStageMs:750,shiftMetric:'sum of PerformanceObserver layout-shift entries with hadRecentInput=false; diagnostic only, not canonical session-window CLS'},phase2:await run(browser,servers[0][1],'phase2'),phase3:await run(browser,servers[1][1],'phase3')};
    const file=path.join(out,'phase3-layout-diagnostic-390.json');await fs.writeFile(file,JSON.stringify(report,null,2));console.log(JSON.stringify({file,conditions:report.conditions,phase2:report.phase2.stages,phase3:report.phase3.stages,phase3Entries:report.phase3.result.entries},null,2));
  }finally{if(browser)await browser.close();for(const [,server] of servers)server.child.kill()}
})().catch(error=>{console.error(error);process.exit(1)});
