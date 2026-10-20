/* Deterministic UI fixtures; no market provider, TradingView account or orders are used. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');

async function fixture(page){
  const calls=[],chatCalls=[],messages=[],stamp='2026-10-19T14:00:00+00:00';let workflow=null;
  await page.addInitScript(()=>{
    window.tradingTestMics=[];window.tradingTestSpoken=[];
    class Mic{constructor(){window.tradingTestMics.push(this);}start(){this.onstart?.();}abort(){this.onend?.();}words(text){const result=[{transcript:text}];result.isFinal=true;this.onresult?.({results:[result]});}}
    class Synth extends EventTarget{getVoices(){return[{name:'Microsoft George',lang:'en-GB',voiceURI:'gb'}];}speak(item){window.tradingTestSpoken.push(item);}cancel(){}}
    Object.defineProperty(window,'SpeechRecognition',{value:Mic,configurable:true});Object.defineProperty(window,'webkitSpeechRecognition',{value:Mic,configurable:true});Object.defineProperty(window,'speechSynthesis',{value:new Synth(),configurable:true});Object.defineProperty(window,'SpeechSynthesisUtterance',{value:class{constructor(text){this.text=text;}},configurable:true});
  });
  const settings={provider_ready:true,provider_url:'http://localhost:11434/v1',model:'trading-browser-fixture',roots:['/tmp'],name:'Owen',has_api_key:false,native_notifications:false,data_dir:'/tmp/trading-test',permissions_mode:'autonomous',computer_access:'full',workflow_timeout:300,application_directories:[],speech_mode:'browser',speech_voice:'cedar',speech_available:false,browser_mode:'existing',background_voice:true};
  const metrics={total_trades:2,wins:1,losses:1,win_rate:'50',net_profit:'120',max_drawdown:'80',profit_factor:'2.5',average_realized_r:'.6'};
  const charts={equity_curve:[{time:stamp,equity:'100000'},{time:'2026-10-19T15:00:00Z',equity:'100120'}],daily_pnl:[{label:'2026-10-19',value:'120'}]};
  const snapshot={mode:'OFF',emergency_stop:false,suspension_reason:'',execution_backend:'tradingview_desktop',connection:{adapter:'tradingview_desktop',tradingview:{connected:false,verified:false,reason:'Installed application has not been verified as Paper Trading.'}},account:{balance:'100000',equity:'100000',available_funds:'100000',currency:'USD'},config:{symbol:'NQ',smt_symbol:'ES',timeframe_minutes:5,execution_backend:'tradingview_desktop',risk_pct:'.5',daily_loss_pct:'2',weekly_loss_pct:'5',max_positions:2,max_exposure_pct:200,rr:'2.5',sessions:[{name:'London',timezone:'Europe/London',open:'08:00',weekdays:[0,1,2,3,4]},{name:'New York',timezone:'America/New_York',open:'09:30',weekdays:[0,1,2,3,4]}]},strategy_versions:[{id:'v1',name:'V1 Original',active:true,created_at:stamp},{id:'v2',name:'<img src=x onerror="window.injected=true">',active:false,created_at:stamp}],active_strategy_id:'v1',data:[],positions:[],orders:[],setups:[{symbol:'NQ',direction:'long',rating:60,time:stamp,passed:false,conditions:{liquidity_sweep:{passed:true,reason:'Actual level traded through.'},bos:{passed:false,reason:'No completed close above structure.'}}}],metrics,analytics:{metrics,charts},recent_backtests:[],candles:[{time:stamp,open:'21000',high:'21005',low:'20998',close:'21003'},{time:'2026-10-19T14:05:00Z',open:'21003',high:'21008',low:'21000',close:'21006'}]};
  snapshot.setups[0].evidence={liquidity_sweep:{price:'21000'},continuation:{fvg:{lower:'21001',upper:'21004',valid:true}}};
  snapshot.strategy_versions[0].config={rr:2.5};snapshot.strategy_versions[1].config={rr:3};
  const trade={id:'trade-fixture',symbol:'NQ',direction:'long',quantity:'1',entry_time:stamp,entry_price:'21000',exit_price:'21006',net_pnl:'120',realized_r:'1.5',exit_reason:'target',evidence:{bos:{passed:true}}};
  const report={id:'bt1',metrics,charts,trades:[trade],coverage:{count:100,start:stamp,end:'2026-10-19T20:00:00Z'},assumptions:['Fixture backtest: stop is applied first if stop and target share a bar.']};
  await page.route('**/api/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname.slice(4),method=request.method();let data=[];
    if(path.startsWith('/trading')){
      calls.push({path,method,body:method==='GET'?null:request.postDataJSON(),header:request.headers()['x-jarvis-client']});
      if(path==='/trading'&&method==='GET')data=snapshot;
      else if(path==='/trading/control'){
        const body=request.postDataJSON();
        if(body.action==='mode'&&body.mode==='PAPER'&&snapshot.connection.adapter!=='independent_paper'){
          await route.fulfill({status:400,json:{detail:'TradingView Paper Trading is not verified; no order was submitted.'}});return;
        }
        if(body.action==='mode')snapshot.mode=body.mode;
        if(body.action==='emergency'){snapshot.mode='OFF';snapshot.emergency_stop=true;snapshot.suspension_reason='Emergency stop: new entries are disabled.';}
        if(body.action==='reset_emergency'){snapshot.emergency_stop=false;snapshot.suspension_reason='';}
        data={message:body.action==='connect'?'TradingView remains unverified.':'Control saved.'};
      }else if(path==='/trading/config'){
        const body=request.postDataJSON();if(body.execution_backend){snapshot.execution_backend=body.execution_backend;snapshot.config.execution_backend=body.execution_backend;snapshot.connection.adapter=body.execution_backend;data={message:'Execution connection selected.'};}
        else {snapshot.strategy_versions.push({id:'v3',name:'Proposed configuration',active:false,created_at:stamp});data={strategy_id:'v3'};}
      }else if(path==='/trading/data'){const body=request.postDataJSON();snapshot.data=[{symbol:body.symbol,source:body.source,kind:body.kind,count:2,start:stamp,end:stamp,stale:true}];data={imported:2};}
      else if(path==='/trading/backtests'&&method==='POST'){snapshot.recent_backtests=[{id:'bt1',created_at:stamp}];data=report;}
      else if(path==='/trading/backtests/bt1')data=report;
      else if(path==='/trading/journal')data={dataset:url.searchParams.get('dataset'),trades:[trade],metrics,charts};
      else if(path==='/trading/strategies'){const body=request.postDataJSON();snapshot.strategy_versions.push({id:'v4',name:body.name,active:false,created_at:stamp});data={id:'v4'};}
      else if(path==='/trading/strategies/v2/activate'){snapshot.strategy_versions.forEach(version=>version.active=version.id==='v2');snapshot.active_strategy_id='v2';data={active_strategy_id:'v2'};}
      else if(path==='/trading/research')data={summary:'Two fixture versions compared. Out-of-sample results are uncertain.',requires_approval:true,warnings:['Insufficient sample for profitability claims.']};
      else if(path==='/trading/export'){await route.fulfill({contentType:'text/csv',body:'symbol,net_pnl\nNQ,120\n'});return;}
    }else if(path==='/status')data={ok:true,settings,drives:[],active:[]};
    else if(path==='/settings')data=settings;
    else if(path==='/conversations'&&method==='POST')data={id:'c1',title:'Trading stop test',created_at:stamp,updated_at:stamp};
    else if(path==='/conversations')data=messages.length?[{id:'c1',title:'Trading stop test',created_at:stamp,updated_at:stamp}]:[];
    else if(path==='/conversations/c1')data={messages,approvals:[],active:false,workflow,actions:[]};
    else if(path==='/conversations/c1/chat'){
      const body=request.postDataJSON();chatCalls.push(body.content);snapshot.mode='OFF';workflow={id:'local-stop',status:'completed',request:body.content,plan:[]};
      messages.push({id:'u1',role:'user',content:body.content},{id:'a1',role:'assistant',content:'New trading entries are stopped, sir.'});
      const events=[{type:'workflow',id:workflow.id,status:'running'},{type:'message',content:'New trading entries are stopped, sir.'},{type:'outcome',workflow,approvals:[]},{type:'done'}];
      await route.fulfill({contentType:'text/event-stream',body:events.map(event=>'data: '+JSON.stringify(event)+'\n\n').join('')});return;
    }
    else if(path==='/activity')data={active:[],workflows:[],actions:[],emergency_hotkey:false};
    await route.fulfill({json:data});
  });
  return{calls,snapshot,settings,chatCalls};
}
async function open(page,view){await page.locator('#menu-button').click();await page.locator('button.nav-item[data-view="'+view+'"]').click();}
async function tab(page,name){await page.locator('[data-trading-tab="'+name+'"]').click();}

(async()=>{
  const browser=await chromium.launch({executablePath:process.env.JARVIS_TEST_BROWSER || '/usr/bin/chromium',headless:true,args:['--no-sandbox']});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:1100},acceptDownloads:true}),errors=[];page.on('pageerror',error=>errors.push(error.message));const api=await fixture(page);
    await page.goto(process.env.JARVIS_TEST_URL || 'http://127.0.0.1:8765');
    await page.waitForFunction(()=>document.querySelector('#model-status').textContent.includes('trading-browser-fixture'));
    assert.equal(await page.locator('#jarvis-hud').isVisible(),true);assert.equal(await page.locator('#conversation-panel').isVisible(),false);assert.equal(await page.locator('#trading-view').isVisible(),false);
    assert.equal(api.calls.length,0,'opening JARVIS must not start trading or query a hidden trading dashboard');
    await open(page,'trading');await page.waitForFunction(()=>document.querySelector('#trading-connection').textContent.includes('not verified'));
    assert.match(await page.locator('#view-title').textContent(),/Trading Control Centre/);
    assert.equal(await page.locator('#trading-mode').textContent(),'OFF');assert.equal(await page.locator('#trading-account-metrics .trading-metric strong').first().textContent(),'—','an unverified TradingView connection must not show the simulator balance as account balance');
    assert.equal(await page.locator('#trading-account-metrics .trading-metric strong').nth(4).textContent(),'—','simulator P/L must not appear as native TradingView P/L');assert.match(await page.locator('#trading-positions').textContent(),/have not been read/);
    assert.deepEqual(api.calls.filter(call=>call.method!=='GET'),[],'loading the dashboard must perform no control action');
    api.snapshot.connection.tradingview.connected=true;api.snapshot.connection.tradingview.verified=true;api.snapshot.account.currency=null;await page.locator('#trading-refresh').click();await page.waitForFunction(()=>document.querySelector('#trading-account-provenance').textContent.includes('currency was not identified'));
    assert.equal(await page.locator('#trading-account-metrics .trading-metric strong').first().textContent(),'100,000');assert.equal(await page.locator('#trading-account-metrics .trading-metric strong').nth(4).textContent(),'—');assert.equal(await page.locator('#trading-account-metrics .trading-metric strong').nth(5).textContent(),'—','unread native positions must remain unknown, rather than a simulator zero');
    api.snapshot.connection.tradingview.connected=false;api.snapshot.connection.tradingview.verified=false;api.snapshot.account.currency='USD';await page.locator('#trading-refresh').click();await page.waitForFunction(()=>document.querySelector('#trading-connection').textContent.includes('not verified'));
    await page.locator('[data-trading-mode="PAPER"]').click();await page.waitForFunction(()=>document.querySelector('#trading-message').textContent.includes('not verified'));
    assert.equal(api.snapshot.mode,'OFF');assert.equal(api.snapshot.connection.adapter,'tradingview_desktop','a failed TradingView start must not silently switch to a simulator');
    await page.locator('#trading-connect').click();await page.waitForFunction(()=>document.querySelector('#trading-message').textContent==='TradingView remains unverified.');
    await page.locator('#trading-backend').selectOption('independent_paper');await page.locator('#trading-backend-save').click();await page.waitForFunction(()=>document.querySelector('#trading-connection').textContent.includes('Independent paper simulator'));
    await page.locator('[data-trading-mode="PAPER"]').click();await page.waitForFunction(()=>document.querySelector('#trading-mode').textContent==='PAPER');
    await page.locator('#trading-emergency-close').check();await page.locator('#trading-emergency').click();await page.waitForFunction(()=>document.querySelector('#trading-suspension').textContent.includes('Emergency stop'));
    assert.equal(api.snapshot.mode,'OFF');assert.equal(api.snapshot.emergency_stop,true);assert.equal(api.calls.find(call=>call.body?.action==='emergency').body.close_positions,true);
    await page.locator('#trading-reset').click();await page.waitForFunction(()=>document.querySelector('#trading-suspension').hidden);
    await page.locator('details.trading-card').first().locator('summary').click();await page.locator('#trading-risk').fill('0.75');await page.locator('#trading-config-form button[type=submit]').click();await page.waitForFunction(()=>document.querySelector('#trading-message').textContent.includes('Configuration proposed'));
    assert.equal(api.snapshot.active_strategy_id,'v1');assert.equal(api.calls.find(call=>call.path==='/trading/config'&&!call.body.execution_backend).body.risk_pct,.75);
    await tab(page,'market');assert.equal(await page.locator('#trading-market-chart svg').count(),1);assert.equal(await page.locator('#trading-market-chart .trading-zone').count(),1);await page.locator('#trading-setups summary').click();assert.match(await page.locator('.trading-conditions').textContent(),/No completed close/);
    await page.locator('[data-trading-panel=market] details.trading-card summary').click();await page.locator('#trading-import-source').fill('Authorised test fixture; NQ dated contract');await page.locator('#trading-import-csv').fill('time,open,high,low,close\n2026-10-19T14:00:00Z,21000,21005,20998,21003');await page.locator('#trading-import-form button[type=submit]').click();await page.waitForFunction(()=>document.querySelector('#trading-data-status').textContent.includes('Authorised test fixture'));
    const imported=api.calls.find(call=>call.path==='/trading/data');assert.equal(imported.body.kind,'historical');assert.equal(imported.body.symbol,'NQ');assert.equal(imported.header,'dashboard');
    await tab(page,'backtest');await page.locator('#trading-backtest-form button[type=submit]').click();await page.waitForFunction(()=>document.querySelector('#trading-backtest-result').textContent.includes('stop is applied first'));
    assert.equal(api.calls.find(call=>call.path==='/trading/backtests').body.symbol,'NQ');
    await tab(page,'analytics');assert.equal(await page.locator('#trading-analytics-chart svg').count(),1);await page.locator('#trading-analytics-chart circle').first().focus();assert.match(await page.locator('#trading-chart-readout').textContent(),/100,000/);await page.locator('#trading-chart-select').selectOption('daily_pnl');assert.equal(await page.locator('#trading-analytics-chart rect').count(),1);
    await tab(page,'journal');await page.waitForFunction(()=>document.querySelector('#trading-journal tbody tr'));await page.locator('#trading-journal tbody tr').click();assert.match(await page.locator('#trading-trade-detail').textContent(),/"bos"/);
    const download=page.waitForEvent('download');await page.locator('[data-trading-export=csv]').click();assert.match((await download).suggestedFilename(),/paper-journal.csv/);
    await tab(page,'research');assert.equal(await page.locator('#trading-strategies img').count(),0);assert.equal(await page.evaluate(()=>window.injected),undefined);assert.match(await page.locator('#trading-strategies').textContent(),/<img src=x/);
    await page.locator('#trading-strategies details').first().locator('summary').click();await page.locator('#trading-refresh').click();assert.equal(await page.locator('#trading-strategies details').first().evaluate(item=>item.open),true,'refresh must preserve an open strategy settings review');
    await page.locator('#trading-research-form button[type=submit]').click();await page.waitForFunction(()=>document.querySelector('#trading-research-result').textContent.includes('Out-of-sample'));
    assert.equal(api.snapshot.active_strategy_id,'v1','research must not activate a candidate');
    await page.locator('[data-activate-strategy=v2]').click();await page.waitForFunction(()=>document.querySelector('[data-activate-strategy=v2]').disabled);
    assert.deepEqual(api.calls.find(call=>call.path.endsWith('/v2/activate')).body,{approved:true});
    await page.setViewportSize({width:390,height:844});await tab(page,'overview');await page.screenshot({path:'/tmp/jarvis-trading-mobile.png',fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'mobile controls must not overflow the viewport');
    await open(page,'chat');assert.equal(await page.locator('#jarvis-hud').isVisible(),true);assert.equal(await page.locator('#conversation-panel').isVisible(),false);assert.equal(await page.locator('#trading-view').isVisible(),false);
    api.settings.provider_ready=false;api.snapshot.mode='PAPER';await page.reload();await page.waitForFunction(()=>document.querySelector('#model-status').textContent.includes('Connect a model'));
    await page.locator('#jarvis-hud').click();await page.waitForFunction(()=>document.body.dataset.voicePhase==='listening');
    await page.evaluate(()=>window.tradingTestMics.at(-1).words('Stop trading'));await page.waitForFunction(()=>document.body.dataset.voicePhase==='speaking');
    assert.deepEqual(api.chatCalls,['Stop trading'],'a local stop must pass the frontend without a configured model');assert.equal(api.snapshot.mode,'OFF');
    await page.evaluate(()=>window.tradingTestSpoken.at(-1).onend());await page.waitForFunction(()=>document.body.dataset.voicePhase==='listening');
    await page.evaluate(()=>window.tradingTestMics.at(-1).words('Start paper trading'));await page.waitForFunction(()=>document.body.dataset.view==='settings');
    assert.deepEqual(api.chatCalls,['Stop trading'],'a missing model must not turn an ordinary start command into a local control');assert.equal(api.snapshot.mode,'OFF');
    assert.deepEqual(errors,[]);
    console.log('Trading UI checks passed: untouched circle front page, explicit desktop/simulator connection, blocked unverified start, emergency latch/reset, proposed settings and explicit activation, real-data import form, separate backtest analytics, interactive chart inspection, safe text, journal details/export, mobile layout and model-independent spoken stops. Fixtures use no real account or orders.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
