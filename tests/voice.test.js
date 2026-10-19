const {test} = require('node:test');
const assert = require('node:assert/strict');
const {VoiceSession, chooseVoice, speechChunks, stopIntent} = require('../jarvis/static/voice.js');

const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

function fixture(overrides = {}) {
  const recognizers = [], spoken = [], turns = [], errors = [], phases = [];
  let blocker = null;
  const synthesis = {speak(utterance) { spoken.push(utterance); }, cancel() {}};
  const makeRecognition = () => {
    const recognition = {
      start() { this.onstart(); }, abort() { this.onend?.(); },
      words(text, final = true) { const result = [{transcript:text}]; result.isFinal = final; this.onresult({results:[result]}); },
    };
    recognizers.push(recognition);
    return recognition;
  };
  const session = new VoiceSession({makeRecognition, synthesis, makeUtterance:text => ({text}),
    onTurn:async text => { turns.push(text); return {text:'Good evening.'}; },
    onState:state => phases.push(state.phase), onError:error => errors.push(error),
    getBlocker:() => blocker, silenceMs:10, restartMs:5, ...overrides});
  return {session, recognizers, spoken, turns, errors, phases, setBlocker(value) {blocker = value; session.refreshContext();}};
}

test('a voice utterance sends automatically, speaks, then listens for another turn', async t => {
  const f = fixture(); t.after(() => f.session.stop());
  f.session.start();
  f.recognizers[0].words('Plan my morning');
  await wait(30);
  assert.deepEqual(f.turns, ['Plan my morning']);
  assert.equal(f.session.phase, 'speaking');
  assert.equal(f.session.recognition, null);
  assert.equal(f.spoken[0].lang, 'en-GB');
  f.spoken[0].onend();
  await wait(15);
  assert.equal(f.session.phase, 'listening');
  assert.equal(f.recognizers.length, 2);
  f.recognizers[1].words('Add a break');
  await wait(30);
  assert.deepEqual(f.turns, ['Plan my morning', 'Add a break']);
});

test('interim results, final results and late end callbacks send only one turn', async t => {
  const f = fixture(); t.after(() => f.session.stop()); f.session.start();
  const mic = f.recognizers[0];
  mic.words('Remind me', false); mic.words('Remind me to stretch');
  mic.onend(); mic.onend();
  await wait(25);
  assert.deepEqual(f.turns, ['Remind me to stretch']);
});

test('permission denial ends the session without repeatedly requesting the microphone', async t => {
  const f = fixture(); t.after(() => f.session.stop()); f.session.start();
  f.recognizers[0].onerror({error:'not-allowed'});
  await wait(20);
  assert.equal(f.session.enabled, false);
  assert.equal(f.recognizers.length, 1);
  assert.match(f.errors[0], /access was denied/);
});

test('browser no-speech events restart listening without sending empty prompts', async t => {
  const f = fixture(); t.after(() => f.session.stop()); f.session.start();
  f.recognizers[0].onerror({error:'no-speech'});
  await wait(20);
  assert.equal(f.recognizers.length, 2);
  assert.deepEqual(f.turns, []);
});

test('computer approvals pause listening until resolved and never approve by voice', async t => {
  const f = fixture(); t.after(() => f.session.stop()); f.session.start();
  f.session.beginTurn(); f.setBlocker('approval');
  const pending = f.session.finishTurn({approvals:1});
  assert.match(f.spoken[0].text, /review the computer action/);
  f.spoken[0].onend(); await pending; await wait(15);
  assert.equal(f.session.phase, 'approval');
  assert.equal(f.session.recognition, null);
  assert.deepEqual(f.turns, []);
  f.setBlocker(null); await wait(15);
  assert.equal(f.session.phase, 'listening');
});

test('saying stop listening ends the session without sending a model request', async () => {
  const f = fixture(); f.session.start();
  await f.session.commit('JARVIS, stop listening.');
  assert.equal(f.session.enabled, false);
  assert.deepEqual(f.turns, []);
});

test('leaving the assistant pauses the mic and cancels reply playback', async t => {
  const f = fixture(); t.after(() => f.session.stop()); f.session.start();
  const audio = f.session.speak('A spoken reply.');
  f.setBlocker('paused'); await audio;
  assert.equal(f.session.phase, 'paused');
  assert.equal(f.session.speaking, false);
  assert.equal(f.session.recognition, null);
  f.setBlocker(null); await wait(15);
  assert.equal(f.session.phase, 'listening');
});

test('provider failure stops the voice loop rather than resending a failed request', async t => {
  const f = fixture({onTurn:async () => ({error:'Provider connection failed'})});
  t.after(() => f.session.stop()); f.session.start();
  f.recognizers[0].words('Hello'); await wait(30);
  assert.equal(f.session.enabled, false);
  assert.deepEqual(f.errors, ['Provider connection failed']);
});

test('ending while a request runs suppresses stale speech and reopening the mic', async () => {
  let complete;
  const f = fixture({onTurn:() => new Promise(resolve => {complete = resolve;})});
  f.session.start(); const request = f.session.commit('Hello');
  f.session.stop(); complete({text:'Late reply'}); await request; await wait(15);
  assert.equal(f.session.enabled, false);
  assert.deepEqual(f.spoken, []);
  assert.equal(f.recognizers.length, 1);
});

test('unavailable speech recognition produces a useful error and stays off', () => {
  const f = fixture({makeRecognition:null});
  assert.equal(f.session.start(), false);
  assert.match(f.errors[0], /Chrome or Edge/);
});

test('default British voice is selected over an American default; explicit choices persist', () => {
  const voices = [{name:'US default',lang:'en-US',voiceURI:'us'}, {name:'Microsoft George',lang:'en-GB',voiceURI:'gb'}];
  assert.equal(chooseVoice(voices).voiceURI, 'gb');
  assert.equal(chooseVoice(voices, 'us').voiceURI, 'us');
  assert.equal(chooseVoice([], 'gone'), null);
});

test('long speech is split without dropping words or reading code blocks aloud', () => {
  const text = 'Here is a sentence. '.repeat(70).trim();
  const chunks = speechChunks(text);
  assert.ok(chunks.every(chunk => chunk.length <= 220));
  assert.equal(chunks.join(' '), text);
  assert.deepEqual(speechChunks('```python\nsecret = 123\n```'), ['The code is in our chat.']);
});

test('browser and natural playback receive the same compact list phrases without reopening the mic', async t => {
  const text = 'Your tasks:\n\n1. Open Outlook\n\n2. Read 3 messages\n\n3. Finish at 9:30';
  const expected = 'Your tasks: Open Outlook; Read 3 messages; Finish at 9:30.';
  assert.deepEqual(speechChunks(text),[expected]);
  const browser = fixture(); t.after(()=>browser.session.stop());
  browser.session.start();
  const spoken = browser.session.speak(text);
  assert.equal(browser.spoken[0].text,expected);
  assert.equal(browser.session.recognition,null);
  assert.equal(browser.recognizers.length,1);
  browser.spoken[0].onend(); await spoken; await wait(15);
  assert.equal(browser.recognizers.length,2);

  let finish;
  const passages=[];
  const natural = fixture({speechPlayer:{canUse:()=>true,cancel() {},play:(words)=>{passages.push(words);return new Promise(resolve=>{finish=resolve;});}}});
  t.after(()=>natural.session.stop()); natural.session.start();
  const played=natural.session.speak(text);
  assert.deepEqual(passages,[expected]);
  assert.equal(natural.session.recognition,null);
  assert.equal(natural.spoken.length,0);
  finish(); await played; await wait(15);
  assert.equal(natural.recognizers.length,2);
});

test('skip reply resumes listening and ignores late speech events', async t => {
  const f = fixture(); t.after(() => f.session.stop()); f.session.start();
  const audio = f.session.speak('Something long.'); const utterance = f.spoken[0];
  f.session.skipReply(); await audio;
  utterance.onend(); await wait(15);
  assert.equal(f.session.phase, 'listening');
  assert.equal(f.recognizers.length, 2);
});

test('task-time microphone accepts cancellation without queuing other instructions', async t => {
  let complete, cancelled = 0;
  const f = fixture({onTurn:() => new Promise(resolve => {complete=resolve;}), onCancel:async () => {cancelled++;complete({text:'Task cancelled.',cancelled:true});}});
  t.after(() => f.session.stop()); f.session.start();
  const task = f.session.commit('Open Outlook');
  await wait(15);
  assert.equal(f.recognizers.length,2);
  f.recognizers[1].words('Now send that email');
  assert.equal(cancelled,0);
  f.recognizers[1].words('Please cancel that');
  await wait(15);
  assert.equal(cancelled,1);
  assert.equal(f.session.phase,'speaking');
  assert.equal(f.session.controlRecognition,null);
  f.spoken.at(-1).onend(); await task; await wait(15);
  assert.equal(f.session.phase,'listening');
});

test('cancellation intent is conversational and does not match negation or unrelated speech', () => {
  for (const text of ['Jarvis, cancel task.', 'Could you please stop?', 'hold on', 'Cancel that please']) assert.equal(stopIntent(text),'task');
  assert.equal(stopIntent('Stop listening'),'listening');
  for (const text of ['Do not cancel','Cancel my dentist appointment','Stop Spotify','I cannot stop thinking']) assert.equal(stopIntent(text),null);
});

test('completed recognition turns use a shorter pause while continued words reset submission', async t => {
  let now=0, sequence=0;
  const timers=new Map();
  const delay=(callback,ms)=>{const id=++sequence;timers.set(id,{callback,at:now+ms});return id;};
  const clear=id=>timers.delete(id);
  const advance=async milliseconds=>{
    now+=milliseconds;
    for(const [id,timer] of [...timers]) if(timer.at<=now) {timers.delete(id);timer.callback();}
    await Promise.resolve();
  };
  const f=fixture({delay,clear,silenceMs:1200}); t.after(()=>f.session.stop());
  f.session.start();
  f.recognizers[0].words('Open',false);
  await advance(700); assert.deepEqual(f.turns,[]);
  f.recognizers[0].words('Open Spotify');
  await advance(600); assert.deepEqual(f.turns,[]);
  f.recognizers[0].words('Open Spotify and bring it forward');
  await advance(649); assert.deepEqual(f.turns,[]);
  await advance(1);
  assert.deepEqual(f.turns,['Open Spotify and bring it forward']);
  f.recognizers[0].onend(); await advance(1200);
  assert.equal(f.turns.length,1);
});
