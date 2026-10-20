/* Local paper-trading controls. Market text/data is rendered without executable markup. */
(() => {
  'use strict';
  const root = document.querySelector('#trading-view');
  if (!root) return;
  const $ = selector => root.querySelector(selector);
  const $$ = selector => [...root.querySelectorAll(selector)];
  const model = {snapshot:null,report:null,journal:[],journalAnalytics:null,tab:'overview',timer:null,loading:false,hydrated:false,versionsHydrated:false,versionFingerprint:'',setupFingerprint:'',request:0};
  const display = value => value === undefined || value === null || value === '' ? '—' : String(value);
  const number = value => value === null || value === '' || value === undefined ? null : Number.isFinite(Number(value)) ? Number(value) : null;
  const money = value => number(value) === null ? '—' : new Intl.NumberFormat(undefined,{style:'currency',currency:'USD',maximumFractionDigits:2}).format(Number(value));
  const decimal = value => number(value) === null ? '—' : new Intl.NumberFormat(undefined,{maximumFractionDigits:3}).format(Number(value));
  const percent = value => number(value) === null ? '—' : decimal(value)+'%';
  const when = value => { const date = new Date(value); return !value || Number.isNaN(date.valueOf()) ? display(value) : date.toLocaleString(); };
  function node(tag,text,className) { const item=document.createElement(tag); if(text!==undefined)item.textContent=display(text);if(className)item.className=className;return item; }
  async function api(path,body,method='POST') {
    const options={credentials:'same-origin',headers:{'X-Jarvis-Client':'dashboard'}};
    if(body!==undefined){options.method=method;options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);}
    const response=await fetch('/api/trading'+path,options);
    const result=await response.json();
    if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:JSON.stringify(result.detail || result));
    return result;
  }
  function notify(text,error=false) { const item=$('#trading-message');item.textContent=text;item.hidden=!text;item.classList.toggle('error',error); }
  async function run(operation,button) { if(button)button.disabled=true;notify('Working…');try { const result=await operation();notify(result?.message || 'Saved.');await refresh();return result; } catch(error){await refresh();notify(error.message,true);return null;}finally{if(button)button.disabled=false;} }
  function metrics(target,definitions,data={}) {
    target.replaceChildren(...definitions.map(([label,key,format])=>{const item=node('div',undefined,'trading-metric');item.append(node('span',label),node('strong',(format || decimal)(data[key])));return item;}));
  }
  function table(target,columns,rows,emptyText,select) {
    target.replaceChildren();if(!rows?.length){target.append(node('p',emptyText,'trading-empty'));return;}
    const t=node('table'),head=node('thead'),header=node('tr'),body=node('tbody');
    columns.forEach(([label])=>{const th=node('th',label);th.scope='col';header.append(th);});head.append(header);
    rows.forEach(row=>{const tr=node('tr');columns.forEach(([,key,format])=>{const td=node('td');td.textContent=(format || display)(typeof key==='function'?key(row):row[key]);tr.append(td);});
      if(select){tr.tabIndex=0;tr.className='trading-selectable';tr.setAttribute('aria-label','View trade '+display(row.id || row.trade_id));tr.onclick=()=>select(row);tr.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();select(row);}};}
      body.append(tr);});t.append(head,body);target.append(t);
  }
  const versionID = version => String(version.id || version.strategy_id || '');
  function versions(snapshot) {
    const fingerprint=JSON.stringify([snapshot.active_strategy_id,snapshot.strategy_versions]);if(fingerprint===model.versionFingerprint)return;model.versionFingerprint=fingerprint;
    const values=snapshot.strategy_versions || [],selected=$('#trading-backtest-strategy').value;
    $('#trading-backtest-strategy').replaceChildren(...values.map(version=>{const option=node('option',version.name+(version.active?' · active':''));option.value=versionID(version);return option;}));
    if(values.some(item=>versionID(item)===selected))$('#trading-backtest-strategy').value=selected;
    else $('#trading-backtest-strategy').value=snapshot.active_strategy_id || versionID(values[0] || {});
    const checks=new Set($$('#trading-research-versions input:checked').map(input=>input.value));
    $('#trading-research-versions').replaceChildren(...values.map(version=>{const label=node('label'),input=node('input');input.type='checkbox';input.value=versionID(version);input.checked=model.versionsHydrated?checks.has(input.value):values.indexOf(version)<2;label.append(input,document.createTextNode(version.name));return label;}));
    model.versionsHydrated=true;
    $('#trading-strategies').replaceChildren(...values.map(version=>{
      const card=node('article',undefined,'trading-version'),copy=node('div');copy.append(node('strong',version.name),node('small',versionID(version)+' · '+(version.active?'Active strategy':'Proposed / inactive')+' · '+when(version.created_at)));
      if(version.config){const configuration=node('details');configuration.append(node('summary','Review this version’s settings'),node('pre',JSON.stringify(version.config,null,2),'trading-detail'));copy.append(configuration);}
      const button=node('button',version.active?'Active':'Activate this version','secondary-button');button.type='button';button.disabled=Boolean(version.active);button.dataset.activateStrategy=versionID(version);
      button.onclick=()=>run(()=>api('/strategies/'+encodeURIComponent(versionID(version))+'/activate',{approved:true}),button);
      card.append(copy,button);return card;
    }));
    if(!values.length)$('#trading-strategies').append(node('p','No strategy versions are available.','trading-empty'));
  }
  const configFields={timeframe_minutes:'timeframe',risk_pct:'risk',daily_loss_pct:'daily-loss',weekly_loss_pct:'weekly-loss',max_positions:'max-positions',max_exposure_pct:'exposure',rr:'rr',spread:'spread',slippage:'slippage',commission_per_contract:'commission',break_even_r:'break-even',trailing_points:'trailing',partial_take_profit_r:'partial-r',partial_fraction:'partial-fraction'};
  function hydrate(config) {
    for(const [key,id] of Object.entries(configFields))if(config[key]!==undefined)$('#trading-'+id).value=config[key];
    $('#trading-backend').value=config.execution_backend || model.snapshot?.execution_backend || model.snapshot?.connection?.adapter || 'tradingview_desktop';
    $('#trading-import-timeframe').value=config.timeframe_minutes || 5;
    if(Array.isArray(config.sessions)){
      $('#trading-session-london').checked=config.sessions.some(item=>item.name==='London');
      $('#trading-session-new-york').checked=config.sessions.some(item=>item.name==='New York');
    }
    model.hydrated=true;
  }
  function renderConnection(tv) {
    const stages={application_not_found:'The installed application was not found.',desktop_unavailable:'Windows desktop access is unavailable.',accessibility_unavailable:'The app was found, but its controls could not be read.',paper_selection_required:'The app was found; select or connect the Paper Trading account.',account_data_unreadable:'The app was found, but its account values could not be read.',paper_verified:'Paper Trading selection was verified.',connection_error:'The connection check encountered an error.'};
    const stage=tv.stage==='paper_verified' && tv.verified!==true?'The previous Paper Trading observation needs a fresh check.':stages[tv.stage] || (tv.application_visible?'The TradingView app was observed; Paper Trading is not yet verified.':'Run Connect TradingView desktop to inspect the installed application.');
    $('#trading-connection-stage').textContent=stage+(tv.observed_at?' Last inspected '+when(tv.observed_at)+'.':'');
    const diagnostics=tv.diagnostics || {};
    function checks(target,values,empty) {
      target.replaceChildren(...(Array.isArray(values)?values:[]).filter(value=>value && typeof value==='object').slice(0,40).map(value=>{
        const status=['passed','failed'].includes(value.status)?value.status:'unknown';
        return node('li',(status==='passed'?'✓ ':status==='failed'?'× ':'◇ ')+display(value.name)+(value.detail?' · '+String(value.detail).slice(0,1200):''),status);
      }));
      if(!target.children.length && empty)target.append(node('li',empty));
    }
    checks($('#trading-connection-checks'),diagnostics.checks,'No measured connection checks have been returned yet.');
    checks($('#trading-connection-actions'),diagnostics.actions,'');
    const controls=(Array.isArray(diagnostics.controls)?diagnostics.controls:[]).filter(value=>value && typeof value==='object').slice(0,200).map(value=>({name:String(value.name || '').slice(0,800),type:String(value.type || '').slice(0,80),...(value.text!==undefined?{text:String(value.text).slice(0,1200)}:{})}));
    $('#trading-observed-controls').hidden=!controls.length;
    $('#trading-observed-control-data').textContent=JSON.stringify(controls,null,2);
    const imageID=tv.screenshot?.image_id,link=$('#trading-connection-screenshot');
    link.hidden=typeof imageID!=='string' || !/^[a-f0-9]{32}$/i.test(imageID);
    if(link.hidden)link.removeAttribute('href');else link.href='/api/screenshots/'+imageID;
  }
  function render(snapshot) {
    model.snapshot=snapshot;const connection=snapshot.connection || {},tv=connection.tradingview || {},simulator=connection.adapter==='independent_paper';
    const verified=!simulator && tv.verified===true && tv.connected===true;
    $('#trading-connection').textContent=simulator?'Independent paper simulator · separate ledger':verified?'TradingView desktop · Paper Trading verified':tv.application_visible?'TradingView desktop · app observed, Paper Trading not verified':'TradingView desktop · not verified';
    $('#trading-connection-led').classList.toggle('connected',verified);
    $('#trading-connection-detail').textContent=simulator?'Simulator fills and balances belong to JARVIS, separate from TradingView.':tv.reason || connection.reason || 'Connect the installed TradingView application and verify Paper Trading before submitting orders.';
    renderConnection(tv);
    $('#trading-mode').textContent=snapshot.mode || 'OFF';
    $('#trading-last-updated').textContent='Updated '+new Date().toLocaleTimeString();
    $('#trading-suspension').textContent=(snapshot.emergency_stop?'Emergency stop is set. ':'')+(snapshot.suspension_reason || '');
    $('#trading-suspension').hidden=!snapshot.emergency_stop && !snapshot.suspension_reason;
    $('#trading-reset').disabled=!snapshot.emergency_stop;
    $$('[data-trading-mode]').forEach(button=>{button.classList.toggle('active',button.dataset.tradingMode===snapshot.mode);button.setAttribute('aria-pressed',String(button.dataset.tradingMode===snapshot.mode));});
    const account=simulator || verified?snapshot.account || {}:{},summary=simulator?snapshot.metrics?.metrics || snapshot.metrics || {}:verified?tv.metrics || {}:{};
    const currency=simulator?'USD':typeof account.currency==='string'&&/^[A-Z]{3}$/.test(account.currency)?account.currency:null;
    const accountMoney=value=>number(value)===null?'—':currency?new Intl.NumberFormat(undefined,{style:'currency',currency,maximumFractionDigits:2}).format(Number(value)):decimal(value);
    const positions=simulator?snapshot.positions:Array.isArray(tv.positions)&&verified?tv.positions:null,orders=simulator?snapshot.orders:Array.isArray(tv.orders)&&verified?tv.orders:null;
    metrics($('#trading-account-metrics'),[[simulator?'Simulator balance':'Balance','balance',accountMoney],[simulator?'Simulator equity':'Equity','equity',accountMoney],['Available funds','available_funds',accountMoney],['Daily net P/L','daily_pnl',accountMoney],['Total net P/L','net_profit',accountMoney],['Open positions','open_positions',decimal]],{...summary,...account,open_positions:Array.isArray(positions)?positions.length:null});
    $('#trading-account-provenance').textContent=simulator?'Independent simulator account and history · USD. These figures do not describe TradingView.':verified?'Only directly observed TradingView account data is shown. '+(currency?'Account currency: '+currency+'.':'Account currency was not identified; balances are shown without an assumed currency.'):'TradingView balances, positions and order status are unknown until directly observed. Independent simulator history is available separately under Analytics and Journal.';
    const feeds=Array.isArray(snapshot.data)?snapshot.data:[];
    $('#trading-data-status').replaceChildren(...feeds.map(feed=>{const item=node('p');item.append(node('strong',feed.symbol+' · '+(feed.kind || 'historical')+(feed.stale?' · stale':'')),node('small',display(feed.count)+' bars · '+display(feed.source)+' · '+when(feed.start)+' → '+when(feed.end)));return item;}));
    if(!feeds.length)$('#trading-data-status').append(node('p','No authorised market data has been loaded. Live automation will wait for a verified fresh feed.','trading-note'));
    table($('#trading-positions'),[['Instrument','symbol'],['Side',item=>item.direction || item.side],['Contracts','quantity',decimal],['Entry',item=>item.entry_price || item.entry,decimal],['Stop','stop_loss',decimal],['Target','take_profit',decimal],['Unrealised P/L','unrealized_pnl',accountMoney]],positions,positions===null?'TradingView positions have not been read. Check the installed application.':'No open paper positions.');
    table($('#trading-orders'),[['Order','id'],['Instrument','symbol'],['Type',item=>item.order_type || item.type],['Side',item=>item.direction || item.side],['Contracts','quantity',decimal],['Status','status']],orders,orders===null?'TradingView order status has not been read. Check the installed application.':'No pending paper orders.');
    versions(snapshot);renderSetups(snapshot.setups || []);renderMarket();renderAnalytics();
    if(!model.hydrated)hydrate(snapshot.config || {});
    $('#trading-chart-period').textContent=display(snapshot.config?.timeframe_minutes || 5)+' MINUTE · RECORDED NQ BARS';
    const history=snapshot.recent_backtests || [];
    $('#trading-backtest-history').replaceChildren(...history.map(report=>{const button=node('button',display(report.name || report.id)+' · '+when(report.created_at),'text-button');button.type='button';button.onclick=()=>run(async()=>{model.report=await api('/backtests/'+encodeURIComponent(report.id));renderBacktest();renderAnalytics();return{message:'Backtest loaded.'};},button);return button;}));
  }
  async function refresh() {
    if(model.loading)return;model.loading=true;const request=++model.request;
    try{const snapshot=await api('');if(request===model.request)render(snapshot);}catch(error){notify(error.message,true);$('#trading-connection').textContent='Connection unavailable · refresh required';$('#trading-connection-led').classList.remove('connected');$('#trading-last-updated').textContent='Previous data may be stale.';}finally{model.loading=false;}
  }
  function panel(name) {model.tab=name;$$('[data-trading-tab]').forEach(button=>{const active=button.dataset.tradingTab===name;button.classList.toggle('active',active);button.setAttribute('aria-current',active?'page':'false');});$$('[data-trading-panel]').forEach(item=>item.hidden=item.dataset.tradingPanel!==name);root.scrollTop=0;if(name==='journal')loadJournal();if(name==='analytics')renderAnalytics();}
  function renderSetups(setups) {
    const fingerprint=JSON.stringify(setups);if(fingerprint===model.setupFingerprint)return;model.setupFingerprint=fingerprint;
    $('#trading-setups').replaceChildren(...setups.slice(0,30).map(setup=>{const item=node('details',undefined,'trading-setup'),summary=node('summary');summary.append(node('strong',display(setup.symbol || 'NQ')+' · '+display(setup.direction || 'No entry')),node('span',(setup.passed?'Qualified':'Rejected')+' · '+display(setup.rating)+'/100 · '+when(setup.time || setup.created_at)));item.append(summary);
      const conditions=setup.conditions || setup.evidence?.conditions || {};
      const list=node('ul',undefined,'trading-conditions');Object.entries(conditions).forEach(([name,condition])=>{const passed=typeof condition==='object'?condition.passed:condition===true;list.append(node('li',(passed?'✓ ':'× ')+name.replaceAll('_',' ')+': '+display(condition?.reason || condition),passed?'passed':'failed'));});
      item.append(list);if(!Object.keys(conditions).length)item.append(node('p',setup.reason || setup.rejection_reason || 'No detailed confirmations recorded.','trading-note'));return item;}));
    if(!setups.length)$('#trading-setups').append(node('p','No setups recorded. Market history is required before rules can be evaluated.','trading-empty'));
  }
  function svg(tag,attributes={}) {const item=document.createElementNS('http://www.w3.org/2000/svg',tag);Object.entries(attributes).forEach(([key,value])=>item.setAttribute(key,String(value)));return item;}
  function chartShell(target,label) {target.replaceChildren();const graphic=svg('svg',{viewBox:'0 0 760 260',role:'img','aria-label':label});target.append(graphic);return graphic;}
  function renderMarket() {
    const candles=(model.snapshot?.candles || []).filter(bar=>[bar.open,bar.high,bar.low,bar.close].every(value=>number(value)!==null)).slice(-120),target=$('#trading-market-chart');
    if(!candles.length){target.replaceChildren(node('p','No NQ bars available. Import authorised data to see price structure.','trading-empty'));$('#trading-chart-detail').textContent='The chart displays recorded data only.';return;}
    const high=Math.max(...candles.map(bar=>Number(bar.high))),low=Math.min(...candles.map(bar=>Number(bar.low))),range=high-low || 1;
    const y=value=>220-(Number(value)-low)*190/range,step=680/candles.length,graphic=chartShell(target,'Recorded NQ OHLC candles');
    for(let i=0;i<4;i++){const value=low+range*i/3;graphic.append(svg('line',{x1:45,y1:y(value),x2:730,y2:y(value),class:'trading-gridline'}));const label=svg('text',{x:3,y:y(value)+4,class:'trading-axis'});label.textContent=decimal(value);graphic.append(label);}
    candles.forEach((bar,index)=>{const x=50+(index+.5)*step,up=Number(bar.close)>=Number(bar.open),group=svg('g',{class:up?'trading-candle-up':'trading-candle-down',tabindex:0});const title=svg('title');title.textContent=when(bar.time)+' · O '+bar.open+' H '+bar.high+' L '+bar.low+' C '+bar.close;group.append(title,svg('line',{x1:x,x2:x,y1:y(bar.high),y2:y(bar.low)}),svg('rect',{x:x-Math.max(1,step*.3),y:Math.min(y(bar.open),y(bar.close)),width:Math.max(2,step*.6),height:Math.max(1,Math.abs(y(bar.open)-y(bar.close)))}));const describe=()=>$('#trading-chart-detail').textContent=title.textContent;group.onmouseenter=describe;group.onfocus=describe;graphic.append(group);});
    const setup=(model.snapshot?.setups || [])[0],evidence=setup?.evidence || {};
    const continuation=evidence.continuation || {},sweep=evidence.liquidity_sweep || evidence.sweep || {};
    for(const [name,zone] of [['FVG',continuation.fvg],['IFVG',continuation.ifvg]]){
      if(!zone || zone.valid===false || number(zone.lower)===null || number(zone.upper)===null || Number(zone.upper)<low || Number(zone.lower)>high)continue;
      const top=Math.min(high,Number(zone.upper)),bottom=Math.max(low,Number(zone.lower));
      graphic.append(svg('rect',{x:45,y:y(top),width:685,height:Math.max(1,y(bottom)-y(top)),class:'trading-zone '+(name==='IFVG'?'inverse':'')}));
      const label=svg('text',{x:53,y:y(top)+11,class:'trading-axis'});label.textContent=name+' '+decimal(zone.lower)+'–'+decimal(zone.upper);graphic.append(label);
    }
    const levels=[['Entry',setup?.entry],['Stop',setup?.stop_loss],['Target',setup?.take_profit],['Equilibrium',continuation.equilibrium?.entry || evidence.equilibrium?.level || evidence.equilibrium?.value],['Sweep',sweep.price || sweep.level],['BOS',evidence.bos?.confirmed?evidence.bos.structure?.price:undefined]];
    for(const [name,value] of levels){if(number(value)===null || Number(value)<low || Number(value)>high)continue;graphic.append(svg('line',{x1:45,x2:730,y1:y(value),y2:y(value),class:'trading-overlay'}));const label=svg('text',{x:53,y:y(value)-5,class:'trading-axis'});label.textContent=name+' '+decimal(value);graphic.append(label);}
    if(evidence.bos?.confirmed&&evidence.bos.time){const index=candles.findIndex(bar=>new Date(bar.time).valueOf()>=new Date(evidence.bos.time).valueOf());if(index>=0){const x=50+(index+.5)*step;graphic.append(svg('line',{x1:x,x2:x,y1:25,y2:225,class:'trading-overlay'}));const label=svg('text',{x:x+4,y:27,class:'trading-axis'});label.textContent='BOS close';graphic.append(label);}}
    $('#trading-chart-detail').textContent=when(candles[0].time)+' → '+when(candles.at(-1).time)+'. Hover or focus a candle to inspect prices. Levels and zones show the latest recorded setup when available.';
  }
  const analyticsDefinitions=[['Closed trades','total_trades'],['Wins','wins'],['Losses','losses'],['Break-even','break_even_trades'],['Win rate','win_rate',percent],['Net profit','net_profit',money],['Gross profit','gross_profit',money],['Gross loss','gross_loss',money],['Average win','average_win',money],['Average loss','average_loss',money],['Profit factor','profit_factor'],['Expected P/L per trade','expected_value_per_trade',money],['Planned risk/reward','average_planned_rr'],['Average realised R','average_realized_r'],['Maximum drawdown','max_drawdown',money],['Maximum drawdown · %','max_drawdown_pct',percent],['Average drawdown','average_drawdown',money],['Longest win streak','longest_winning_streak'],['Longest loss streak','longest_losing_streak'],['Recovery factor','recovery_factor'],['Sharpe ratio','sharpe_ratio'],['Sortino ratio','sortino_ratio'],['Average duration · seconds','average_trade_duration_seconds'],['Costs','trading_costs',money],['Equity growth','equity_growth',percent]];
  function analyticsSource() {return $('#trading-analytics-dataset').value==='backtest'?model.report:model.journalAnalytics?.dataset==='paper'?model.journalAnalytics:model.snapshot?.analytics || model.snapshot?.metrics || {};}
  function renderAnalytics() {
    const source=analyticsSource() || {},values=source.metrics || source,metricRoot=$('#trading-analytics-metrics');metrics(metricRoot,analyticsDefinitions,values);
    const kind=$('#trading-chart-select').value,series=source.charts?.[kind] || (kind==='equity_curve'?source.equity_curve:[]) || [];
    renderSeries($('#trading-analytics-chart'),series,kind);
    const assumptions=source.assumptions || [];$('#trading-analytics-notes').replaceChildren(node('p',$('#trading-analytics-dataset').value==='paper'?model.snapshot?.paper_history_label || 'Independent paper simulator history · not TradingView account history.':'Historical backtest results · separate from paper execution.'),...assumptions.map(item=>node('p',item)));
    if(!assumptions.length)$('#trading-analytics-notes').append(node('p','Unavailable ratios display —. A small sample cannot establish that a strategy is profitable.'));
  }
  function renderSeries(target,raw,kind) {
    const series=(Array.isArray(raw)?raw:[]).map(item=>({label:display(item.label || item.time),value:number(item.value ?? item.equity ?? item.net_pnl ?? item.drawdown),original:item})).filter(item=>item.value!==null);
    if(!series.length){target.replaceChildren(node('p','No recorded results for this chart.','trading-empty'));$('#trading-chart-readout').textContent='Select a backtest or collect closed paper trades to view results.';return;}
    const graphic=chartShell(target,$('#trading-chart-select').selectedOptions[0].textContent),min=Math.min(0,...series.map(item=>item.value)),max=Math.max(0,...series.map(item=>item.value)),range=max-min || 1,y=value=>225-(value-min)*190/range,step=680/Math.max(1,series.length);
    graphic.append(svg('line',{x1:45,y1:y(0),x2:735,y2:y(0),class:'trading-gridline'}));
    if(kind==='equity_curve'||kind==='drawdown'){const points=series.map((item,index)=>`${50+(index+.5)*step},${y(item.value)}`).join(' ');graphic.append(svg('polyline',{points,class:'trading-series-line'}));}
    series.forEach((item,index)=>{const x=50+(index+.5)*step,title=svg('title');title.textContent=item.label+': '+decimal(item.value);const point=kind==='equity_curve'||kind==='drawdown'?svg('circle',{cx:x,cy:y(item.value),r:4,class:'trading-chart-point',tabindex:0}):svg('rect',{x:x-step*.35,y:Math.min(y(0),y(item.value)),width:Math.max(1,step*.7),height:Math.max(1,Math.abs(y(0)-y(item.value))),class:item.value<0?'trading-chart-bar negative':'trading-chart-bar',tabindex:0});point.append(title);const describe=()=>$('#trading-chart-readout').textContent=title.textContent;point.onmouseenter=describe;point.onfocus=describe;point.onclick=describe;graphic.append(point);});
    [min,max].forEach(value=>{const label=svg('text',{x:3,y:y(value)+4,class:'trading-axis'});label.textContent=decimal(value);graphic.append(label);});
    $('#trading-chart-readout').textContent=series.length+' recorded points. Hover or focus a point to inspect its value.';
  }
  function renderBacktest() {
    const report=model.report;if(!report)return;const target=$('#trading-backtest-result');target.replaceChildren();target.append(node('p','Backtest '+display(report.id || report.run_id)+' · '+display(report.coverage?.count || report.coverage?.bars)+' available bars.'));
    const summary=node('div',undefined,'trading-metrics');metrics(summary,[['Closed trades','total_trades'],['Net profit','net_profit',money],['Win rate','win_rate',percent],['Maximum drawdown','max_drawdown',money]],report.metrics || {});target.append(summary);
    (report.assumptions || []).forEach(item=>target.append(node('p',item)));
    if(report.coverage)target.append(node('p','Coverage: '+JSON.stringify(report.coverage)));
  }
  function renderResearch(result) {
    const target=$('#trading-research-result');target.replaceChildren();
    const summary=result.summary || result.conclusion || 'Comparison completed. Review held-out results and sample sizes before activating a version.';
    target.append(node('p',typeof summary==='string'?summary:JSON.stringify(summary)));
    const names=new Map((model.snapshot?.strategy_versions || []).map(version=>[versionID(version),version.name]));
    if(Array.isArray(result.versions)){
      const holder=node('div',undefined,'trading-table-wrap');table(holder,[['Version',item=>names.get(String(item.id)) || item.id],['Training net P/L',item=>item.train?.metrics?.net_profit,money],['Held-out net P/L',item=>item.out_of_sample?.metrics?.net_profit,money],['Held-out trades',item=>item.out_of_sample?.trade_count,decimal],['Walk-forward folds',item=>item.walk_forward?.length,decimal]],result.versions,'No comparable versions.');target.append(holder);
    }
    for(const [label,entries] of [['Findings',result.findings],['Uncertainty & limitations',result.warnings]]){
      if(!Array.isArray(entries)||!entries.length)continue;target.append(node('h3',label));const list=node('ul');entries.forEach(entry=>list.append(node('li',typeof entry==='string'?entry:JSON.stringify(entry))));target.append(list);
    }
    if(result.monte_carlo?.versions?.length){target.append(node('h3','Monte Carlo · seeded simulations'));const holder=node('div',undefined,'trading-table-wrap');table(holder,[['Version',item=>names.get(String(item.id)) || item.id],['Net P/L · 5th percentile',item=>item.terminal_pnl?.p05,money],['Median',item=>item.terminal_pnl?.median,money],['Net P/L · 95th percentile',item=>item.terminal_pnl?.p95,money],['Loss probability',item=>item.loss_probability,decimal]],result.monte_carlo.versions,'No simulation results.');target.append(holder);}
    target.append(node('p','The active strategy is unchanged. Use its explicit Activate control only after reviewing the evidence.'));
    const detail=node('details');detail.append(node('summary','Full reproducible report'),node('pre',JSON.stringify(result,null,2),'trading-detail'));target.append(detail);
  }
  function dateRange(prefix,dateOnly=false) {
    const result={};for(const key of ['start','end']){const value=$('#'+prefix+'-'+key).value;if(value){const date=new Date(dateOnly?value+'T'+(key==='end'?'23:59:59.999':'00:00:00'):value);if(Number.isNaN(date.valueOf()))throw new Error('Choose a valid date range.');result[key]=date.toISOString();}}
    if(result.start&&result.end&&result.start>=result.end)throw new Error('The end must be after the start.');return result;
  }
  async function loadJournal() {
    try{const query=new URLSearchParams({dataset:$('#trading-journal-dataset').value,query:$('#trading-journal-query').value,...dateRange('trading-journal',true)});if(model.report?.id&&query.get('dataset')==='backtest')query.set('run_id',model.report.id);
      const result=await api('/journal?'+query);model.journal=result.trades || [];model.journalAnalytics=result;table($('#trading-journal'),[['Entry','entry_time',when],['Instrument','symbol'],['Direction',item=>item.direction || item.side],['Contracts','quantity',decimal],['Entry price',item=>item.entry_price || item.entry,decimal],['Exit','exit_price',decimal],['Net P/L','net_pnl',money],['Realised R',item=>item.realized_r ?? item.realised_r,decimal],['Exit reason','exit_reason']],model.journal,'No trades match these filters.',trade=>{$('#trading-trade-detail').textContent=JSON.stringify(trade,null,2);$('#trading-trade-detail').parentElement.open=true;});renderAnalytics();
    }catch(error){notify(error.message,true);}
  }
  async function downloadConnectionReport(button) {
    button.disabled=true;
    try {
      const report=await api('/connection/report'),blob=new Blob([JSON.stringify(report,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),link=node('a');
      link.href=url;link.download='JARVIS-TradingView-connection.json';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
      notify('Connection report downloaded.');
    } catch(error) { notify(error.message,true); }
    finally { button.disabled=false; }
  }
  async function exportReport(format,button) {
    button.disabled=true;try{const query=new URLSearchParams({dataset:$('#trading-journal-dataset').value,format,query:$('#trading-journal-query').value,...dateRange('trading-journal',true)});if(model.report?.id&&query.get('dataset')==='backtest')query.set('run_id',model.report.id);
      const response=await fetch('/api/trading/export?'+query,{credentials:'same-origin'});if(!response.ok){const result=await response.json();throw new Error(result.detail || 'Export failed.');}const url=URL.createObjectURL(await response.blob()),link=node('a');link.href=url;link.download='JARVIS-'+query.get('dataset')+'-journal.'+format;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);notify('Report exported.');
    }catch(error){notify(error.message,true);}finally{button.disabled=false;}
  }
  $$('[data-trading-tab]').forEach(button=>button.onclick=()=>panel(button.dataset.tradingTab));
  $$('[data-trading-mode]').forEach(button=>button.onclick=()=>run(()=>api('/control',{action:'mode',mode:button.dataset.tradingMode}),button));
  $('#trading-refresh').onclick=()=>refresh();
  $('#trading-emergency').onclick=event=>run(()=>api('/control',{action:'emergency',close_positions:$('#trading-emergency-close').checked}),event.currentTarget);
  $('#trading-reset').onclick=event=>run(()=>api('/control',{action:'reset_emergency'}),event.currentTarget);
  $('#trading-cancel-orders').onclick=event=>run(()=>api('/control',{action:'cancel_orders'}),event.currentTarget);
  $('#trading-close-positions').onclick=event=>run(()=>api('/control',{action:'close_positions'}),event.currentTarget);
  $('#trading-connect').onclick=event=>run(async()=>{const result=await api('/control',{action:'connect'});return{...result,message:result.message || result.reason || 'Connection check completed.'};},event.currentTarget);
  $('#trading-download-connection-report').onclick=event=>downloadConnectionReport(event.currentTarget);
  $('#trading-backend-save').onclick=event=>run(()=>api('/config',{execution_backend:$('#trading-backend').value},'PATCH'),event.currentTarget);
  $('#trading-config-form').onsubmit=event=>{event.preventDefault();const changes={};Object.entries(configFields).forEach(([key,id])=>changes[key]=Number($('#trading-'+id).value));changes.sessions=[];if($('#trading-session-london').checked)changes.sessions.push({name:'London',timezone:'Europe/London',open:'08:00',weekdays:[0,1,2,3,4]});if($('#trading-session-new-york').checked)changes.sessions.push({name:'New York',timezone:'America/New_York',open:'09:30',weekdays:[0,1,2,3,4]});run(async()=>{const result=await api('/config',changes,'PATCH');return{...result,message:'Configuration proposed. Activate its version explicitly in Strategy research.'};},event.submitter);};
  $('#trading-import-form').onsubmit=event=>{event.preventDefault();run(async()=>{const file=$('#trading-import-file').files[0];if(file&&file.size>10*1024*1024)throw new Error('Use a CSV smaller than 10 MB.');const csv=file?await file.text():$('#trading-import-csv').value;if(!csv.trim())throw new Error('Choose a CSV file or paste its contents.');return api('/data',{symbol:$('#trading-import-symbol').value,source:$('#trading-import-source').value,kind:$('#trading-import-kind').value,timeframe_minutes:Number($('#trading-import-timeframe').value),csv});},event.submitter);};
  $('#trading-backtest-form').onsubmit=event=>{event.preventDefault();run(async()=>{model.report=await api('/backtests',{symbol:'NQ',strategy_id:$('#trading-backtest-strategy').value,initial_equity:$('#trading-backtest-equity').value,...dateRange('trading-backtest')});renderBacktest();$('#trading-analytics-dataset').value='backtest';renderAnalytics();return{message:'Backtest completed. Results use recorded history and stated assumptions.'};},event.submitter);};
  $('#trading-journal-form').onsubmit=event=>{event.preventDefault();loadJournal();};
  $('#trading-journal-dataset').onchange=()=>loadJournal();
  $$('[data-trading-export]').forEach(button=>button.onclick=()=>exportReport(button.dataset.tradingExport,button));
  $('#trading-analytics-dataset').onchange=()=>{if($('#trading-analytics-dataset').value==='paper'){$('#trading-journal-dataset').value='paper';loadJournal();}else renderAnalytics();};
  $('#trading-chart-select').onchange=renderAnalytics;
  $('#trading-strategy-form').onsubmit=event=>{event.preventDefault();const entry=$('#trading-strategy-entry').value;run(()=>api('/strategies',{name:$('#trading-strategy-name').value,changes:{rr:Number($('#trading-strategy-rr').value),entry_types:entry==='all'?['fvg','ifvg','equilibrium']:[entry],fvg_entry_fraction:Number($('#trading-strategy-fvg').value),require_rsi:$('#trading-strategy-rsi').checked,require_smt:$('#trading-strategy-smt').checked}}),event.submitter);};
  $('#trading-research-form').onsubmit=event=>{event.preventDefault();run(async()=>{const strategy_ids=$$('#trading-research-versions input:checked').map(input=>input.value);if(strategy_ids.length<2)throw new Error('Choose at least two strategy versions for comparison.');const result=await api('/research',{strategy_ids,...dateRange('trading-research')});renderResearch(result);return{message:'Research completed; the active strategy is unchanged.'};},event.submitter);};
  const pause=()=>{clearInterval(model.timer);model.timer=null;};
  const open=()=>{pause();refresh();model.timer=setInterval(()=>{if(!document.hidden&&!root.hidden)refresh();},5000);};
  window.JarvisTrading={open,pause,refresh};
  document.addEventListener('visibilitychange',()=>{if(document.hidden)pause();else if(!root.hidden)open();});
  window.addEventListener('pagehide',pause);
})();
