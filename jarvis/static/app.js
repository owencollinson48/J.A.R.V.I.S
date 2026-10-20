const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const icons = {
  chat: '<path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9H13a8.5 8.5 0 0 1 8 8v.5Z"/>',
  check: '<rect x="3" y="3" width="18" height="18" rx="5"/><path d="m8 12 3 3 5-6"/>',
  folder: '<path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/>',
  memory: '<path d="M9 4a3 3 0 0 0-5 2 4 4 0 0 0-1 7 4 4 0 0 0 3 6 3 3 0 0 0 6 0V5a3 3 0 0 0-3-3m6 2a3 3 0 0 1 5 2 4 4 0 0 1 1 7 4 4 0 0 1-3 6 3 3 0 0 1-6 0"/><path d="M6 9a3 3 0 0 1 3 3m9-3a3 3 0 0 0-3 3M5 16h3m8 0h3"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
  settings: '<path d="m10 3-1 3-3 1-3 3 2 2-1 4 3 1 2 4h5l1-3 3-1 3-3-2-2 1-4-3-1-2-4h-5Z"/><circle cx="12" cy="12" r="3"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9m-11 9a3 3 0 0 0 6 0"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
  volume: '<path d="m11 4-5 4H3v8h3l5 4V4Zm5 4a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  mic: '<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2m-7 9v3m-4 0h8"/>',
  arrow: '<path d="M12 19V5m-5 5 5-5 5 5"/>',
  'arrow-up': '<path d="m5 12 7-7 7 7M12 5v15"/>',
  search: '<circle cx="10.5" cy="10.5" r="7.5"/><path d="m16 16 5 5"/>',
  sparkles: '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3ZM20 2v4m-2-2h4"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Z"/><path d="M14 2v6h6M8 13h8m-8 4h6"/>',
};
const icon = name => `<svg viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.file}</svg>`;
function hydrateIcons(root = document) { $$('[data-icon]', root).forEach(el => { el.innerHTML = icon(el.dataset.icon); }); }
hydrateIcons();
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {view:'chat', conversation:null, conversations:[], tasks:[], goals:[], reminders:[], memories:[], settings:null, busy:false, planner:'tasks', approvals:[], currentPath:'', previewPath:'', editing:null, speaking:localStorage.getItem('jarvis-speak') === 'true', notified:new Set(), refreshing:false};
let voiceSession = null;
Object.assign(state, {active:[], workflow:null, flowActions:[], applications:[], appRequest:0, visual:null, taskCheckpoint:null});
let toastTimer;
function toast(text) { $('#toast').textContent = text; $('#toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4200); }
async function api(path, options = {}) {
  const response = await fetch('/api' + path, { ...options, headers:{'Content-Type':'application/json', 'X-Jarvis-Client':'dashboard', ...options.headers} });
  if (!response.ok) { let error; try { error = await response.json(); } catch { error = {}; } throw new Error(typeof error.detail === 'string' ? error.detail : `Request failed (${response.status})`); }
  return response.json();
}
const post = (path, data = {}) => api(path, {method:'POST', body:JSON.stringify(data)});
const patch = (path, data) => api(path, {method:'PATCH', body:JSON.stringify(data)});
const remove = path => api(path, {method:'DELETE'});
function showVisual(visual) {
  state.visual = visual || null;
  if (visual) JarvisVisual.show(visual); else JarvisVisual.clear();
}
document.addEventListener('jarvis:visual-dismiss', async event => {
  const conversation = state.conversation;
  const visual = state.visual;
  if (!conversation || !visual || event.detail?.id !== visual.id) return;
  state.visual = null;
  try { await remove('/conversations/' + conversation + '/visuals/' + visual.id); }
  catch(error) {
    if (state.conversation === conversation && !state.visual) showVisual(visual);
    errorToast(error);
  }
});
const formatDate = value => value ? new Date(value).toLocaleString(undefined, {month:'short', day:'numeric', hour:'numeric', minute:'2-digit'}) : 'Whenever you’re ready';
function relativeDue(value) {
  if (!value) return 'No due date';
  const date = new Date(value), today = new Date();
  const clock = date.toLocaleTimeString(undefined, {hour:'numeric', minute:'2-digit'});
  if (date.toDateString() === today.toDateString()) return `Today, ${clock}`;
  today.setDate(today.getDate() + 1);
  return date.toDateString() === today.toDateString() ? `Tomorrow, ${clock}` : formatDate(value);
}
function empty(title, description, symbol = 'sparkles') { return `<div class="empty-state">${icon(symbol)}<h3>${escapeHTML(title)}</h3>${escapeHTML(description)}</div>`; }
function setDates() {
  const date = new Date();
  $('#today-date').textContent = date.toLocaleDateString(undefined, {month:'short', day:'numeric', year:'numeric'});
  $('#day-label').textContent = date.toLocaleDateString(undefined, {weekday:'long'}).toUpperCase();
  $('#day-date').textContent = date.toLocaleDateString(undefined, {month:'long', day:'numeric'});
}
setDates();
setInterval(setDates, 60000);

function showView(view) {
  state.view = view;
  document.body.dataset.view = view;
  if(view !== 'chat') setConversationOpen(false);
  $$('.main-column > main').forEach(el => { el.hidden = el.id !== view + '-view'; });
  $$('.nav-item').forEach(el => el.classList.toggle('active', el.dataset.view === view));
  $('#view-title').textContent = {chat:'Assistant', planner:'My planner', files:'Computer', memory:'Memory', settings:'Settings', applications:'Applications', activity:'Activity', trading:'Trading Control Centre'}[view];
  $('#sidebar').classList.remove('open'); $('#menu-button').setAttribute('aria-expanded','false');
  if (view === 'files' && !state.currentPath) loadFiles();
  if (view === 'planner') renderPlanner();
  if (view === 'memory') renderMemories();
  if (view === 'settings') populateSettings();
  if (view === 'applications') loadApplications().catch(errorToast);
  if (view === 'activity') loadActivity().catch(errorToast);
  if (view === 'trading') window.JarvisTrading?.open();
  else window.JarvisTrading?.pause();
  voiceSession?.refreshContext();
  if(view==='activity') JarvisHUD.updateWorkflow(state.workflow,state.flowActions);
  if(view==='chat') JarvisHUD.showTaskFlow();
}
$$('button[data-view]').forEach(el => el.addEventListener('click', () => showView(el.dataset.view)));
$$('[data-open-view]').forEach(el => el.addEventListener('click', () => showView(el.dataset.openView)));
$('#menu-button').addEventListener('click', () => { const open=$('#sidebar').classList.toggle('open'); $('#menu-button').setAttribute('aria-expanded',String(open)); });
function setConversationOpen(open) {
  $('#conversation-panel').hidden = !open;
  $('#conversation-toggle').setAttribute('aria-expanded',String(open));
  if(open) { scrollChat(); if(!state.busy) $('#chat-input').focus(); }
  else if(document.activeElement && $('#conversation-panel').contains(document.activeElement)) $('#conversation-toggle').focus();
}
$('#conversation-toggle').onclick = () => { showView('chat'); setConversationOpen($('#conversation-panel').hidden); };
$('#close-conversation').onclick = () => setConversationOpen(false);
$('#quick-settings').onclick = () => showView('settings');
document.addEventListener('keydown', event => {
  if(event.key==='Escape' && !state.busy && !state.active.length && !state.approvals.length) { setConversationOpen(false); $('#sidebar').classList.remove('open'); $('#menu-button').setAttribute('aria-expanded','false'); }
});
$('#notifications-button').addEventListener('click', () => { state.planner = 'reminders'; showView('planner'); });
$('#access-settings').addEventListener('click', () => showView('settings'));
$('#attach-button').addEventListener('click', () => showView('files'));

function renderConversations() {
  const root = $('#conversation-list');
  root.innerHTML = state.conversations.length ? state.conversations.map(c => `<div class="conversation-item ${c.id === state.conversation ? 'active' : ''}"><button data-conversation="${c.id}" title="${escapeHTML(c.title)}">${escapeHTML(c.title)}</button><button class="delete-chat" data-delete-conversation="${c.id}" aria-label="Delete conversation">×</button></div>`).join('') : '<p class="sidebar-empty">A fresh start. What’s on your mind?</p>';
  $$('[data-conversation]', root).forEach(el => el.onclick = () => loadConversation(el.dataset.conversation).catch(errorToast));
  $$('[data-delete-conversation]', root).forEach(el => el.onclick = async () => {
    if (state.settings?.permissions_mode !== 'autonomous' && !confirm('Delete this conversation? Saved memories and planner items will remain.')) return;
    try { await remove('/conversations/' + el.dataset.deleteConversation); if (state.conversation === el.dataset.deleteConversation) newChat(); await refresh(); } catch(e) { errorToast(e); }
  });
}
function newChat() {
  if (state.busy) { toast('Let this conversation finish first.'); return; }
  voiceSession?.stop();
  state.conversation = null; state.approvals = []; localStorage.removeItem('jarvis-conversation');
  showVisual(null);
  state.taskCheckpoint = null;
  state.workflow = null; state.flowActions = []; renderWorkflow();
  $('#messages').innerHTML = ''; $('#approval-list').innerHTML = ''; $('#welcome').hidden = false; $('#chat-error').hidden = true;
  renderConversations(); showView('chat'); setConversationOpen(false);
}
$('#new-chat').onclick = newChat;
document.addEventListener('keydown', event => { if (event.key.toLowerCase() === 'n' && !event.ctrlKey && !event.metaKey && !event.altKey && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)) { event.preventDefault(); newChat(); } });
function formattedText(text) {
  return text.split(/(```[\s\S]*?```)/g).map(part => {
    if (part.startsWith('```')) return '<pre>' + escapeHTML(part.slice(3, -3).replace(/^\w*\n/, '')) + '</pre>';
    return escapeHTML(part).replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>').replace(/`([^`\n]+)`/g, '<code>$1</code>');
  }).join('');
}
function appendMessage(role, content = '', streaming = false) {
  const el = document.createElement('article');
  el.className = `message ${role}${streaming ? ' streaming' : ''}`;
  el.innerHTML = `<div class="message-avatar">${role === 'user' ? escapeHTML((state.settings?.name || 'You')[0].toUpperCase()) : 'J'}</div><div class="message-body"><div class="message-label">${role === 'user' ? escapeHTML(state.settings?.name || 'You') : 'JARVIS'}</div><div class="message-text"></div></div>`;
  $('.message-text', el).innerHTML = formattedText(content); $('#messages').append(el); return el;
}
function scrollChat() { const el = $('#chat-scroll'); el.scrollTop = el.scrollHeight; }
async function loadConversation(id, force = false) {
  if (state.busy && !force) { toast('Let this conversation finish first.'); return; }
  if (state.conversation !== id) voiceSession?.stop();
  const history = await api('/conversations/' + id);
  state.conversation = id; localStorage.setItem('jarvis-conversation', id); state.approvals = history.approvals;
  showVisual(history.visual);
  state.taskCheckpoint = history.task_checkpoint || null;
  state.workflow = history.workflow || null; state.flowActions = history.actions || []; renderWorkflow();
  $('#messages').innerHTML = ''; $('#welcome').hidden = voiceSession?.enabled || history.messages.some(m => ['user','assistant'].includes(m.role) && m.content);
  history.messages.filter(m => ['user','assistant'].includes(m.role) && m.content).forEach(m => appendMessage(m.role, m.content));
  renderApprovals(); renderConversations(); showView('chat'); scrollChat();
  if (history.active && !state.busy) { showWorking('Finishing your last request'); scheduleRecovery(id); }
}
let recoveryTimer;
function scheduleRecovery(id) {
  clearTimeout(recoveryTimer);
  recoveryTimer = setTimeout(async () => {
    if (state.conversation !== id || state.busy) return;
    try { const data = await api('/conversations/' + id); if (data.active) scheduleRecovery(id); else await loadConversation(id); } catch(e) { errorToast(e); }
  }, 1800);
}
function setBusy(value) { JarvisHUD.setBusy(value); state.busy = value; $('#send-button').disabled = value; $('#chat-input').disabled = value; $('#new-chat').disabled = value; $$('[data-approval]').forEach(el => el.disabled = value); updateStopButton(); voiceSession?.refreshContext(); }
function showWorking(text) {
  let el = $('#working');
  if (!el) { el = document.createElement('div'); el.id = 'working'; el.className = 'working'; $('#messages').append(el); }
  el.textContent = text;
}
async function stream(path, body) {
  setBusy(true); $('#chat-error').hidden = true;
  let current = null, partial = '';
  const outcome = {text:'', error:'', approvals:0};
  try {
    const response = await fetch('/api' + path, {method:'POST', headers:{'Content-Type':'application/json', 'X-Jarvis-Client':'dashboard'}, body:JSON.stringify(body)});
    if (!response.ok) { const data = await response.json(); throw new Error(data.detail || 'Unable to send request'); }
    const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = '', doneEvent = false;
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream:true});
      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        const block = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
        if (!block.startsWith('data: ')) continue;
        const event = JSON.parse(block.slice(6));
        if (event.type === 'token') { $('#working')?.remove(); if (!current) current = appendMessage('assistant', '', true); partial += event.text; $('.message-text', current).textContent = partial; scrollChat(); }
        if (event.type === 'message') { if (current) { current.classList.remove('streaming'); $('.message-text', current).innerHTML = formattedText(event.content); } else if (event.content) appendMessage('assistant', event.content); outcome.text = event.content; current = null; partial = ''; }
        if (event.type === 'status') { showWorking(event.text); scrollChat(); }
        if (event.type === 'workflow') { if(state.workflow?.id !== event.id) state.flowActions = []; state.workflow = {id:event.id,status:event.status,request:body.content || 'Continuing your task',plan:[]}; state.active = [state.conversation]; renderWorkflow(); updateStopButton(); }
        if (event.type === 'plan') { state.workflow = {...state.workflow,detail:event.outcome,plan:event.steps}; renderWorkflow(); $('#workflow-card').open = true; }
        if (event.type === 'visual') { showVisual(event.visual); }
        if (event.type === 'checkpoint') { state.taskCheckpoint = event.checkpoint; }
        if (event.type === 'action') { const update=event.action; const index=state.flowActions.findIndex(action=>action.id===update.id); if(index<0) state.flowActions.push(update); else state.flowActions[index]={...state.flowActions[index],...update}; renderWorkflow(); }
        if (event.type === 'tool') { const action=state.flowActions.find(action=>action.id===event.action_id); if(action) action.status=event.status || (event.ok?'completed':'failed'); renderWorkflow(); showWorking(event.ok ? `${event.name.replaceAll('_',' ')} · done` : `${event.name.replaceAll('_',' ')} · not completed`); }
        if (event.type === 'approval') { state.approvals.push(event.approval); renderApprovals(); scrollChat(); }
        if (event.type === 'error') { toast(event.message); outcome.error = event.message; $('#chat-error').textContent = event.message; $('#chat-error').hidden = false; }
        if (event.type === 'cancelled') { outcome.text = event.message; outcome.cancelled = true; showWorking('Task cancelled'); }
        if (event.type === 'outcome' && event.workflow) {
          state.workflow=event.workflow;
          if (Array.isArray(event.approvals)) { state.approvals=event.approvals; renderApprovals(); }
          outcome.received=true; renderWorkflow();
        }
        if (event.type === 'done') { doneEvent = true; state.active = []; }
      }
    }
    if (!doneEvent) throw new Error('Connection interrupted. Refresh this conversation to see the saved result.');
  } catch(error) { toast(error.message); outcome.error = error.message; $('#chat-error').textContent = error.message; $('#chat-error').hidden = false; }
  finally {
    $('#working')?.remove(); setBusy(false); const view = state.view;
    try {
      // A completed stream already contains the final answer, approvals and
      // observed task outcome. Sidebar/history requests must not delay speech.
      if (outcome.received) { refresh().catch(errorToast); }
      else { if (state.conversation) await loadConversation(state.conversation, true); if (view !== 'chat') showView(view); await refresh(); }
    }
    catch (error) { outcome.error ||= error.message; errorToast(error); }
    outcome.approvals = state.approvals.length;
    updateStopButton();
    if (state.view === 'chat' && !voiceSession?.enabled && !$('#conversation-panel').hidden) $('#chat-input').focus();
  }
  return outcome;
}
async function deliverReply(result, wasHandsfree) {
  const handledBySession = voiceSession.enabled;
  await voiceSession.finishTurn(result);
  if (!handledBySession && !wasHandsfree && !voiceSession.enabled && state.speaking && result.text && !result.error && !result.cancelled) await voiceSession.speak(result.text);
}
async function sendMessage(text, {manageVoice = true} = {}) {
  const content = (text ?? $('#chat-input').value).trim();
  if (!content) return {error:'Write a message first.'};
  const tradingStop=JarvisVoice.tradingStopIntent(content);
  if(state.busy && tradingStop){try{return await requestTradingStop(content);}catch(error){toast(error.message);return{error:error.message};}}
  if (state.busy) return {error:'Wait for the current request to finish.'};
  if (!state.settings?.provider_ready && !tradingStop) { showView('settings'); toast('Connect an API provider or local model to start chatting.'); return {error:'Connect a model in Settings before starting voice.'}; }
  if (state.approvals.length && !tradingStop) { toast('Approve or decline your pending actions first.'); showView('chat'); scrollChat(); return {error:'Review the pending computer actions first.'}; }
  const wasHandsfree = voiceSession?.enabled;
  voiceSession?.beginTurn();
  setBusy(true);
  try {
    if (!state.conversation) { const c = await post('/conversations'); state.conversation = c.id; localStorage.setItem('jarvis-conversation', c.id); }
    $('#chat-input').value = ''; $('#chat-input').style.height = 'auto'; $('#welcome').hidden = true;
    showView('chat'); appendMessage('user', content); showWorking('Thinking'); scrollChat();
    const result = await stream('/conversations/' + state.conversation + '/chat', {content});
    if (manageVoice) await deliverReply(result, wasHandsfree);
    return result;
  } catch(e) { errorToast(e); setBusy(false); const result = {error:e.message}; if (manageVoice) await deliverReply(result, wasHandsfree); return result; }
}
$('#send-button').onclick = () => sendMessage();
$('#chat-input').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); sendMessage(); } });
$('#chat-input').addEventListener('input', event => { event.target.style.height = 'auto'; event.target.style.height = Math.min(event.target.scrollHeight, 170) + 'px'; });
$$('[data-prompt]').forEach(el => el.onclick = () => { $('#chat-input').value = el.dataset.prompt; sendMessage(); });
function renderApprovals() {
  if(state.approvals.length) setConversationOpen(true);
  $('#approval-list').innerHTML = state.approvals.map(a => `<section class="approval-card"><h3>${icon('shield')} Your approval is needed</h3><p>${escapeHTML(a.preview.summary)}</p><pre>${escapeHTML(JSON.stringify(a.arguments, null, 2))}</pre>${a.preview.docx_note ? `<p>${escapeHTML(a.preview.docx_note)}</p>` : ''}<p>This action will run on your computer. Existing documents are backed up before replacement.</p><div class="approval-actions"><button class="secondary-button" data-approval="${a.id}" data-decision="false">Decline</button><button class="primary-button" data-approval="${a.id}" data-decision="true">Approve action <span>↗</span></button></div></section>`).join('');
  $$('[data-approval]').forEach(el => { el.disabled = state.busy; el.onclick = async () => { if (state.busy) return; const wasHandsfree = voiceSession.enabled; voiceSession.beginTurn(); const result = await stream('/approvals/' + el.dataset.approval, {approved:el.dataset.decision === 'true'}); await deliverReply(result, wasHandsfree); }; });
  voiceSession?.refreshContext();
  updateStopButton();
}

async function refresh() {
  if (state.refreshing) return;
  state.refreshing = true;
  try {
    const [tasks, goals, reminders, memories, conversations] = await Promise.all(['tasks','goals','reminders','memories','conversations'].map(k => api('/' + k)));
    Object.assign(state, {tasks, goals, reminders, memories, conversations}); renderRail(); renderConversations();
    if (state.view === 'planner') renderPlanner();
    if (state.view === 'memory') renderMemories();
    if (state.view === 'activity') await loadActivity();
    notifyDue();
  } finally { state.refreshing = false; }
}
function renderRail() {
  const tasks = state.tasks.filter(t => t.status === 'open').sort((a,b) => (a.due_at || 'z').localeCompare(b.due_at || 'z'));
  $('#task-count').textContent = tasks.length;
  $('#rail-tasks').innerHTML = tasks.length ? tasks.slice(0,3).map(t => `<div class="rail-task"><button class="task-check" data-task-check="${t.id}" aria-label="Complete ${escapeHTML(t.title)}"></button><div>${escapeHTML(t.title)}<small>${escapeHTML(relativeDue(t.due_at))}</small></div></div>`).join('') : '<div class="rail-empty"><strong>A little breathing room.</strong>Add a task when you’re ready to begin.</div>';
  const reminders = state.reminders.filter(r => !r.acknowledged).sort((a,b) => a.due_at.localeCompare(b.due_at));
  $('#rail-reminders').innerHTML = reminders.length ? reminders.slice(0,2).map(r => `<div class="rail-reminder ${r.fired_at ? 'fired' : ''}"><span>${icon('bell')}</span><div><strong>${escapeHTML(r.title)}</strong><small>${escapeHTML(r.fired_at ? 'Due now · open planner' : relativeDue(r.due_at))}</small></div></div>`).join('') : '<div class="rail-empty">Nothing on the horizon.<br>I’ll keep an eye on things for you.</div>';
  $('#notification-dot').hidden = !reminders.some(r => r.fired_at);
  const goals = state.goals.filter(g => g.status === 'active');
  $('#rail-goals').innerHTML = goals.length ? goals.slice(0,2).map(g => { const linked = state.tasks.filter(t => t.goal_id === g.id), done = linked.filter(t => t.status === 'done').length, percent = linked.length ? Math.round(done / linked.length * 100) : 0; return `<div class="rail-goal">${escapeHTML(g.title)}<div class="goal-stats"><span>${done} of ${linked.length} steps</span><span>${percent}%</span></div><div class="progress-bar"><div class="progress-fill" data-progress="${percent}"></div></div></div>`; }).join('') : '<div class="rail-empty"><strong>What are you working toward?</strong>Big things start with a small step.</div>';
  $$('[data-progress]').forEach(el => { el.style.width = el.dataset.progress + '%'; });
  bindTaskChecks();
  $('.connection-card p').textContent = state.settings?.provider_ready ? 'Your model is configured. Bring your ideas, questions, and everyday plans.' : 'Connect a model to bring your personal assistant to life.';
}
function bindTaskChecks(root = document) { $$('[data-task-check]',root).forEach(el => el.onclick = async () => { const t = state.tasks.find(t => t.id === el.dataset.taskCheck); try { await patch('/tasks/' + t.id, {status:t.status === 'done' ? 'open' : 'done'}); await refresh(); } catch(e) { errorToast(e); } }); }
$('#rail-add-task').onclick = () => openItem('tasks');
$$('[data-planner]').forEach(el => el.onclick = () => { state.planner = el.dataset.planner; renderPlanner(); });
$('#add-planner').onclick = () => openItem(state.planner);
function renderPlanner() {
  $$('[data-planner]').forEach(el => el.classList.toggle('active', el.dataset.planner === state.planner));
  $('#planner-task-count').textContent = state.tasks.filter(t => t.status === 'open').length;
  const kind = state.planner;
  const items = [...state[kind]].sort((a,b) => kind === 'reminders' ? a.due_at.localeCompare(b.due_at) : (a.status === 'done') - (b.status === 'done'));
  $('#planner-list').innerHTML = items.length ? items.map(item => `<article class="planner-item ${item.status === 'done' ? 'done' : ''}">${kind === 'tasks' ? `<button class="task-check ${item.status === 'done' ? 'done' : ''}" data-task-check="${item.id}" aria-label="${item.status === 'done' ? 'Reopen' : 'Complete'} task">${item.status === 'done' ? '✓' : ''}</button>` : `<span class="${kind === 'goals' ? 'green' : 'amber'}">${icon(kind === 'goals' ? 'target' : 'bell')}</span>`}<div class="planner-item-body"><h3>${escapeHTML(item.title)}</h3>${item.details ? `<p>${escapeHTML(item.details)}</p>` : ''}<small>${escapeHTML(kind === 'goals' ? (item.status === 'done' ? 'Completed' : `${state.tasks.filter(t => t.goal_id === item.id && t.status === 'done').length} of ${state.tasks.filter(t => t.goal_id === item.id).length} steps complete`) : kind === 'reminders' && item.acknowledged ? 'Dismissed' : kind === 'reminders' && item.fired_at ? 'Due now' : relativeDue(item.due_at))}</small>${item.goal_id ? `<small> · ${escapeHTML(state.goals.find(g => g.id === item.goal_id)?.title || '')}</small>` : ''}</div><div class="planner-item-actions">${kind === 'goals' ? `<button class="icon-button" data-goal-toggle="${item.id}" aria-label="Toggle goal completion">${item.status === 'done' ? '↶' : '✓'}</button>` : ''}${kind === 'reminders' && !item.acknowledged ? `<button class="icon-button" data-dismiss="${item.id}" aria-label="Dismiss reminder">✓</button>` : ''}${kind !== 'reminders' ? `<button class="icon-button" data-edit-item="${item.id}" aria-label="Edit item">✎</button>` : ''}<button class="icon-button" data-delete-item="${item.id}" aria-label="Delete item">×</button></div></article>`).join('') : empty({tasks:'A fresh page for your day',goals:'Give your next chapter a direction',reminders:'One less thing to hold in your head'}[kind], {tasks:'Add your first task. One small step is a great place to start.',goals:'Save a goal, then break it into manageable tasks.',reminders:'Set a time and let JARVIS remember for you.'}[kind], kind === 'tasks' ? 'check' : kind === 'goals' ? 'target' : 'bell');
  bindTaskChecks($('#planner-list'));
  $$('[data-edit-item]').forEach(el => el.onclick = () => openItem(kind, state[kind].find(i => i.id === el.dataset.editItem)));
  $$('[data-delete-item]').forEach(el => el.onclick = async () => { if (state.settings?.permissions_mode !== 'autonomous' && !confirm('Delete this item?')) return; try { await remove('/' + kind + '/' + el.dataset.deleteItem); await refresh(); } catch(e) { errorToast(e); } });
  $$('[data-goal-toggle]').forEach(el => el.onclick = async () => { const goal = state.goals.find(g => g.id === el.dataset.goalToggle); try { await patch('/goals/' + goal.id, {status:goal.status === 'done' ? 'active' : 'done'}); await refresh(); } catch(e) { errorToast(e); } });
  $$('[data-dismiss]').forEach(el => el.onclick = async () => { try { await patch('/reminders/' + el.dataset.dismiss, {acknowledged:1}); await refresh(); } catch(e) { errorToast(e); } });
}
function openItem(kind, item = null) {
  state.editing = item;
  $('#item-form').reset(); $('#item-kind').value = kind; $('#kind-label').hidden = !!item;
  $('#item-text').value = item?.content || item?.title || ''; $('#item-details').value = item?.details || '';
  $('#item-goal').innerHTML = '<option value="">No goal</option>' + state.goals.map(g => `<option value="${g.id}">${escapeHTML(g.title)}</option>`).join(''); $('#item-goal').value = item?.goal_id || '';
  if (item?.due_at) { const d = new Date(item.due_at); $('#item-due').value = new Date(d.getTime() - d.getTimezoneOffset()*60000).toISOString().slice(0,16); }
  $('#dialog-error').hidden = true; updateDialog(); $('#item-dialog').showModal();
  voiceSession?.refreshContext();
}
function updateDialog() {
  const kind = $('#item-kind').value;
  $('#dialog-title').textContent = `${state.editing ? 'Edit' : 'Add'} ${kind === 'memories' ? 'a memory' : 'a ' + kind.slice(0,-1)}`;
  $('#item-text-label').textContent = kind === 'memories' ? 'What should JARVIS remember?' : 'Title';
  $('#item-details-label').hidden = !['tasks','goals'].includes(kind);
  $('#item-due-label').hidden = !['tasks','reminders'].includes(kind);
  $('#item-goal-label').hidden = kind !== 'tasks';
  $('#item-due').required = kind === 'reminders';
}
$('#item-kind').onchange = updateDialog;
$('#close-dialog').onclick = $('#cancel-dialog').onclick = () => $('#item-dialog').close();
$('#item-form').onsubmit = async event => {
  event.preventDefault(); const kind = $('#item-kind').value, text = $('#item-text').value.trim();
  const body = kind === 'memories' ? {content:text} : {title:text};
  if (['tasks','goals'].includes(kind)) body.details = $('#item-details').value;
  if (['tasks','reminders'].includes(kind)) body.due_at = $('#item-due').value ? new Date($('#item-due').value).toISOString() : null;
  if (kind === 'tasks') body.goal_id = $('#item-goal').value || null;
  try { if (state.editing) await patch('/' + kind + '/' + state.editing.id, body); else await post('/' + kind, body); $('#item-dialog').close(); await refresh(); toast('Saved. A little less to keep in your head.'); } catch(e) { $('#dialog-error').textContent = e.message; $('#dialog-error').hidden = false; }
};

$('#add-memory').onclick = () => openItem('memories');
function renderMemories() {
  $('#memory-count').textContent = `${state.memories.length} ${state.memories.length === 1 ? 'memory' : 'memories'}`;
  $('#memory-list').innerHTML = state.memories.length ? state.memories.map(m => `<article class="memory-card"><p>${escapeHTML(m.content)}</p><div class="memory-card-footer"><span>${escapeHTML(new Date(m.created_at).toLocaleDateString(undefined,{month:'short',day:'numeric'}))}</span><div><button class="icon-button" data-edit-memory="${m.id}" aria-label="Edit memory">✎</button><button class="icon-button" data-delete-memory="${m.id}" aria-label="Delete memory">×</button></div></div></article>`).join('') : empty('The beginning of knowing you', 'Add a preference, a useful fact, or something you’d like JARVIS to remember.', 'memory');
  $$('[data-edit-memory]').forEach(el => el.onclick = () => openItem('memories', state.memories.find(m => m.id === el.dataset.editMemory)));
  $$('[data-delete-memory]').forEach(el => el.onclick = async () => { if (state.settings?.permissions_mode !== 'autonomous' && !confirm('Forget this saved memory? Existing conversation history will remain.')) return; try { await remove('/memories/' + el.dataset.deleteMemory); await refresh(); } catch(e) { errorToast(e); } });
}
async function searchMemory() {
  try {
    const query = $('#memory-search').value.trim(); if (!query) { $('#memory-results').innerHTML = ''; return; }
    const results = await api('/memory/search?q=' + encodeURIComponent(query));
    const memories = results.memories.filter(m => m.content.toLowerCase().includes(query.toLowerCase()));
    $('#memory-results').innerHTML = '<div class="section-heading"><h3>Search results</h3></div>' + [...memories.map(m => ({content:m.content, label:'Saved memory'})), ...results.past_conversations.map(m => ({...m,label:formatDate(m.created_at)}))].map(m => `<div class="memory-hit"><small>${escapeHTML(m.label)}</small>${escapeHTML(m.content)}</div>`).join('');
    if (!memories.length && !results.past_conversations.length) $('#memory-results').innerHTML += empty('Nothing here yet', 'Try another word or phrase.', 'search');
  } catch(e) { errorToast(e); }
}
$('#memory-search-button').onclick = searchMemory;
$('#memory-search').onkeydown = e => { if (e.key === 'Enter') searchMemory(); };

let parentPath = '';
async function loadFiles(path = null, query = '') {
  $('#file-results').innerHTML = '<div class="rail-empty">Looking through your files…</div>';
  try {
    const params = new URLSearchParams(); if (path) params.set('path',path); if (query) params.set('q',query);
    const result = await api('/files?' + params);
    if (result.path) { state.currentPath = result.path; parentPath = result.parent; $('#file-path').value = result.path; }
    const entries = result.entries || result.results;
    $('#file-results').innerHTML = entries.length ? entries.map(f => `<button class="file-row" data-file-path="${escapeHTML(f.path)}" data-directory="${!!f.directory}"><span>${icon(f.directory ? 'folder' : 'file')}</span><div><strong>${escapeHTML(f.name)}</strong>${query ? `<small>${escapeHTML(f.path)}</small>` : ''}</div><span class="file-size">${f.directory ? 'Folder ↗' : f.size !== undefined ? f.size < 1024 ? f.size + ' B' : Math.round(f.size/1024) + ' KB' : 'Preview'}</span></button>`).join('') : empty(query ? 'No matching files' : 'A little room for something new', query ? 'Try a different filename or narrow your search.' : 'This folder is empty.', 'folder');
    if (result.limited) $('#file-results').insertAdjacentHTML('beforeend','<p class="field-note muted">Showing a limited set. Choose a narrower folder for more results.</p>');
    $$('[data-file-path]').forEach(el => el.onclick = () => el.dataset.directory === 'true' ? loadFiles(el.dataset.filePath) : previewFile(el.dataset.filePath));
  } catch(e) { $('#file-results').innerHTML = `<div class="inline-error">${escapeHTML(e.message)}</div>`; }
}
$('#file-go').onclick = () => loadFiles($('#file-path').value);
$('#file-path').onkeydown = e => { if (e.key === 'Enter') loadFiles($('#file-path').value); };
$('#file-up').onclick = () => loadFiles(parentPath);
$('#file-search-button').onclick = () => loadFiles($('#file-path').value, $('#file-search').value.trim());
$('#file-search').onkeydown = e => { if (e.key === 'Enter') loadFiles($('#file-path').value, $('#file-search').value.trim()); };
async function previewFile(path) {
  try { const result = await api('/files/read?path=' + encodeURIComponent(path)); state.previewPath = path; $('#file-preview-title').textContent = path; $('#file-content').textContent = result.content + (result.truncated ? '\n\n[Preview truncated]' : ''); $('#file-preview').hidden = false; $('#file-preview').scrollIntoView({behavior:'smooth'}); } catch(e) { errorToast(e); }
}
$('#close-preview').onclick = () => { $('#file-preview').hidden = true; };
$('#discuss-file').onclick = () => { showView('chat'); setConversationOpen(true); $('#chat-input').value = `Read this file and help me with it: ${state.previewPath}`; $('#chat-input').focus(); };

function populateSettings() {
  const s = state.settings; if (!s) return;
  $('#provider-url').value = s.provider_url; $('#provider-model').value = s.model; $('#provider-key').value = '';
  $('#provider-key').placeholder = s.has_api_key ? 'Key saved · leave blank to keep' : 'Enter a key to connect';
  $('#access-roots').value = s.roots.join('\n'); $('#user-name').value = s.name;
  $('#native-notifications').checked = s.native_notifications;
  $('#permissions-mode').value = s.permissions_mode || 'autonomous';
  $('#computer-access').value = s.computer_access || 'full';
  $('#browser-mode').value = s.browser_mode || 'existing';
  $('#speech-mode').value = s.speech_mode || 'auto';
  $('#background-voice').checked = s.background_voice !== false;
  $('#speech-voice').value = s.speech_voice || 'cedar';
  $('#speech-key').value = '';
  $('#speech-key').placeholder = s.has_speech_api_key ? 'Key saved · leave blank to keep' : 'Uses your OpenAI chat key when available';
  $('#workflow-timeout').value = s.workflow_timeout || 300;
  $('#application-directories').value = (s.application_directories || []).join('\n');
  $('#storage-path').textContent = 'Local data: ' + s.data_dir;
  $('#provider-pill').textContent = s.provider_ready ? 'Configured' : 'Not connected'; $('#provider-pill').classList.toggle('connected', s.provider_ready);
}
function updateConnectionUI() {
  const s = state.settings;
  $('#model-status').textContent = s.provider_ready ? `${s.model} · Configured and ready to connect` : 'Your workspace is ready · Connect a model to chat';
  $('#model-light').classList.toggle('off', !s.provider_ready);
  $('#profile-name').textContent = s.name || 'Your workspace'; $('#profile-initial').textContent = (s.name || 'You')[0].toUpperCase();
  populateVoiceChoices();
  $('#permission-note').textContent = s.permissions_mode === 'autonomous' ? 'Your request is approval. Available computer actions run automatically.' : s.permissions_mode === 'review' ? 'Computer actions wait for your review.' : 'Routine actions run automatically. Significant consequences ask first.';
}
async function saveSettings(options = {}) {
  const body = {provider_url:$('#provider-url').value.trim(), model:$('#provider-model').value.trim(), roots:$('#access-roots').value.split('\n').map(v => v.trim()).filter(Boolean), name:$('#user-name').value.trim(), native_notifications:$('#native-notifications').checked, permissions_mode:$('#permissions-mode').value, computer_access:$('#computer-access').value, browser_mode:$('#browser-mode').value, speech_mode:$('#speech-mode').value, speech_voice:$('#speech-voice').value, background_voice:$('#background-voice').checked, workflow_timeout:Number($('#workflow-timeout').value), application_directories:$('#application-directories').value.split('\n').map(v => v.trim()).filter(Boolean)};
  if ($('#provider-key').value) body.api_key = $('#provider-key').value.trim();
  if (options.model) body.model = options.model;
  if ($('#speech-key').value) body.speech_api_key = $('#speech-key').value.trim();
  state.settings = await patch('/settings', body); $('#provider-key').value = ''; updateConnectionUI(); populateSettings(); return state.settings;
}
$('#settings-form').onsubmit = async event => { event.preventDefault(); try { await saveSettings(); $('#settings-result').textContent = 'Changes saved'; toast('Your workspace is updated.'); } catch(e) { $('#settings-result').textContent = e.message; } };
$('#preset-openai').onclick = () => { $('#provider-url').value = 'https://api.openai.com/v1'; $('#provider-model').value = 'gpt-6.1-sol'; };
$('#clear-api-key').onclick = async () => { try { state.settings = await patch('/settings', {api_key:''}); populateSettings(); updateConnectionUI(); toast(state.settings.has_api_key ? 'Saved key removed. An environment key is still available.' : 'Saved key removed.'); } catch(e) { errorToast(e); } };
$('#preset-ollama').onclick = () => { $('#provider-url').value = 'http://localhost:11434/v1'; $('#provider-model').value = 'qwen3:8b'; toast('Start Ollama and run: ollama pull qwen3:8b'); };
async function checkConnection(repair = false, sol = false) {
  $('#check-provider').disabled = $('#repair-provider').disabled = $('#use-gpt-sol').disabled = true;
  $('#provider-result').textContent = sol ? 'Checking GPT-6.1 Sol…' : repair ? 'Finding a model your provider accepts and checking its tools…' : 'Checking streaming, tools and the tool-result follow-up…';
  try {
    await saveSettings(sol ? {model:state.settings.model} : {});
    const result = await post(sol ? '/provider/use-sol' : repair ? '/provider/repair' : '/provider/check');
    if(result.changed) { state.settings=await api('/settings'); populateSettings(); updateConnectionUI(); }
    $('#provider-result').textContent = result.message;
    $('#provider-pill').textContent = 'Connected'; $('#provider-pill').classList.add('connected');
  } catch(error) {
    $('#provider-result').textContent = error.message;
    $('#provider-pill').textContent = 'Connection failed'; $('#provider-pill').classList.remove('connected');
  } finally { $('#check-provider').disabled = $('#repair-provider').disabled = $('#use-gpt-sol').disabled = false; }
}
$('#check-provider').onclick = () => checkConnection();
$('#repair-provider').onclick = () => checkConnection(true);
$('#use-gpt-sol').onclick = () => checkConnection(false, true);
$('#enable-notifications').onclick = async () => {
  if (!('Notification' in window)) { toast('This browser does not support desktop notifications.'); return; }
  const permission = await Notification.requestPermission(); toast(permission === 'granted' ? 'Browser reminders enabled.' : 'Reminders will still appear in your dashboard.');
};
function notifyDue() {
  for (const reminder of state.reminders.filter(r => r.fired_at && !r.acknowledged)) {
    if (state.notified.has(reminder.id)) continue;
    state.notified.add(reminder.id); toast('Reminder: ' + reminder.title);
    if ('Notification' in window && Notification.permission === 'granted') new Notification('JARVIS reminder', {body:reminder.title, tag:reminder.id, icon:'/static/favicon.svg'});
  }
}
$('.composer-tools').insertAdjacentHTML('beforeend', `<button type="button" class="voice-mode" id="handsfree-toggle" aria-pressed="false">${icon('mic')}<span>Hands-free</span></button>`);
const speechPlayer = new JarvisSpeech.SpeechPlayer({getSettings:()=>state.settings || {},onEnergy:level=>JarvisHUD.setSpeechEnergy(level)});
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const replyVoice = () => JarvisVoice.chooseVoice(window.speechSynthesis?.getVoices() || [], localStorage.getItem('jarvis-voice-uri') || '');
const replyRate = () => Math.min(1.25, Math.max(0.75, Number(localStorage.getItem('jarvis-voice-rate')) || 1));
function renderVoice() {
  const on = state.speaking || voiceSession?.enabled;
  $('#speak-toggle').classList.toggle('enabled', on); $('#speak-toggle').setAttribute('aria-pressed', String(on)); $('#speak-toggle span:last-child').textContent = on ? 'Voice on' : 'Voice off';
  $('#handsfree-toggle').classList.toggle('enabled', !!voiceSession?.enabled); $('#handsfree-toggle').setAttribute('aria-pressed', String(!!voiceSession?.enabled));
}
voiceSession = new JarvisVoice.VoiceSession({
  makeRecognition: SpeechRecognition ? () => new SpeechRecognition() : null,
  synthesis: window.speechSynthesis,
  makeUtterance: window.SpeechSynthesisUtterance ? text => new SpeechSynthesisUtterance(text) : null,
  speechPlayer,
  getVoice: replyVoice,
  getRate: replyRate,
  onCancel: () => requestCancellation(false),
  onTradingStop: text => requestTradingStop(text),
  onBoundary: () => JarvisHUD.pulse(),
  getBlocker: () => (document.hidden && state.settings?.background_voice === false) || state.view !== 'chat' || $('#item-dialog').open ? 'paused' : state.busy ? 'thinking' : state.approvals.length ? 'approval' : null,
  onTurn: text => sendMessage(text, {manageVoice:false}),
  onTranscript: text => { $('#voice-transcript').textContent = text || 'Speak naturally. I’ll reply, then listen again.'; },
  onError: toast,
  onState: ({enabled, phase}) => {
    JarvisHUD.setVoice({enabled,phase});
    document.body.dataset.voicePhase = phase;
    $('#voice-session').hidden = !enabled;
    $('#voice-skip').hidden = phase !== 'speaking';
    $('#front-skip').hidden = phase !== 'speaking';
    $('#front-voice-stop').hidden = !enabled;
    $('#voice-session-status').textContent = {starting:'Opening microphone…', listening:'I’m listening', thinking:'Thinking…', speaking:'Speaking', approval:'Waiting for your approval', paused:'Voice paused', off:'Voice off'}[phase] || phase;
    if (phase === 'listening') $('#voice-transcript').textContent = 'Go ahead. I’m listening.';
    if (phase === 'speaking') $('#voice-transcript').textContent = 'I’ll listen again after this reply.';
    if (phase === 'approval') $('#voice-transcript').textContent = 'Review the action in chat. Listening resumes after it is resolved.';
    if (phase === 'thinking') $('#voice-transcript').textContent = 'Working on your request. Say “cancel task” or “stop trading” to stop.';
    if (phase === 'paused') $('#voice-transcript').textContent = 'Return to the Assistant tab to continue.';
    $('#welcome').hidden = enabled || !!$('#messages .message');
    $$('[data-voice-toggle]').forEach(el => { el.setAttribute('aria-pressed', String(enabled)); el.setAttribute('aria-label', enabled ? 'End hands-free conversation' : 'Start hands-free conversation'); });
    $('#mic-button').classList.toggle('mic-active', phase === 'listening');
    $('#mic-button').setAttribute('aria-label', enabled ? 'End hands-free conversation' : 'Start hands-free conversation');
    $('#mic-button').title = enabled ? 'End hands-free conversation' : 'Start hands-free conversation';
    renderVoice();
  },
});
function toggleHandsfree() {
  if (voiceSession.enabled) { voiceSession.stop(); return; }
  if (!state.settings?.provider_ready) toast('Local trading stop commands are available. Other requests need a connected model.');
  if ($('#chat-input').value.trim()) { toast('Send or clear your draft before starting hands-free mode.'); return; }
  if (state.approvals.length) { toast('Review the pending actions before starting hands-free mode.'); return; }
  showView('chat');
  if (voiceSession.start()) toast('Hands-free mode on. Speak, then pause to send. Say “stop listening” to finish.');
}
$$('[data-voice-toggle]').forEach(el => el.onclick = toggleHandsfree);
$('#handsfree-toggle').onclick = $('#mic-button').onclick = toggleHandsfree;
$('#voice-stop').onclick = $('#front-voice-stop').onclick = () => voiceSession.stop();
$('#voice-skip').onclick = $('#front-skip').onclick = () => voiceSession.skipReply();
$('#speak-toggle').onclick = () => {
  if (!window.speechSynthesis) { toast('Speech playback is unavailable in this browser.'); return; }
  if (voiceSession.enabled) { voiceSession.stop(); state.speaking = false; }
  else { state.speaking = !state.speaking; if (!state.speaking) voiceSession.cancelSpeech(); }
  localStorage.setItem('jarvis-speak', state.speaking); renderVoice();
};
$('#chat-input').addEventListener('input', () => { if (voiceSession.enabled) voiceSession.stop(); });
$('#item-dialog').addEventListener('close', () => voiceSession.refreshContext());
document.addEventListener('visibilitychange', () => voiceSession.refreshContext());
window.addEventListener('pagehide', () => voiceSession.stop());
function populateVoiceChoices() {
  const voices = window.speechSynthesis?.getVoices() || [];
  const british = voices.filter(v => /^en[-_]GB$/i.test(v.lang));
  const other = voices.filter(v => !british.includes(v)).sort((a,b) => a.name.localeCompare(b.name));
  const options = list => list.map(v => `<option value="${escapeHTML(v.voiceURI || v.name)}">${escapeHTML(v.name)} · ${escapeHTML(v.lang)}</option>`).join('');
  $('#voice-select').innerHTML = '<option value="">Automatic · British English</option>' + (british.length ? `<optgroup label="British English">${options(british)}</optgroup>` : '') + (other.length ? `<optgroup label="Other available voices">${options(other)}</optgroup>` : '');
  const saved = localStorage.getItem('jarvis-voice-uri') || '';
  $('#voice-select').value = voices.some(v => (v.voiceURI || v.name) === saved) ? saved : '';
  const chosen = replyVoice();
  const fallback = chosen ? `Installed fallback: ${chosen.name} (${chosen.lang}).` : 'Add an English (United Kingdom) speech voice in Windows Settings → Time & language → Speech for browser fallback.';
  const natural = state.settings?.speech_mode !== 'browser' && state.settings?.speech_available;
  $('#voice-hint').textContent = natural ? `Natural British voice: ${state.settings.speech_voice === 'marin' ? 'Marin' : 'Cedar'}. ${fallback}` : (state.settings?.speech_mode==='neural' ? 'Natural voice needs an OpenAI speech key. ' : '') + fallback;
  $('#voice-rate').value = replyRate(); $('#voice-rate-label').textContent = replyRate().toFixed(2) + '×';
}
$('#voice-select').onchange = () => { localStorage.setItem('jarvis-voice-uri', $('#voice-select').value); populateVoiceChoices(); };
$('#voice-rate').oninput = () => { localStorage.setItem('jarvis-voice-rate', $('#voice-rate').value); $('#voice-rate-label').textContent = replyRate().toFixed(2) + '×'; };
$('#voice-preview').onclick = async () => { try { state.settings=await patch('/settings',{speech_mode:$('#speech-mode').value,speech_voice:$('#speech-voice').value,background_voice:$('#background-voice').checked,...($('#speech-key').value.trim()?{speech_api_key:$('#speech-key').value.trim()}:{})}); $('#speech-key').value=''; populateVoiceChoices(); await voiceSession.speak('Good evening. I’m JARVIS, your personal assistant. How may I assist you?'); } catch(error) { errorToast(error); } };
$('#clear-speech-key').onclick = async () => { try { state.settings=await patch('/settings',{speech_api_key:''}); populateSettings(); toast('Saved speech key removed. An OpenAI chat or environment key may still provide natural speech.'); } catch(error) { errorToast(error); } };
window.speechSynthesis?.addEventListener('voiceschanged', populateVoiceChoices);
populateVoiceChoices(); voiceSession.status('off');
function updateStopButton() { $('#emergency-stop').hidden = !(state.busy || state.active.length || state.approvals.length); }
async function requestTradingStop(text) {
  const intent=JarvisVoice.tradingStopIntent(text);
  if(!intent)throw new Error('Use a clear trading stop command.');
  const body=intent==='off'?{action:'mode',mode:'OFF'}:{action:intent};
  await post('/trading/control',body);
  const reply=intent==='pause_today'?'Trading is stopped for today, sir.':intent==='emergency'?'The trading emergency stop is active, sir.':'New trading entries are stopped, sir.';
  toast(reply);window.JarvisTrading?.refresh();
  return{text:reply};
}
async function requestCancellation(endVoice = true) {
  if (endVoice) voiceSession.stop();
  try {
    await post('/cancel'); toast('Stop requested. Completed actions remain in Activity.');
    if (state.busy) showWorking('Stopping task…');
    else { state.active = []; if (state.conversation) await loadConversation(state.conversation); updateStopButton(); }
    if (state.view === 'activity') await loadActivity();
  } catch (error) { toast('Could not reach the stop control: ' + error.message + '. You can stop JARVIS with Ctrl+C in its terminal.'); }
}
$('#emergency-stop').onclick = () => requestCancellation();
document.addEventListener('keydown', event => {
  if ((event.key === 'Escape' && !$('#item-dialog').open && (state.busy || state.active.length || state.approvals.length)) || (event.key === 'F8' && event.ctrlKey && event.altKey)) {
    event.preventDefault(); requestCancellation();
  }
});
function renderWorkflow() {
  const workflow = state.workflow;
  JarvisHUD.updateWorkflow(workflow,state.flowActions);
  $('#workflow-card').hidden = !workflow;
  if (!workflow) return;
  let plan = workflow.plan || []; if (typeof plan === 'string') { try {plan = JSON.parse(plan);} catch {plan=[];} }
  $('#workflow-title').textContent = workflow.request || 'Your task';
  $('#workflow-status').textContent = (workflow.status || 'running').replaceAll('_',' ');
  $('#workflow-status').dataset.status = workflow.status || 'running';
  $('#workflow-outcome').textContent = workflow.detail || 'Working through the requested outcome.';
  $('#workflow-steps').innerHTML = plan.map(step => `<li>${escapeHTML(step)}</li>`).join('');
  updateStopButton();
}
async function loadApplications(force = false) {
  const token = ++state.appRequest;
  $('#application-report').textContent = force ? 'Refreshing installed app registrations…' : 'Reading your application index…';
  $('#refresh-applications').disabled = true;
  try {
    const query = $('#application-query').value.trim();
    const result = await api('/applications?q=' + encodeURIComponent(query) + '&refresh=' + force);
    if (token !== state.appRequest) return;
    state.applications = result.applications || [];
    $('#application-report').textContent = !result.supported ? 'Native app discovery and launch checks work when JARVIS runs on Windows.' : `${result.count} indexed applications. ${result.cached ? 'Using saved index.' : 'Index refreshed.'} ${(result.warnings || []).join(' ')}`;
    $('#application-list').innerHTML = state.applications.length ? state.applications.map(app => `<article class="application-card"><div class="application-icon">${icon('grid')}</div><div class="application-copy"><h3>${escapeHTML(app.name)}</h3><p>${[...new Set(app.launchers.map(method => method.kind))].map(kind => `<span class="app-method">${escapeHTML(kind)}</span>`).join(' ')}${app.preferred ? `<span class="muted"> · remembered: ${escapeHTML(app.preferred.kind)}</span>` : ''}</p><small>${escapeHTML([...new Set(app.launchers.map(method => method.source))].join(' · '))}</small>${app.last_used ? `<small>Last verified: ${escapeHTML(formatDate(app.last_used))}</small>` : ''}<p id="app-check-${app.id}" role="status" class="app-check-result"></p></div><div class="application-actions"><button type="button" class="secondary-button" data-app-open="${app.id}">Open in Assistant</button><button type="button" class="text-button" data-app-check="${app.id}">Check launch</button></div></article>`).join('') : empty('No matching apps yet', result.supported ? 'Try Refresh index, another app name, or add a portable application folder in Settings.' : 'Open this app on your Windows computer to build its installed application index.', 'grid');
    $('#alias-application').innerHTML = state.applications.map(app => `<option value="${app.id}">${escapeHTML(app.name)}</option>`).join('');
    $('#application-aliases').textContent = (result.aliases || []).map(alias => `${alias.alias} → ${state.applications.find(app => app.id === alias.application_id)?.name || alias.application_id.slice(0,8)}`).join(' · ') || 'No custom aliases yet.';
    $$('[data-app-open]').forEach(button => button.onclick = () => {
      const app = state.applications.find(app => app.id === button.dataset.appOpen);
      showView('chat'); sendMessage(`Open ${app.name}. Its installed application id is ${app.id}.`);
    });
    $$('[data-app-check]').forEach(button => button.onclick = async () => {
      button.disabled = true; const label = $('#app-check-' + button.dataset.appCheck); label.textContent = 'Launching and checking for a visible window…';
      try { const result = await post('/applications/' + button.dataset.appCheck + '/test'); label.textContent = result.verified ? `Verified ${result.already_running ? 'existing window' : 'launch'}${result.focused ? ' · brought to foreground' : ' · Windows did not grant foreground focus'}.` : result.message || 'Launch could not be verified. Check the desktop.'; }
      catch (error) { label.textContent = error.message; }
      finally { button.disabled = false; }
    });
  } catch (error) { $('#application-report').textContent = error.message; }
  finally { if (token === state.appRequest) $('#refresh-applications').disabled = false; }
}
$('#refresh-applications').onclick = () => loadApplications(true);
$('#search-applications').onclick = () => loadApplications();
$('#application-query').onkeydown = event => { if (event.key === 'Enter') loadApplications(); };
$('#save-application-alias').onclick = async () => {
  try { await post('/applications/alias', {alias:$('#application-alias').value.trim(), application_id:$('#alias-application').value}); toast('Application preference remembered.'); await loadApplications(); }
  catch (error) { errorToast(error); }
};
async function loadActivity() {
  const result = await api('/activity');
  state.active = result.active || []; updateStopButton();
  const latest=(result.workflows || [])[0];
  if(latest && (!state.workflow || state.view==='activity')) { state.workflow=latest; state.flowActions=(result.actions||[]).filter(action=>action.workflow_id===latest.id).reverse(); renderWorkflow(); }
  $('#emergency-status').textContent = result.emergency_hotkey ? 'Emergency stop: Ctrl+Alt+F8 anywhere in Windows. Escape or Stop task also works in JARVIS.' : 'Emergency stop: Escape or Stop task in JARVIS. The global Windows hotkey is unavailable in this session.';
  $('#workflow-list').innerHTML = (result.workflows || []).slice(0,8).map(workflow => `<details class="workflow-card"><summary><span>${escapeHTML(workflow.request)}</span><span class="pill" data-status="${escapeHTML(workflow.status)}">${escapeHTML(workflow.status.replaceAll('_',' '))}</span></summary><p>${escapeHTML(workflow.detail || '')}</p><ol>${workflow.plan.map(step => `<li>${escapeHTML(step)}</li>`).join('')}</ol><small>${escapeHTML(formatDate(workflow.created_at))}</small></details>`).join('') || empty('A clear record of your work', 'Plans and actions will appear here when you ask JARVIS to do something.', 'clock');
  $('#action-list').innerHTML = (result.actions || []).map(action => `<details class="action-card"><summary><span>${escapeHTML(action.name.replaceAll('_',' '))}</span><span class="pill" data-status="${escapeHTML(action.status)}">${escapeHTML(action.status.replaceAll('_',' '))}</span></summary><p>${escapeHTML(formatDate(action.created_at))} · ${escapeHTML(action.risk)}</p><pre>${escapeHTML(JSON.stringify({arguments:action.arguments,result:action.result},null,2))}</pre>${action.undo_available ? `<button type="button" class="secondary-button" data-undo-action="${action.id}">Undo this file change</button>` : ''}${action.result?.image_id ? `<a class="text-button" href="/api/screenshots/${escapeHTML(action.result.image_id)}" target="_blank" rel="noopener">View captured window ↗</a>` : ''}</details>`).join('');
  $$('[data-undo-action]').forEach(button => button.onclick = () => { showView('chat'); sendMessage('Undo the file change with action id ' + button.dataset.undoAction + '.'); });
}
$('#refresh-activity').onclick = () => loadActivity().catch(errorToast);
function errorToast(error) { toast(error.message || String(error)); }
async function init() {
  try {
    const status = await api('/status'); state.settings = status.settings; state.active = status.active || []; updateConnectionUI(); updateStopButton();
    $('#drive-buttons').innerHTML = status.drives.map(d => `<button type="button" class="secondary-button" data-drive="${escapeHTML(d)}">＋ ${escapeHTML(d)}</button>`).join('');
    $$('[data-drive]').forEach(el => el.onclick = () => { const values = $('#access-roots').value.split('\n'); if (!values.includes(el.dataset.drive)) $('#access-roots').value += '\n' + el.dataset.drive; });
    await refresh();
    const id = localStorage.getItem('jarvis-conversation'); if (id && state.conversations.some(c => c.id === id)) await loadConversation(id);
  } catch(e) { const message='Could not reach your local JARVIS server. ' + e.message; $('#chat-error').textContent = message; $('#chat-error').hidden = false; toast(message); }
}
init();
setInterval(() => { refresh().catch(() => {}); }, 5000);
