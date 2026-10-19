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
async function installAPI(page, permissionsMode='autonomous', neural=false) {
 const messages=[], pending=[], prompts=[], decisions=[], speechRequests=[], providerRequests=[], delayedRefresh=[];let workflow=null,actions=[];
 const provider={check:{status:200,body:{ok:true,message:'Streaming, tools and tool-result follow-up accepted.'}},repair:{status:200,body:{ok:true,changed:false,message:'Current model supports the full workflow.'}},chatError:'',holdRefresh:false};
 const stamp=new Date().toISOString();
 const settings={provider_ready:true,provider_url:'http://localhost:11434/v1',model:'browser-test-fixture',roots:['/tmp'],name:'Owen',has_api_key:false,native_notifications:false,data_dir:'/tmp/voice-test',permissions_mode:permissionsMode,computer_access:'full',workflow_timeout:300,application_directories:[],speech_mode:'auto',speech_voice:'cedar',speech_available:neural,browser_mode:'existing',background_voice:true};
 await page.route('**/api/**',async route=>{
  const req=route.request(), path=new URL(req.url()).pathname.slice(4), method=req.method();
  let data;
  if(provider.holdRefresh&&method==='GET'&&['/tasks','/goals','/reminders','/memories','/conversations'].includes(path)){delayedRefresh.push(route);return;}
  if(path==='/settings'||path==='/provider/check'||path==='/provider/repair')providerRequests.push({path,method,body:method==='PATCH'?req.postDataJSON():null,model:settings.model});
  if(path==='/provider/check'||path==='/provider/repair'){
   const result=path.endsWith('/repair')?provider.repair:provider.check;
   if(result.status===200&&result.body.changed)settings.model=result.body.model;
   await route.fulfill({status:result.status,json:result.body});return;
  }
  if(path==='/speech'){speechRequests.push(req.postDataJSON());await route.fulfill({contentType:'audio/wav',body:audioFixture()});return;}
  if(path==='/status') data={ok:true,settings,drives:[],active:[]};
  else if(path==='/settings'){if(method==='PATCH')Object.assign(settings,req.postDataJSON());data=settings;}
  else if(path==='/conversations'&&method==='POST') data={id:'c1',title:'New conversation',created_at:stamp,updated_at:stamp};
  else if(path==='/conversations') data=messages.length?[{id:'c1',title:'Voice test',created_at:stamp,updated_at:stamp}]:[];
  else if(path==='/conversations/c1') data={messages,approvals:pending,active:false,workflow,actions};
  else if(path==='/conversations/c1/chat'){
   const content=req.postDataJSON().content;prompts.push(content);messages.push({id:'u'+messages.length,role:'user',content});
   workflow={id:'w'+prompts.length,status:'completed',request:content,plan:['Find the application','Open its window']};
   actions=[{id:'step1',workflow_id:workflow.id,name:'search_applications',status:'completed'},{id:'step2',workflow_id:workflow.id,name:'launch_application',status:'completed'}];
   const activity=[{type:'workflow',id:workflow.id,status:'running'},{type:'plan',outcome:content,steps:workflow.plan},...actions.flatMap(a=>[{type:'action',action:{...a,status:'running'}},{type:'tool',action_id:a.id,name:a.name,ok:true,status:'completed'}])];
   if(provider.chatError){
    workflow.status='failed';workflow.detail=provider.chatError;
    await route.fulfill({contentType:'text/event-stream',body:[...activity,{type:'error',message:provider.chatError},{type:'outcome',workflow,approvals:pending},{type:'done'}].map(e=>'data: '+JSON.stringify(e)+'\n\n').join('')});return;
   }
   if(content==='Open Calculator'&&settings.permissions_mode!=='autonomous'){
    const approval={id:'a1',conversation_id:'c1',name:'open_application',arguments:{name:'calculator'},preview:{summary:'Open Calculator'}};
    pending.push(approval);workflow.status='awaiting_confirmation';actions[1].status='awaiting_confirmation';
    await route.fulfill({contentType:'text/event-stream',body:[...activity,{type:'action',action:actions[1]},{type:'approval',approval},{type:'outcome',workflow,approvals:pending},{type:'done'}].map(e=>'data: '+JSON.stringify(e)+'\n\n').join('')});return;
   }
   const text=prompts.length===1?'Good evening, Owen. How can I help?':'Of course. Let us plan your day.';
   messages.push({id:'a'+messages.length,role:'assistant',content:text});
   await route.fulfill({contentType:'text/event-stream',body:[...activity,{type:'token',text},{type:'message',content:text},{type:'outcome',workflow,approvals:pending},{type:'done'}].map(e=>'data: '+JSON.stringify(e)+'\n\n').join('')});return;
  }
  else if(path==='/approvals/a1'){
   decisions.push(req.postDataJSON().approved);pending.length=0;workflow.status='completed';actions[1].status='declined';const text='The action was declined.';messages.push({id:'a'+messages.length,role:'assistant',content:text});
   await route.fulfill({contentType:'text/event-stream',body:[{type:'message',content:text},{type:'outcome',workflow,approvals:pending},{type:'done'}].map(e=>'data: '+JSON.stringify(e)+'\n\n').join('')});return;
  }
  else if(path==='/activity') data={active:[],workflows:workflow?[workflow]:[],actions:actions.slice().reverse(),emergency_hotkey:false};
  else data=[];
  await route.fulfill({json:data});
 });
 return {prompts,decisions,speechRequests,settings,provider,providerRequests,delayedRefresh,async releaseRefresh(){provider.holdRefresh=false;await Promise.all(delayedRefresh.splice(0).map(route=>route.fulfill({json:[]})));}};
}
function audioFixture(){
 const rate=16000,seconds=2,samples=rate*seconds,buffer=Buffer.alloc(44+samples*2);
 buffer.write('RIFF');buffer.writeUInt32LE(buffer.length-8,4);buffer.write('WAVEfmt ',8);buffer.writeUInt32LE(16,16);buffer.writeUInt16LE(1,20);buffer.writeUInt16LE(1,22);buffer.writeUInt32LE(rate,24);buffer.writeUInt32LE(rate*2,28);buffer.writeUInt16LE(2,32);buffer.writeUInt16LE(16,34);buffer.write('data',36);buffer.writeUInt32LE(samples*2,40);
 for(let index=0;index<samples;index++)buffer.writeInt16LE(Math.round(Math.sin(index*2*Math.PI*220/rate)*4000),44+index*2);
 return buffer;
}
const phase=(page,name)=>page.waitForFunction(name=>document.body.dataset.voicePhase===name,name);
const words=(page,text)=>page.evaluate(text=>window.testMics.at(-1).words(text),text);
const finish=(page)=>page.evaluate(()=>window.testSpoken.at(-1).onend());
const home=async page=>{
 assert.equal(await page.locator('#jarvis-hud').isVisible(),true);
 assert.equal(await page.locator('#flow-rail').isVisible(),true);
 assert.equal(await page.locator('#conversation-panel').isVisible(),false);
 for(const selector of ['#chat-scroll','#chat-input','.composer-area','#voice-transcript'])assert.equal(await page.locator(selector).isVisible(),false,selector+' must stay in the closed transcript drawer');
};
const navigate=async(page,view)=>{await page.locator('#menu-button').click();await page.locator('[data-view="'+view+'"]').click();};
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.JARVIS_TEST_BROWSER || '/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await installSpeech(page);const api=await installAPI(page);
  await page.goto(process.env.JARVIS_TEST_URL || 'http://127.0.0.1:8765');await page.waitForFunction(()=>document.querySelector('#model-status').textContent.includes('browser-test-fixture'));
  await home(page);
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
  await home(page);
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
  const micCount=await page.evaluate(()=>window.testMics.length);
  await page.locator('#conversation-toggle').click();
  assert.equal(await page.locator('#conversation-panel').isVisible(),true);
  assert.equal(await page.locator('#chat-input').isVisible(),true);
  assert.match(await page.locator('#messages').textContent(),/Hello JARVIS/);
  assert.match(await page.locator('#messages').textContent(),/Good evening, Owen/);
  await phase(page,'listening');assert.equal(await page.evaluate(()=>window.testMics.at(-1).active),true);
  await page.locator('#close-conversation').click();await home(page);await phase(page,'listening');
  assert.equal(await page.evaluate(()=>window.testMics.length),micCount,'opening the transcript must preserve the running microphone');
  await words(page,'Plan my day');await phase(page,'speaking');assert.equal(api.prompts.length,2);
  await page.locator('#front-skip').click();await phase(page,'listening');await home(page);
  await page.screenshot({path:'/tmp/jarvis-handsfree-desktop.png',fullPage:true});
  await words(page,'Open Calculator');await phase(page,'speaking');await home(page);
  assert.equal(await page.locator('.approval-card').count(),0,'autonomous requests must not show approval cards');assert.deepEqual(api.decisions,[]);
  await finish(page);await phase(page,'listening');
  await page.setViewportSize({width:390,height:844});await home(page);await page.screenshot({path:'/tmp/jarvis-handsfree-mobile.png',fullPage:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.setViewportSize({width:820,height:900});await home(page);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await page.screenshot({path:'/tmp/jarvis-hud-tablet.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await page.locator('#front-voice-stop').click();await phase(page,'off');
  await navigate(page,'activity');
  await page.waitForFunction(()=>!document.querySelector('#activity-view').hidden);
  assert.equal(await page.locator('#activity-flow .flow-action').count(),2);assert.ok(await page.locator('#activity-flow .flow-edge').count()>=3);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.screenshot({path:'/tmp/jarvis-hud-mobile-flow.png',fullPage:true});
  await navigate(page,'chat');await home(page);await page.locator('#jarvis-hud').click();await phase(page,'listening');
  await words(page,'Stop listening');await phase(page,'off');assert.equal(api.prompts.length,3);
  await page.setViewportSize({width:1440,height:1000});await navigate(page,'settings');
  assert.equal(await page.locator('#permissions-mode').inputValue(),'autonomous');
  await page.locator('#voice-select').selectOption('gb');await page.locator('#voice-rate').fill('1.1');await page.locator('#voice-rate').dispatchEvent('input');const previousSamples=await page.evaluate(()=>window.testSpoken.length);await page.locator('#voice-preview').click();await page.waitForFunction(count=>window.testSpoken.length>count,previousSamples);
  assert.equal(await page.evaluate(()=>window.testSpoken.at(-1).voice.voiceURI),'gb');assert.equal(await page.evaluate(()=>window.testSpoken.at(-1).rate),1.1);await finish(page);
  await page.reload();await page.waitForFunction(()=>document.querySelector('#model-status').textContent.includes('browser-test-fixture'));assert.equal(await page.evaluate(()=>localStorage.getItem('jarvis-voice-uri')),'gb');assert.equal(await page.evaluate(()=>window.testMics.length),0);
  await home(page);await page.locator('#jarvis-hud').click();await phase(page,'listening');await page.evaluate(()=>window.testMics.at(-1).onerror({error:'not-allowed'}));await phase(page,'off');assert.match(await page.locator('#toast').textContent(),/access was denied/);await page.waitForTimeout(800);assert.equal(await page.evaluate(()=>window.testMics.length),1);
  await page.emulateMedia({reducedMotion:'reduce'});assert.equal(await page.locator('.hud-rotate').evaluate(el=>getComputedStyle(el).animationName),'none');
  assert.deepEqual(errors,[]);

  // Legacy confirmation is still available when deliberately selected in Settings.
  const review=await browser.newPage({viewport:{width:1440,height:1000}});const reviewErrors=[];review.on('pageerror',e=>reviewErrors.push(e.message));
  await installSpeech(review);const reviewAPI=await installAPI(review,'routine');
  await review.goto(process.env.JARVIS_TEST_URL || 'http://127.0.0.1:8765');await review.waitForFunction(()=>document.querySelector('#model-status').textContent.includes('browser-test-fixture'));
  await home(review);await review.locator('#jarvis-hud').click();await phase(review,'listening');
  await words(review,'Open Calculator');await phase(review,'speaking');
  if(!await review.locator('#conversation-panel').isVisible())await review.locator('#conversation-toggle').click();
  await review.locator('.approval-card').waitFor();await finish(review);await phase(review,'approval');
  assert.equal(await review.locator('#flow-rail .flow-action[data-status="awaiting_confirmation"]').count(),1);assert.equal(await review.evaluate(()=>window.testMics.at(-1).active),false);assert.deepEqual(reviewAPI.decisions,[]);
  await words(review,'Approve action');await review.waitForTimeout(1300);assert.deepEqual(reviewAPI.decisions,[]);
  await review.locator('[data-decision="false"]').click();await phase(review,'speaking');assert.deepEqual(reviewAPI.decisions,[false]);await finish(review);await phase(review,'listening');
  await review.locator('#close-conversation').click();await home(review);await phase(review,'listening');
  await review.locator('#front-voice-stop').click();await phase(review,'off');assert.deepEqual(reviewErrors,[]);
  const natural=await browser.newPage({viewport:{width:1440,height:1000}}),naturalErrors=[];
  natural.on('pageerror',error=>naturalErrors.push(error.message));
  await installSpeech(natural);const naturalAPI=await installAPI(natural,'autonomous',true);
  await natural.goto(process.env.JARVIS_TEST_URL || 'http://127.0.0.1:8765');await natural.waitForFunction(()=>document.querySelector('#model-status').textContent.includes('browser-test-fixture'));
  await natural.locator('#jarvis-hud').click();await phase(natural,'listening');await words(natural,'Hello in your natural British voice');await phase(natural,'speaking');
  await natural.waitForFunction(()=>document.querySelector('body').dataset.voicePhase==='speaking' && performance.getEntriesByType('resource').some(entry=>entry.name.includes('/api/speech')));
  assert.equal(naturalAPI.speechRequests.length,1);assert.match(naturalAPI.speechRequests[0].text,/Good evening/);
  assert.equal(await natural.evaluate(()=>window.testSpoken.length),0,'natural audio must replace browser synthesis');
  assert.equal(await natural.evaluate(()=>window.testMics.at(-1).active),false,'microphone must pause throughout neural playback');
  await phase(natural,'listening');await home(natural);
  await words(natural,'Another reply');await phase(natural,'speaking');await natural.locator('#front-skip').click();await phase(natural,'listening');
  await natural.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'));});
  await phase(natural,'listening');assert.equal(await natural.evaluate(()=>window.testMics.at(-1).active),true);
  await natural.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});document.dispatchEvent(new Event('visibilitychange'));});
  await navigate(natural,'settings');await natural.locator('#background-voice').uncheck();await natural.locator('#settings-form button[type=submit]').click();await natural.waitForFunction(()=>document.querySelector('#settings-result').textContent==='Changes saved');
  await navigate(natural,'chat');await phase(natural,'listening');
  await natural.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'));});await phase(natural,'paused');
  await natural.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});document.dispatchEvent(new Event('visibilitychange'));});await phase(natural,'listening');
  await natural.locator('#front-voice-stop').click();await phase(natural,'off');assert.deepEqual(naturalErrors,[]);

  // Verified reply playback must not wait for unrelated sidebar requests.
  const quick=await browser.newPage({viewport:{width:1440,height:1000}}),quickErrors=[];
  quick.on('pageerror',error=>quickErrors.push(error.message));
  await installSpeech(quick);const quickAPI=await installAPI(quick);
  await quick.goto(process.env.JARVIS_TEST_URL || 'http://127.0.0.1:8765');await quick.waitForFunction(()=>document.querySelector('#model-status').textContent.includes('browser-test-fixture'));
  await quick.locator('#jarvis-hud').click();await phase(quick,'listening');
  quickAPI.provider.holdRefresh=true;
  await words(quick,'Hello without waiting for the sidebar');await phase(quick,'speaking');
  assert.ok(quickAPI.delayedRefresh.length>0,'the sidebar remains pending when speech starts');
  assert.equal(await quick.evaluate(()=>window.testSpoken.length),1);
  assert.equal(await quick.locator('#flow-rail .flow-hub').getAttribute('data-status'),'completed','stream supplies the observed final workflow without fetching history');
  await quickAPI.releaseRefresh();await finish(quick);await phase(quick,'listening');
  await quick.locator('#front-voice-stop').click();await phase(quick,'off');assert.deepEqual(quickErrors,[]);

  // Diagnose the actual provider error and repair only after a model passes the
  // backend's checks. A failed continuation must remain visible in the task flow
  // without opening the conversation drawer on the minimal home screen.
  const diagnostics=await browser.newPage({viewport:{width:1440,height:1000}}),diagnosticErrors=[];
  diagnostics.on('pageerror',error=>diagnosticErrors.push(error.message));
  await installSpeech(diagnostics);const diagnosticAPI=await installAPI(diagnostics);
  await diagnostics.goto(process.env.JARVIS_TEST_URL || 'http://127.0.0.1:8765');await diagnostics.waitForFunction(()=>document.querySelector('#model-status').textContent.includes('browser-test-fixture'));
  await navigate(diagnostics,'settings');
  const rejectedModel='unavailable-custom-model';
  const providerFailure='Model provider returned HTTP 400 (param: model): The model unavailable-custom-model does not support tool calling.';
  diagnosticAPI.provider.check={status:400,body:{detail:providerFailure}};
  await diagnostics.locator('#provider-model').fill(rejectedModel);
  await diagnostics.locator('#check-provider').click();
  await diagnostics.waitForFunction(message=>document.querySelector('#provider-result').textContent===message,providerFailure);
  assert.equal(await diagnostics.locator('#provider-pill').textContent(),'Connection failed');
  assert.equal(await diagnostics.locator('#provider-model').inputValue(),rejectedModel,'a rejected connection must preserve the user selection for diagnosis');
  assert.equal(await diagnostics.locator('#check-provider').isEnabled(),true);
  assert.equal(await diagnostics.locator('#repair-provider').isEnabled(),true);
  assert.equal(diagnosticAPI.settings.model,rejectedModel,'test connection must save the edited model before testing it');
  assert.deepEqual(diagnosticAPI.providerRequests.slice(0,2).map(request=>request.path),['/settings','/provider/check']);
  assert.equal(diagnosticAPI.providerRequests[1].model,rejectedModel);
  const repairedModel='provider-listed-compatible-model';
  const repairedMessage='Connected with provider-listed-compatible-model. Streaming, JARVIS tools and tool-result follow-up passed.';
  diagnosticAPI.provider.repair={status:200,body:{ok:true,changed:true,model:repairedModel,message:repairedMessage}};
  await diagnostics.locator('#repair-provider').click();
  await diagnostics.waitForFunction(message=>document.querySelector('#provider-result').textContent===message,repairedMessage);
  assert.equal(await diagnostics.locator('#provider-pill').textContent(),'Connected');
  assert.equal(await diagnostics.locator('#provider-model').inputValue(),repairedModel);
  assert.equal(diagnosticAPI.settings.model,repairedModel);
  assert.match(await diagnostics.locator('#model-status').textContent(),/provider-listed-compatible-model/);
  assert.deepEqual(diagnosticAPI.providerRequests.map(request=>request.path),['/settings','/provider/check','/settings','/provider/repair','/settings']);
  assert.equal(diagnosticAPI.settings.permissions_mode,'autonomous');
  assert.equal(await diagnostics.locator('#repair-provider').isEnabled(),true);
  diagnosticAPI.provider.chatError=providerFailure;
  await navigate(diagnostics,'chat');await home(diagnostics);
  await diagnostics.locator('#jarvis-hud').click();await phase(diagnostics,'listening');
  await words(diagnostics,'Open Notes and write my note');
  await diagnostics.waitForFunction(()=>document.querySelector('#flow-rail .flow-hub')?.dataset.status==='failed');
  await phase(diagnostics,'off');await home(diagnostics);
  assert.equal(await diagnostics.locator('#flow-rail .flow-hub small').textContent(),providerFailure);
  assert.equal(await diagnostics.locator('#flow-rail .flow-hub').getAttribute('title'),providerFailure,'full diagnostic must remain accessible when the node subtitle is truncated');
  assert.equal(await diagnostics.locator('#flow-rail .flow-action[data-status="completed"]').count(),2,'completed launch remains recorded even though its model continuation failed');
  assert.equal(await diagnostics.locator('.approval-card').count(),0);
  assert.equal(await diagnostics.locator('#chat-error').isVisible(),false,'provider errors must not expose conversation text on the minimal home page');
  await diagnostics.screenshot({path:'/tmp/jarvis-provider-failure.png',fullPage:true});
  await diagnostics.reload();await diagnostics.waitForFunction(()=>document.querySelector('#flow-rail .flow-hub')?.dataset.status==='failed');
  await home(diagnostics);assert.equal(await diagnostics.locator('#flow-rail .flow-hub small').textContent(),providerFailure);
  assert.deepEqual(diagnosticErrors,[]);
  console.log('Dashboard E2E passed: minimal circle/flow front page, hidden transcript during speech, transcript drawer without microphone interruption, front-page speech controls, autonomous requests without approval, optional routine approval pause/decline/resume, animated HUD, live connected graph on desktop/mobile, reduced motion, three spoken turns, UK voice selection, no self-echo, spoken stop, saved voice/rate, real blob audio playback and neural turn-taking, no auto-mic after reload, permission failure, exact connection diagnostics, compatible model repair, failed continuation remains visible in flow while home stays clean; reply playback before delayed sidebar refresh; no page errors.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
