const {chromium}=require('playwright');
const assert=require('node:assert/strict');
async function installSpeech(page) {
 await page.addInitScript(()=>{
  window.testMics=[];window.testSpoken=[];
  class Mic {
   constructor(){window.testMics.push(this);}
   start(){this.active=true;this.onstart?.();}
   abort(){this.active=false;this.onend?.();}
   words(text){const r=[{transcript:text}];r.isFinal=true;this.onresult?.({results:[r]});}
  }
  class Synth extends EventTarget {
   getVoices(){return [{name:'US default',lang:'en-US',voiceURI:'us'}, {name:'Microsoft George',lang:'en-GB',voiceURI:'gb'}];}
   speak(u){window.testSpoken.push(u);}
   cancel(){}
  }
  Object.defineProperty(window,'SpeechRecognition',{value:Mic,configurable:true});
  Object.defineProperty(window,'webkitSpeechRecognition',{value:Mic,configurable:true});
  Object.defineProperty(window,'speechSynthesis',{value:new Synth(),configurable:true});
  Object.defineProperty(window,'SpeechSynthesisUtterance',{value:class {constructor(text){this.text=text;}},configurable:true});
 });
}
async function installAPI(page) {
 const messages=[], pending=[], prompts=[], decisions=[];let workflow=null,actions=[];
 const stamp=new Date().toISOString();
 await page.route('**/api/**',async route=>{
  const req=route.request(), path=new URL(req.url()).pathname.slice(4), method=req.method();
  let data;
  if(path==='/status') data={ok:true,settings:{provider_ready:true,provider_url:'http://localhost:11434/v1',model:'browser-test-fixture',roots:['/tmp'],name:'Owen',has_api_key:false,native_notifications:false,data_dir:'/tmp/voice-test'},drives:[],active:[]};
  else if(path==='/conversations'&&method==='POST') data={id:'c1',title:'New conversation',created_at:stamp,updated_at:stamp};
  else if(path==='/conversations') data=messages.length?[{id:'c1',title:'Voice test',created_at:stamp,updated_at:stamp}]:[];
  else if(path==='/conversations/c1') data={messages,approvals:pending,active:false,workflow,actions};
  else if(path==='/conversations/c1/chat'){
   const content=req.postDataJSON().content;prompts.push(content);messages.push({id:'u'+messages.length,role:'user',content});
   workflow={id:'w'+prompts.length,status:'completed',request:content,plan:['Find the application','Open its window']};
   actions=[{id:'step1',workflow_id:workflow.id,name:'search_applications',status:'completed'},{id:'step2',workflow_id:workflow.id,name:'launch_application',status:'completed'}];
   const activity=[{type:'workflow',id:workflow.id,status:'running'},{type:'plan',outcome:content,steps:workflow.plan},...actions.flatMap(a=>[{type:'action',action:{...a,status:'running'}},{type:'tool',action_id:a.id,name:a.name,ok:true,status:'completed'}])];
   if(content==='Open Calculator'){
    const approval={id:'a1',conversation_id:'c1',name:'open_application',arguments:{name:'calculator'},preview:{summary:'Open Calculator'}};
    pending.push(approval);workflow.status='awaiting_confirmation';actions[1].status='awaiting_confirmation';
    await route.fulfill({contentType:'text/event-stream',body:[...activity,{type:'action',action:actions[1]},{type:'approval',approval},{type:'done'}].map(e=>'data: '+JSON.stringify(e)+'\n\n').join('')});return;
   }
   const text=prompts.length===1?'Good evening, Owen. How can I help?':'Of course. Let us plan your day.';
   messages.push({id:'a'+messages.length,role:'assistant',content:text});
   await route.fulfill({contentType:'text/event-stream',body:[...activity,{type:'token',text},{type:'message',content:text},{type:'done'}].map(e=>'data: '+JSON.stringify(e)+'\n\n').join('')});return;
  }
  else if(path==='/approvals/a1'){
   decisions.push(req.postDataJSON().approved);pending.length=0;workflow.status='completed';actions[1].status='declined';const text='The action was declined.';messages.push({id:'a'+messages.length,role:'assistant',content:text});
   await route.fulfill({contentType:'text/event-stream',body:[{type:'message',content:text},{type:'done'}].map(e=>'data: '+JSON.stringify(e)+'\n\n').join('')});return;
  }
  else if(path==='/activity') data={active:[],workflows:workflow?[workflow]:[],actions:actions.slice().reverse(),emergency_hotkey:false};
  else data=[];
  await route.fulfill({json:data});
 });
 return {prompts,decisions};
}
const phase=(page,name)=>page.waitForFunction(name=>document.body.dataset.voicePhase===name,name);
const words=(page,text)=>page.evaluate(text=>window.testMics.at(-1).words(text),text);
const finish=(page)=>page.evaluate(()=>window.testSpoken.at(-1).onend());
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.JARVIS_TEST_BROWSER || '/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await installSpeech(page);const api=await installAPI(page);
  await page.goto(process.env.JARVIS_TEST_URL || 'http://127.0.0.1:8765');await page.waitForFunction(()=>document.querySelector('#model-status').textContent.includes('browser-test-fixture'));
  assert.equal(await page.locator('.hud-instrument').isVisible(),true);
  const before=await page.locator('.hud-rotate').evaluate(el=>getComputedStyle(el).transform);await page.waitForTimeout(150);
  assert.notEqual(await page.locator('.hud-rotate').evaluate(el=>getComputedStyle(el).transform),before,'idle rings must move');
  await page.screenshot({path:'/tmp/jarvis-hud-desktop.png',fullPage:true});
  await page.evaluate(()=>{
   window.observedActions=[];
   new MutationObserver(records=>{for(const record of records)for(const node of record.addedNodes){if(node.nodeType!==1)continue;const actions=[...(node.matches?.('.flow-action')?[node]:[]),...node.querySelectorAll('.flow-action')];window.observedActions.push(...actions.map(el=>el.dataset.status));}}).observe(document.querySelector('#flow-rail'),{childList:true,subtree:true});
  });
  await page.locator('#jarvis-hud').click();await phase(page,'listening');
  await words(page,'Hello JARVIS');await phase(page,'speaking');assert.deepEqual(api.prompts,['Hello JARVIS']);
  assert.equal(await page.evaluate(()=>window.testSpoken[0].voice.voiceURI),'gb');
  assert.equal(await page.evaluate(()=>window.testMics.length),1);
  await page.waitForFunction(()=>document.querySelector('#flow-rail .flow-action[data-action-id="step2"][data-status="completed"]'));
  assert.equal(await page.locator('#jarvis-hud').getAttribute('data-phase'),'speaking');
  await page.evaluate(()=>window.testSpoken.at(-1).onboundary({name:'word'}));await page.waitForTimeout(60);
  assert.ok(Number(await page.locator('.hud-instrument').evaluate(el=>el.style.getPropertyValue('--hud-energy')))>0);
  assert.ok(await page.locator('#flow-rail .flow-edge').count()>=3);
  assert.ok(await page.evaluate(()=>window.observedActions.includes('running')),'live action events must render before completion');
  await page.screenshot({path:'/tmp/jarvis-hud-working.png',fullPage:true});
  await words(page,'This is stale speaker audio');await page.waitForTimeout(1300);assert.equal(api.prompts.length,1);
  await finish(page);await phase(page,'listening');
  await words(page,'Plan my day');await phase(page,'speaking');assert.equal(api.prompts.length,2);
  await finish(page);await phase(page,'listening');
  await page.screenshot({path:'/tmp/jarvis-handsfree-desktop.png',fullPage:true});
  await words(page,'Open Calculator');await phase(page,'speaking');await page.locator('.approval-card').waitFor();
  await finish(page);await phase(page,'approval');assert.equal(await page.locator('#flow-rail .flow-action[data-status="awaiting_confirmation"]').count(),1);assert.equal(await page.evaluate(()=>window.testMics.at(-1).active),false);assert.deepEqual(api.decisions,[]);
  await words(page,'Approve action');await page.waitForTimeout(1300);assert.deepEqual(api.decisions,[]);
  await page.locator('[data-decision="false"]').click();await phase(page,'speaking');assert.deepEqual(api.decisions,[false]);await finish(page);await phase(page,'listening');
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:'/tmp/jarvis-handsfree-mobile.png',fullPage:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.locator('#voice-stop').click();await phase(page,'off');
  await page.locator('#menu-button').click();await page.locator('[data-view="activity"]').click();
  await page.waitForFunction(()=>!document.querySelector('#activity-view').hidden);
  assert.equal(await page.locator('#activity-flow .flow-action').count(),2);assert.ok(await page.locator('#activity-flow .flow-edge').count()>=3);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.screenshot({path:'/tmp/jarvis-hud-mobile-flow.png',fullPage:true});
  await page.locator('#menu-button').click();await page.locator('[data-view="chat"]').click();await page.locator('#jarvis-hud').click();await phase(page,'listening');
  await words(page,'Stop listening');await phase(page,'off');assert.equal(api.prompts.length,3);
  await page.setViewportSize({width:1440,height:1000});await page.locator('[data-view="settings"]').click();
  await page.locator('#voice-select').selectOption('gb');await page.locator('#voice-rate').fill('1.1');await page.locator('#voice-rate').dispatchEvent('input');await page.locator('#voice-preview').click();
  assert.equal(await page.evaluate(()=>window.testSpoken.at(-1).voice.voiceURI),'gb');assert.equal(await page.evaluate(()=>window.testSpoken.at(-1).rate),1.1);await finish(page);
  await page.reload();await page.waitForFunction(()=>document.querySelector('#model-status').textContent.includes('browser-test-fixture'));assert.equal(await page.evaluate(()=>localStorage.getItem('jarvis-voice-uri')),'gb');assert.equal(await page.evaluate(()=>window.testMics.length),0);
  await page.locator('#handsfree-toggle').click();await phase(page,'listening');await page.evaluate(()=>window.testMics.at(-1).onerror({error:'not-allowed'}));await phase(page,'off');assert.match(await page.locator('#toast').textContent(),/access was denied/);await page.waitForTimeout(800);assert.equal(await page.evaluate(()=>window.testMics.length),1);
  await page.emulateMedia({reducedMotion:'reduce'});assert.equal(await page.locator('.hud-rotate').evaluate(el=>getComputedStyle(el).animationName),'none');
  assert.deepEqual(errors,[]);
  console.log('Dashboard E2E passed: animated HUD, speech pulses, connected real-event graph, mobile activity graph, reduced motion; two automatic spoken turns, UK voice selection, no self-echo, approval pause/decline/resume, spoken stop, saved voice/rate, mobile layout, no auto-mic after reload, permission failure; no page errors.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
