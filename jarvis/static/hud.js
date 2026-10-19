/* SVG instrument display and a graph built exclusively from recorded workflow events. */
(() => {
  'use strict';
  const ns = 'http://www.w3.org/2000/svg';
  const svgNode = (tag, attrs = {}) => { const el = document.createElementNS(ns, tag); for (const [key,value] of Object.entries(attrs)) el.setAttribute(key,value); return el; };
  const point = (radius, angle) => [300 + radius*Math.cos(angle*Math.PI/180), 300 + radius*Math.sin(angle*Math.PI/180)];
  const arc = (radius,start,end) => { const a=point(radius,start), b=point(radius,end); return `M${a} A${radius},${radius} 0 ${end-start>180?1:0},1 ${b}`; };
  const display = document.querySelector('#jarvis-hud');
  const svg = svgNode('svg', {viewBox:'0 0 600 600', 'aria-hidden':'true', class:'hud-instrument'});
  display.prepend(svg);
  const add = (parent,tag,attrs) => { const el=svgNode(tag,attrs); parent.append(el); return el; };
  add(svg,'circle',{cx:300,cy:300,r:221,class:'hud-glass'});
  const dots=add(svg,'g',{class:'hud-dots hud-rotate-slow'});
  for(let i=0;i<90;i++){const p=point(270,i*4);add(dots,'circle',{cx:p[0],cy:p[1],r:i%5===0?2.5:1.7});}
  const ticks=add(svg,'g',{class:'hud-ticks hud-rotate-reverse'});
  for(let i=0;i<240;i++){const a=point(i%10===0?249:253,i*1.5),b=point(260,i*1.5);add(ticks,'path',{d:`M${a} L${b}`});}
  const outer=add(svg,'g',{class:'hud-ring hud-rotate'});
  add(outer,'circle',{cx:300,cy:300,r:240,class:'hud-outline'});
  [[-90,-38],[-30,45],[57,135],[147,215],[228,255]].forEach(([start,end])=>add(outer,'path',{d:arc(235,start,end),class:'hud-band'}));
  for(let i=0;i<60;i++){
    if(i%15>10)continue;
    const a=point(i%3===0?200:213,i*6),b=point(230,i*6);add(outer,'path',{d:`M${a} L${b}`,class:'hud-comb'});
  }
  const middle=add(svg,'g',{class:'hud-ring hud-rotate-reverse'});
  [[-90,-10],[12,75],[112,176],[202,259]].forEach(([start,end])=>add(middle,'path',{d:arc(185,start,end),class:'hud-mid-band'}));
  for(let i=0;i<56;i++){if(i%14>9)continue;add(middle,'path',{d:arc(173,i*360/56,i*360/56+3.4),class:'hud-segment'});}
  const inner=add(svg,'g',{class:'hud-ring hud-rotate-inner'});
  add(inner,'circle',{cx:300,cy:300,r:134,class:'hud-inner-outline'});
  [[-82,-20],[5,52],[78,155],[180,235],[262,278]].forEach(([start,end])=>add(inner,'path',{d:arc(126,start,end),class:'hud-inner-band'}));
  for(let i=0;i<72;i++){if(i%18>12)continue;const a=point(112,i*5),b=point(121,i*5);add(inner,'path',{d:`M${a} L${b}`,class:'hud-inner-comb'});}
  const levels=add(svg,'g',{class:'hud-levels'}),bars=[];
  for(let i=0;i<48;i++){const bar=add(levels,'path',{class:'hud-level'});bars.push(bar);}
  const target=add(svg,'g',{class:'hud-target'});
  [[-72,-20],[20,72],[108,160],[200,252]].forEach(([start,end])=>add(target,'path',{d:arc(87,start,end)}));
  [[286,300,294,300],[306,300,314,300],[300,286,300,294],[300,306,300,314]].forEach(p=>add(target,'path',{d:`M${p[0]},${p[1]}L${p[2]},${p[3]}`}));
  let phase='off',running=false,level=0,pulse=0,last=0,raf=0;
  let audioContext=null,stream=null,analyser=null,samples=null,meterGeneration=0;
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  const label=document.querySelector('#hud-status'),hint=document.querySelector('#hud-hint');
  function stopMeter(){meterGeneration++;stream?.getTracks().forEach(track=>track.stop());stream=null;analyser=null;samples=null;audioContext?.close().catch(()=>{});audioContext=null;}
  async function startMeter(){
    if(stream||audioContext||!navigator.mediaDevices?.getUserMedia)return;
    const generation=++meterGeneration;
    try{
      const captured=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true},video:false});
      if(generation!==meterGeneration||phase!=='listening'){captured.getTracks().forEach(track=>track.stop());return;}
      stream=captured;audioContext=new (window.AudioContext||window.webkitAudioContext)();
      analyser=audioContext.createAnalyser();analyser.fftSize=256;samples=new Uint8Array(analyser.fftSize);
      audioContext.createMediaStreamSource(stream).connect(analyser);await audioContext.resume();
    }catch{stopMeter();} // The visual meter is optional; speech recognition still works without it.
  }
  function updateLabels(){
    display.dataset.phase=phase==='off'&&running?'thinking':phase;
    label.textContent={off:running?'PROCESSING':'STANDING BY',starting:'CONNECTING',listening:'LISTENING',thinking:'PROCESSING',speaking:'JARVIS SPEAKING',approval:'AWAITING APPROVAL',paused:'CONVERSATION PAUSED'}[phase]||'STANDING BY';
    hint.textContent=phase==='off'?'Click the circle once to start talking':phase==='listening'?'Speak naturally. Pause when you’re finished.':phase==='speaking'?'Listening resumes after my reply.':phase==='approval'?'Review the requested action in chat.':phase==='thinking'?'Say “cancel task” to stop.':'Your conversation stays here.';
  }
  function frame(now){
    raf=requestAnimationFrame(frame);
    if(document.hidden||now-last<45)return;last=now;
    let target=0;
    if(analyser&&samples){analyser.getByteTimeDomainData(samples);target=Math.min(1,Math.sqrt(samples.reduce((sum,v)=>sum+((v-128)/128)**2,0)/samples.length)*6);}
    pulse*=.84;
    level+=(Math.max(target,pulse)-level)*.25;
    const active=['listening','speaking'].includes(phase), fallback=active&&!analyser?.context?.state?.includes('running');
    const energy=reduced.matches?0:Math.max(level,fallback ? .12 : 0);
    svg.style.setProperty('--hud-energy',energy.toFixed(3));
    bars.forEach((bar,i)=>{const length=3+(reduced.matches?0:energy*(9+8*Math.sin(i*1.7+now/210)**2));const a=point(99,i*7.5),b=point(99+length,i*7.5);bar.setAttribute('d',`M${a}L${b}`);});
  }
  requestAnimationFrame(frame);
  const rail=document.querySelector('#right-panel'),day=document.createElement('div');day.id='day-rail';
  while(rail.firstChild)day.append(rail.firstChild);day.hidden=true;
  const tabs=document.createElement('div');tabs.className='rail-tabs';tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','Workspace panels');
  const flowPanel=document.createElement('section');flowPanel.id='flow-rail';flowPanel.setAttribute('role','tabpanel');
  flowPanel.innerHTML='<div class="flow-heading"><span class="flow-live-dot"></span> LIVE TASK FLOW <span class="flow-session">LOCAL</span></div><h2>Thought into action.</h2><p class="flow-intro">Follow the work as it happens.</p><div class="flow-canvas" data-flow-graph></div><button type="button" class="rail-link" data-flow-activity>Open full activity <span>↗</span></button>';
  ['Task flow','Your day'].forEach((name,i)=>{const button=document.createElement('button');button.type='button';button.textContent=name;button.id=i?'day-tab':'flow-tab';button.setAttribute('role','tab');button.setAttribute('aria-controls',i?'day-rail':'flow-rail');button.setAttribute('aria-selected',String(!i));button.onclick=()=>{day.hidden=!i;flowPanel.hidden=!!i;[...tabs.children].forEach((b,index)=>b.setAttribute('aria-selected',String(index===i)));drawAll();};tabs.append(button);});
  day.setAttribute('role','tabpanel');day.setAttribute('aria-labelledby','day-tab');flowPanel.setAttribute('aria-labelledby','flow-tab');rail.append(tabs,flowPanel,day);
  flowPanel.querySelector('[data-flow-activity]').onclick=()=>document.querySelector('[data-view="activity"]').click();
  let workflow=null,actions=[];
  const statusText=status=>String(status||'pending').replaceAll('_',' ');
  function box(root,title,subtitle,status,id,kind){
    const node=document.createElement('div');node.className='flow-node '+kind;node.dataset.status=status||'pending';if(id)node.dataset.actionId=id;
    const badge=document.createElement('span');badge.className='flow-node-icon';badge.textContent=kind==='flow-root'?'◈':status==='completed'?'✓':status==='failed'?'!':status==='awaiting_confirmation'?'Ⅱ':status==='cancelled'?'■':'◇';
    const copy=document.createElement('div'),name=document.createElement('strong'),detail=document.createElement('small');name.textContent=title;detail.textContent=subtitle;copy.append(name,detail);node.append(badge,copy);root.append(node);return node;
  }
  function draw(root){
    root.replaceChildren();root.classList.toggle('flow-empty',!workflow);
    if(!workflow){const message=document.createElement('p');message.textContent='Your next request starts here. Connected actions appear as JARVIS works.';root.append(message);return;}
    const lines=svgNode('svg',{'aria-hidden':'true',class:'flow-lines'});root.append(lines);
    const nodes=document.createElement('div');nodes.className='flow-nodes';root.append(nodes);
    const request=box(nodes,'YOUR REQUEST',workflow.request||'Current task','received','','flow-root');
    const hub=box(nodes,'J.A.R.V.I.S.',statusText(workflow.status),workflow.status||'running','','flow-hub');
    let plan=workflow.plan||[];if(typeof plan==='string'){try{plan=JSON.parse(plan);}catch{plan=[];}}
    if(plan.length)hub.title=plan.join('\n');
    const branches=document.createElement('div');branches.className='flow-branches';nodes.append(branches);
    const shown=actions.filter(action=>action.workflow_id===workflow.id||!action.workflow_id);
    const children=shown.map(action=>box(branches,statusText(action.name),statusText(action.status),action.status,action.id,'flow-action'));
    if(!children.length){const wait=document.createElement('p');wait.className='flow-wait';wait.textContent=workflow.status==='running'?'Planning the next step…':'No tool actions recorded for this conversation turn.';branches.append(wait);}
    const bounds=root.getBoundingClientRect();if(!bounds.width)return;
    lines.setAttribute('viewBox',`0 0 ${bounds.width} ${root.scrollHeight}`);
    function connect(a,b,status){const ar=a.getBoundingClientRect(),br=b.getBoundingClientRect(),x1=ar.left+ar.width/2-bounds.left,y1=ar.bottom-bounds.top,x2=br.left+br.width/2-bounds.left,y2=br.top-bounds.top;add(lines,'path',{d:`M${x1} ${y1} C${x1} ${y1+18},${x2} ${y2-18},${x2} ${y2}`,class:'flow-edge','data-status':status});}
    connect(request,hub,workflow.status);children.forEach((node,i)=>connect(hub,node,shown[i].status));
  }
  const drawAll=()=>document.querySelectorAll('[data-flow-graph]').forEach(draw);
  const resize=new ResizeObserver(()=>drawAll());resize.observe(rail);const full=document.querySelector('#activity-flow');if(full)resize.observe(full);
  window.JarvisHUD={
    setVoice(state){phase=state.phase;updateLabels();if(phase==='listening'&&!document.hidden)startMeter();else stopMeter();},
    pulse(){pulse=.5+Math.random()*.4;},
    setBusy(value){running=value;updateLabels();},
    updateWorkflow(value,recorded=[]){workflow=value;actions=recorded;drawAll();},
  };
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stopMeter();else if(phase==='listening')startMeter();});
  window.addEventListener('pagehide',()=>{stopMeter();cancelAnimationFrame(raf);resize.disconnect();});
  updateLabels();drawAll();
})();
