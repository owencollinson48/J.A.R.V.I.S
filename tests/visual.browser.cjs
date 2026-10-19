const {chromium} = require('playwright');
const assert = require('node:assert/strict');

async function installSpeech(page) {
  await page.addInitScript(() => {
    window.testMics = [];
    window.testSpoken = [];
    window.visualAtSpeak = [];
    class Mic {
      constructor() { window.testMics.push(this); }
      start() { this.active = true; this.onstart?.(); }
      abort() { this.active = false; this.onend?.(); }
      words(text) { const result = [{transcript:text}]; result.isFinal = true; this.onresult?.({results:[result]}); }
    }
    class Synth extends EventTarget {
      getVoices() { return [{name:'Microsoft George',lang:'en-GB',voiceURI:'gb'}]; }
      speak(utterance) {
        window.testSpoken.push(utterance);
        const panel = document.querySelector('#visual-panel');
        window.visualAtSpeak.push(Boolean(panel && !panel.hidden && panel.getBoundingClientRect().height));
      }
      cancel() {}
    }
    Object.defineProperty(window, 'SpeechRecognition', {value:Mic,configurable:true});
    Object.defineProperty(window, 'webkitSpeechRecognition', {value:Mic,configurable:true});
    Object.defineProperty(window, 'speechSynthesis', {value:new Synth(),configurable:true});
    Object.defineProperty(window, 'SpeechSynthesisUtterance', {value:class {constructor(text) { this.text = text; }},configurable:true});
  });
}

async function installAPI(page) {
  const conversations = new Map();
  const replies = [];
  const prompts = [];
  const dismissed = [];
  const stamp = new Date().toISOString();
  const settings = {provider_ready:true,provider_url:'http://localhost:11434/v1',model:'visual-browser-fixture',roots:['/tmp'],name:'Owen',has_api_key:false,native_notifications:false,data_dir:'/tmp/visual-test',permissions_mode:'autonomous',computer_access:'full',workflow_timeout:300,application_directories:[],speech_mode:'auto',speech_voice:'cedar',speech_available:false,browser_mode:'existing',background_voice:true};
  await page.route('**/api/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname.slice(4);
    const method = request.method();
    let data;
    if (path === '/status') data = {ok:true,settings,drives:[],active:[]};
    else if (path === '/settings') data = settings;
    else if (path === '/conversations' && method === 'POST') {
      data = {id:'c' + (conversations.size + 1),title:'Visual conversation',created_at:stamp,updated_at:stamp,messages:[],approvals:[],active:false,workflow:null,actions:[],visual:null};
      conversations.set(data.id, data);
    } else if (path === '/conversations') data = [...conversations.values()].map(({id,title,created_at,updated_at}) => ({id,title,created_at,updated_at}));
    else if (/^\/conversations\/[^/]+\/visuals\/[^/]+$/.test(path) && method === 'DELETE') {
      const [, , id, , visualID] = path.split('/');
      dismissed.push({conversation_id:id,id:visualID});
      const conversation = conversations.get(id);
      if (conversation?.visual?.id === visualID) conversation.visual = null;
      data = {ok:true};
    } else if (/^\/conversations\/[^/]+\/chat$/.test(path)) {
      const id = path.split('/')[2];
      const conversation = conversations.get(id);
      const content = request.postDataJSON().content;
      prompts.push(content);
      const reply = replies.shift();
      assert.ok(reply, 'every chat must have an explicit visual fixture');
      conversation.messages.push({id:'u' + prompts.length,role:'user',content});
      conversation.visual = {...reply.visual,conversation_id:id};
      const workflow = {id:'w' + prompts.length,status:'completed',request:content,plan:['Build the requested visual']};
      conversation.workflow = workflow;
      conversation.messages.push({id:'a' + prompts.length,role:'assistant',content:reply.text});
      const events = [{type:'workflow',id:workflow.id,status:'running'}, {type:'visual',visual:conversation.visual}, {type:'message',content:reply.text}, {type:'outcome',workflow,approvals:[]}, {type:'done'}];
      await route.fulfill({contentType:'text/event-stream',body:events.map(event => 'data: ' + JSON.stringify(event) + '\n\n').join('')});
      return;
    } else if (/^\/conversations\/[^/]+$/.test(path)) data = conversations.get(path.split('/')[2]);
    else if (path === '/activity') data = {active:[],workflows:[],actions:[],emergency_hotkey:false};
    else data = [];
    await route.fulfill({json:data});
  });
  return {conversations,replies,prompts,dismissed};
}

const phase = (page, name) => page.waitForFunction(name => document.body.dataset.voicePhase === name, name);
const words = (page, text) => page.evaluate(text => window.testMics.at(-1).words(text), text);
const finish = page => page.evaluate(() => window.testSpoken.at(-1).onend());
const ready = page => page.waitForFunction(() => document.querySelector('#model-status').textContent.includes('visual-browser-fixture'));
async function cleanHome(page) {
  assert.equal(await page.locator('#jarvis-hud').isVisible(), true);
  assert.equal(await page.locator('#flow-rail').isVisible(), true);
  assert.equal(await page.locator('#conversation-panel').isVisible(), false);
  assert.equal(await page.locator('#chat-input').isVisible(), false);
}
async function noOverflow(page) {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'visuals must not widen the page');
  const box = await page.locator('#visual-panel').boundingBox();
  const viewport = page.viewportSize();
  assert.ok(box && box.width > 0 && box.height > 0);
  assert.ok(box.x >= 0 && box.x + box.width <= viewport.width + 1, 'the visual panel must fit the viewport');
}

(async () => {
  const browser = await chromium.launch({executablePath:process.env.JARVIS_TEST_BROWSER || '/usr/bin/chromium',headless:true,args:['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await installSpeech(page);
    const api = await installAPI(page);
    const url = process.env.JARVIS_TEST_URL || 'http://127.0.0.1:8765';
    await page.goto(url);
    await ready(page);
    await cleanHome(page);
    assert.equal(await page.locator('#visual-panel').isVisible(), false, 'the front page stays simple until a visual is requested');
    const day = {id:'v-day',kind:'timeline',title:'Your day, at a glance',subtitle:'A suggested outline — not added to your calendar.',created_at:new Date().toISOString(),items:[{time:'09:00',label:'Morning focus',detail:'Prepare the project outline.',status:'active'},{time:'12:30',label:'Lunch and a reset',detail:'Leave a little room to breathe.',status:'planned'},{time:'16:00',label:'Wrap up',detail:'Review progress and plan tomorrow.',status:'planned'}]};
    const dayText = 'Here is your day, sir. Morning focus at nine, lunch at half past twelve, then a wrap-up at four.';
    api.replies.push({visual:day,text:dayText});
    await page.locator('#jarvis-hud').click();
    await phase(page, 'listening');
    await words(page, 'Show an outline of my day');
    await phase(page, 'speaking');
    await cleanHome(page);
    assert.deepEqual(api.prompts, ['Show an outline of my day']);
    assert.equal(await page.locator('#visual-panel').isVisible(), true);
    assert.equal(await page.locator('#visual-panel').getAttribute('data-kind'), 'timeline');
    assert.equal(await page.locator('#visual-title').textContent(), day.title);
    assert.deepEqual(await page.locator('.visual-time').allTextContents(), day.items.map(item => item.time));
    assert.deepEqual(await page.locator('#visual-panel .visual-item-label').allTextContents(), day.items.map(item => item.label));
    assert.equal(await page.locator('#visual-panel .visual-item[data-status="active"]').count(), 1);
    assert.equal(await page.evaluate(() => window.testSpoken[0].text), dayText);
    assert.deepEqual(await page.evaluate(() => window.visualAtSpeak), [true], 'the requested graphic is already visible while the explanation is spoken');
    assert.equal(await page.evaluate(() => window.testMics.length), 1, 'a visual must not create another recognition session');
    assert.equal(await page.evaluate(() => window.testMics.at(-1).active), false, 'speech playback keeps the existing microphone paused');
    await noOverflow(page);
    await page.screenshot({path:'/tmp/jarvis-day-visual.png',fullPage:true});

    // Saved graphics restore with the conversation, without replaying old speech
    // or silently starting the microphone after a reload.
    await page.reload();
    await ready(page);
    await page.locator('#visual-title').waitFor();
    assert.equal(await page.locator('#visual-title').textContent(), day.title);
    await cleanHome(page);
    assert.equal(await page.evaluate(() => window.testSpoken.length), 0);
    assert.equal(await page.evaluate(() => window.testMics.length), 0);
    await page.getByRole('button', {name:'Dismiss visual',exact:true}).click();
    await page.waitForFunction(() => document.querySelector('#visual-panel').hidden);
    assert.deepEqual(api.dismissed, [{conversation_id:'c1',id:'v-day'}]);
    await page.reload();
    await ready(page);
    assert.equal(await page.locator('#visual-panel').isVisible(), false, 'dismissed saved visuals stay dismissed on reload');

    // Each new visual replaces the existing graphic; user labels are rendered as
    // text rather than HTML and cards preserve their supplied content.
    const hostile = '<img src=x onerror="window.visualXSS=true">';
    const cards = {id:'v-cards',kind:'cards',title:'Your three priorities',items:[{label:'Prepare the outline',detail:'Keep it to one page.',status:'done'},{label:hostile,detail:'This supplied label must remain plain text.',status:'planned'},{label:'Call Alex',time:'15:00',status:'planned'}]};
    api.replies.push({visual:cards,text:'Here are your three priorities, sir.'});
    await page.locator('#jarvis-hud').click();
    await phase(page, 'listening');
    await words(page, 'Show my priorities as cards');
    await phase(page, 'speaking');
    assert.equal(await page.locator('#visual-panel').getAttribute('data-kind'), 'cards');
    assert.deepEqual(await page.locator('#visual-panel .visual-item-label').allTextContents(), cards.items.map(item => item.label));
    assert.equal(await page.locator('#visual-panel img').count(), 0);
    assert.equal(await page.evaluate(() => window.visualXSS), undefined);
    assert.equal(await page.locator('#visual-panel .visual-item[data-status="done"]').textContent().then(text => text.includes('Done')), true);
    await finish(page);
    await phase(page, 'listening');
    const bars = {id:'v-bars',kind:'bars',title:'Time by activity',unit:'hours',items:[{label:'Project work',value:3.5,detail:'Focused work blocks.'},{label:'Meetings',value:1.75},{label:'Unallocated',value:0}]};
    api.replies.push({visual:bars,text:'Your work blocks total three and a half hours, with one and three-quarter hours for meetings, sir.'});
    await words(page, 'Show the time breakdown');
    await phase(page, 'speaking');
    assert.equal(await page.locator('#visual-panel').count(), 1);
    assert.equal(await page.locator('#visual-panel').getAttribute('data-kind'), 'bars');
    assert.equal(await page.locator('#visual-title').textContent(), bars.title);
    assert.equal(await page.locator('.visual-cards').count(), 0);
    assert.deepEqual(await page.locator('.visual-bar-value').allTextContents(), ['3.5 hours','1.75 hours','0 hours']);
    assert.deepEqual(await page.locator('.visual-bar-fill').evaluateAll(elements => elements.map(element => element.style.width)), ['100%','50%','0%'], 'bar lengths must reflect the supplied numeric values');
    await finish(page);
    await phase(page, 'listening');

    // Long mobile outlines remain scrollable inside the panel and do not widen
    // the dashboard. Reduced-motion preferences also apply to this addition.
    const longDay = {...day,id:'v-long-day',title:'A detailed outline for today',items:Array.from({length:12}, (_, index) => ({time:String(index + 7).padStart(2, '0') + ':00',label:'Activity ' + (index + 1),detail:'A deliberately long detail with a continuousidentifierthatmustwrapwithoutpushingthepaneloutsidetheviewport.',status:index === 0 ? 'active' : 'planned'}))};
    api.replies.push({visual:longDay,text:'Here is the full outline, sir. You can scroll through the remaining activities.'});
    await words(page, 'Show the detailed outline');
    await phase(page, 'speaking');
    await page.emulateMedia({reducedMotion:'reduce'});
    assert.equal(await page.locator('#visual-panel').evaluate(element => getComputedStyle(element).animationName), 'none');
    for (const viewport of [{width:390,height:844},{width:820,height:900},{width:667,height:375}]) {
      await page.setViewportSize(viewport);
      await cleanHome(page);
      await noOverflow(page);
    }
    await page.setViewportSize({width:390,height:844});
    const scrolled = await page.locator('#visual-panel').evaluate(element => { element.scrollTop = element.scrollHeight; return {top:element.scrollTop,scroll:element.scrollHeight,client:element.clientHeight}; });
    assert.ok(scrolled.scroll > scrolled.client && scrolled.top > 0, 'a long outline remains readable through its own scroll area');
    await page.locator('#visual-panel').evaluate(element => { element.scrollTop = 0; });
    await page.screenshot({path:'/tmp/jarvis-day-visual-mobile.png',fullPage:true});
    await page.locator('#front-voice-stop').click();
    await phase(page, 'off');
    await page.setViewportSize({width:1440,height:1000});
    await page.locator('#menu-button').click();
    await page.locator('#new-chat').click();
    assert.equal(await page.locator('#visual-panel').isVisible(), false, 'a new conversation clears the previous graphic');
    assert.equal(await page.evaluate(() => localStorage.getItem('jarvis-conversation')), null);
    assert.equal(api.conversations.get('c1').visual.id, 'v-long-day', 'starting a conversation does not dismiss the graphic saved in another conversation');
    await page.reload();
    await ready(page);
    assert.equal(await page.locator('#visual-panel').isVisible(), false);
    assert.equal(await page.evaluate(() => window.testSpoken.length), 0);
    assert.equal(await page.evaluate(() => window.testMics.length), 0);
    assert.deepEqual(errors, []);
    console.log('Visual browser checks passed: hands-free graphics and speech, persistence, dismissal, replacement, numeric bars, inert labels, responsive layout and reduced motion.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
