const {test} = require('node:test');
const assert = require('node:assert/strict');
const {SpeechPlayer, speechSegments, speechText} = require('../jarvis/static/speech.js');
const {VoiceSession, chooseVoice} = require('../jarvis/static/voice.js');

const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => {resolve=yes; reject=no;}); return {promise,resolve,reject}; };
const response = () => ({ok:true, blob:async () => new Blob(['ID3audio'], {type:'audio/mpeg'})});

function playerFixture(options = {}) {
  const requests = [], audios = [], revoked = [];
  let sequence = 0;
  const player = new SpeechPlayer({
    getSettings:() => ({speech_mode:'auto',speech_available:true}),
    fetch:async (url, request) => {requests.push({url,...request}); return response();},
    audioFactory:() => {
      const audio = {playCount:0,loadCount:0,paused:false,play() {this.playCount++; return Promise.resolve();},pause() {this.paused=true;},removeAttribute() {},load() {this.loadCount++;}};
      audios.push(audio); return audio;
    }, createObjectURL:() => 'blob:audio-' + (++sequence), revokeObjectURL:url => revoked.push(url), ...options,
  });
  return {player,requests,audios,revoked};
}

function voiceFixture(speechPlayer, options = {}) {
  const spoken = [], microphones = [], errors = [];
  const session = new VoiceSession({speechPlayer,
    makeRecognition:() => {const mic = {start() {this.onstart();},abort() {this.onend?.();}}; microphones.push(mic); return mic;},
    synthesis:{speak:utterance => spoken.push(utterance),cancel() {}},makeUtterance:text=>({text}),
    getBlocker:()=>null,onError:message=>errors.push(message),onTurn:async()=>({text:'Good evening.'}),restartMs:1,...options});
  return {session,spoken,microphones,errors};
}

test('natural British voices rank ahead of legacy voices while explicit choices persist', () => {
  const voices = [{name:'Microsoft George',lang:'en-GB',voiceURI:'george'},
    {name:'Microsoft Ryan Online (Natural)',lang:'en-GB',voiceURI:'ryan'},
    {name:'US Natural',lang:'en-US',voiceURI:'us'}];
  assert.equal(chooseVoice(voices).voiceURI,'ryan');
  assert.equal(chooseVoice(voices,'george').voiceURI,'george');
});

test('natural speech segments preserve every word within the official input limit', () => {
  const text = 'A smoothly spoken sentence with British phrasing. '.repeat(250).trim();
  const parts = speechSegments(text);
  assert.ok(parts.length > 1);
  assert.ok(parts.every(part=>part.length <= 4000));
  assert.equal(parts.join(' '),text);
});

test('browser mode or missing OpenAI speech key disables the neural transport', () => {
  for (const settings of [{speech_mode:'browser',speech_available:true},{speech_mode:'auto',speech_available:false}]) {
    assert.equal(playerFixture({getSettings:()=>settings}).player.canUse(),false);
  }
});

test('the transport posts to local authenticated speech API and cleans completed audio', async () => {
  const f = playerFixture();
  const task = f.player.play('Good evening.',{rate:0.95}); await tick();
  assert.equal(f.requests[0].url,'/api/speech');
  assert.equal(f.requests[0].headers['X-Jarvis-Client'],'dashboard');
  assert.deepEqual(JSON.parse(f.requests[0].body),{text:'Good evening.',rate:0.95});
  assert.equal(f.audios[0].playCount,1);
  f.audios[0].onended(); await task;
  assert.deepEqual(f.revoked,['blob:audio-1']);
  assert.equal(f.player.active,null);
});

test('long replies prefetch the following passage during playback without dropping text', async () => {
  const f = playerFixture();
  const text = 'This is a full sentence. '.repeat(260).trim();
  const task = f.player.play(text); await tick();
  assert.equal(f.requests.length,3);
  assert.equal(f.audios.filter(audio=>audio.playCount).length,1);
  assert.equal(f.audios[1].loadCount,1);
  assert.equal(f.audios[1].playCount,0);
  const segments = speechSegments(text);
  for (let index=0;index<segments.length;index++) {
    assert.equal(f.audios.filter(audio=>audio.playCount).length,index+1);
    f.player.active.audio.onended(); await tick();
  }
  await task;
  assert.equal(f.requests.map(request=>JSON.parse(request.body).text).join(' '),text);
  assert.equal(f.revoked.length,segments.length);
});

test('cancellation aborts in-flight generation and suppresses late audio', async () => {
  const pending = deferred(); let signal;
  const f = playerFixture({fetch:(_url,request)=>{signal=request.signal;return pending.promise;}});
  const task = f.player.play('A reply that is no longer needed.');
  f.player.cancel(); await task;
  assert.equal(signal.aborted,true);
  pending.resolve(response()); await tick();
  assert.equal(f.audios.length,0);
  assert.equal(f.revoked.length,0);
});

test('cancelling active audio pauses playback and releases every prefetched object URL', async () => {
  const f = playerFixture();
  const task = f.player.play('A full sentence. '.repeat(400)); await tick();
  const lateEnd = f.audios[0].onended;
  f.player.cancel(); await task;
  assert.ok(f.audios.every(audio=>audio.paused));
  assert.equal(f.revoked.length,3);
  lateEnd(); await tick();
  assert.equal(f.audios.filter(audio=>audio.playCount).length,1);
});

test('a second reply cancels the first without allowing stale playback', async () => {
  const pending = deferred(); let calls = 0;
  const f = playerFixture({fetch:async ()=>{calls++;return calls===1?pending.promise:response();}});
  const old = f.player.play('Old reply');
  const current = f.player.play('Current reply'); await old; await tick();
  pending.resolve(response()); await tick();
  assert.equal(f.audios.length,1);
  f.audios[0].onended(); await current;
  assert.equal(f.revoked.length,1);
});

test('natural voice turn-taking pauses recognition until audio ends and resumes automatically', async t => {
  const p = playerFixture(), f = voiceFixture(p.player); t.after(()=>f.session.stop());
  f.session.start();
  const task = f.session.commit('Hello'); await tick();
  assert.equal(f.session.phase,'speaking');
  assert.equal(f.session.recognition,null);
  assert.equal(f.spoken.length,0);
  p.audios[0].onended(); await task;
  await new Promise(resolve=>setTimeout(resolve,10));
  assert.equal(f.session.phase,'listening');
  assert.equal(f.microphones.length,2);
});

test('neural service failure honestly falls back to British browser playback once', async t => {
  const p = playerFixture({fetch:async()=>({ok:false})}), f = voiceFixture(p.player);
  t.after(()=>f.session.stop());
  let task = f.session.speak('First reply'); await tick();
  assert.equal(f.spoken[0].lang,'en-GB');
  assert.match(f.errors[0],/Using the installed British voice/);
  f.spoken[0].onend(); await task;
  task = f.session.speak('Second reply'); await tick();
  assert.equal(f.errors.length,1);
  f.spoken[1].onend(); await task;
});

test('ending a session during generation prevents fallback and late reply audio', async () => {
  const pending = deferred(), p = playerFixture({fetch:()=>pending.promise}), f = voiceFixture(p.player);
  f.session.start(); const task = f.session.commit('Hello'); await tick();
  f.session.stop(); await task;
  pending.resolve(response()); await tick();
  assert.equal(p.audios.length,0);
  assert.equal(f.spoken.length,0);
  assert.equal(f.errors.length,0);
  assert.equal(f.session.enabled,false);
});

test('browser voices still work without an API speech connection', async () => {
  const p = playerFixture({getSettings:()=>({speech_mode:'auto',speech_available:false})});
  const f = voiceFixture(p.player);
  assert.equal(f.session.start(),true);
  const task = f.session.speak('Hello');
  assert.equal(f.spoken.length,1);
  f.spoken[0].onend(); await task;
  f.session.stop();
});

test('a later generation failure falls back only for passages that have not finished', async t => {
  let calls = 0;
  const p = playerFixture({fetch:async()=>++calls===1?response():{ok:false}}), f = voiceFixture(p.player);
  t.after(()=>f.session.stop());
  const text = 'Already spoken naturally. '.repeat(150) + 'The remaining passage. '.repeat(40);
  const expected = speechSegments(text).slice(1).join(' ');
  const task = f.session.speak(text); await tick();
  assert.equal(p.audios.length,1);
  p.audios[0].onended(); await tick();
  const fallback = [];
  while (f.session.utterance) {
    fallback.push(f.session.utterance.text);
    f.session.utterance.onend();
  }
  await task;
  assert.equal(fallback.join(' '),expected);
  assert.equal(p.revoked.length,1);
});

test('explicit natural mode without a key explains the fallback', async () => {
  const p = playerFixture({getSettings:()=>({speech_mode:'neural',speech_available:false})});
  const f = voiceFixture(p.player);
  const task = f.session.speak('Hello');
  assert.match(f.errors[0],/needs an OpenAI speech API key/);
  assert.equal(f.spoken.length,1);
  f.spoken[0].onend(); await task;
  f.session.stop();
});

test('generation timeout aborts the request and prevents late audio', async () => {
  const pending = deferred(), timers = [];
  const p = playerFixture({fetch:()=>pending.promise,
    delay:(callback,ms)=>{const timer={callback,ms};timers.push(timer);return timer;},clear:()=>{}});
  const task = p.player.play('Hello');
  const rejection = assert.rejects(task,/could not play/);
  timers.find(timer=>timer.ms===55000).callback();
  await rejection;
  pending.resolve(response()); await tick();
  assert.equal(p.audios.length,0);
});

test('decoded audio energy reaches the HUD callback and is released after playback', async () => {
  const levels = []; let closed = 0, disconnected = 0, cancelled = 0;
  class AudioContext {
    constructor() {this.state='running';this.destination={};}
    createMediaElementSource() {return {connect() {},disconnect() {disconnected++;}};}
    createAnalyser() {return {fftSize:256,connect() {},disconnect() {disconnected++;},getByteTimeDomainData(samples) {samples.fill(150);}};}
    async close() {closed++;}
  }
  const p = playerFixture({AudioContext,onEnergy:level=>levels.push(level),requestFrame:()=>7,cancelFrame:()=>cancelled++});
  const task = p.player.play('Hello'); await tick();
  assert.ok(levels[0] > 0);
  p.audios[0].onended(); await task;
  assert.equal(levels.at(-1),0);
  assert.equal(closed,1);
  assert.equal(disconnected,2);
  assert.equal(cancelled,1);
});

test('first audio starts from a short passage before later generation completes', async () => {
  const later = deferred(); let requests=0;
  const p = playerFixture({fetch:async()=>++requests===2?later.promise:response()});
  const text = 'The requested application is now open and ready. ' + 'Here is the verified detail. '.repeat(100);
  const task = p.player.play(text); await tick();
  const segments = speechSegments(text);
  assert.ok(segments[0].length<=240);
  assert.ok(segments.length>2);
  assert.equal(requests,3);
  assert.equal(p.audios.filter(audio=>audio.playCount).length,1);
  assert.equal(p.audios[0].playCount,1);
  p.audios[0].onended(); await tick();
  assert.equal(p.audios.filter(audio=>audio.playCount).length,1);
  later.resolve(response()); await tick();
  for(let index=1;index<segments.length;index++) {p.player.active.audio.onended();await tick();}
  await task;
  assert.equal(p.revoked.length,segments.length);
});

test('short conversational replies remain one natural passage', () => {
  const text='Good evening. Spotify is open and ready for you.';
  assert.deepEqual(speechSegments(text),[text]);
});

test('numbered and spaced lists become compact phrases without speaking layout or losing values', () => {
  const input = 'Here is your plan:\n\n  1. **Open Outlook**\n\n  2) Read the 3 messages\n\n  3. Visit https://example.com/a_b#tasks\n';
  const expected = 'Here is your plan: Open Outlook; Read the 3 messages; Visit https://example.com/a_b#tasks.';
  assert.equal(speechText(input),expected);
  assert.equal(speechText(expected),expected);
  assert.equal(speechSegments(input).join(' '),expected);
});

test('bullets, continuation lines and markup use short boundaries while real numbers stay intact', () => {
  const input = '## Today\n\n- [ ] Take 1.5 mg at 9:30\n  with  200 ml water\n\n• Call `Task 2`\n+ Open [the report](https://example.com/report)\n\nThen\tfinish.&nbsp; Thank\u200Byou.';
  const expected = 'Today. Take 1.5 mg at 9:30 with 200 ml water; Call Task 2; Open the report; Then finish. Thankyou.';
  assert.equal(speechText(input),expected);
  assert.equal(speechText('Use C# and https://example.com/a_b#top; keep 2 * 3 = 6.'),'Use C# and https://example.com/a_b#top; keep 2 * 3 = 6.');
  assert.equal(speechText('__Open__ https://example.com/a__b__c'),'Open https://example.com/a__b__c');
  assert.deepEqual(speechSegments('\n\t&nbsp;\u200B\n---\n'),[]);
});

test('prepared neural passages are loaded before playback and reused at the next boundary', async () => {
  const p = playerFixture();
  const text = 'A useful detail with a brief pause. '.repeat(20);
  const task = p.player.play(text); await tick();
  assert.equal(speechSegments(text).length,2);
  const first=p.audios[0], following=p.audios[1];
  assert.equal(first.playCount,1);
  assert.equal(following.loadCount,1);
  assert.equal(following.playCount,0);
  first.onended(); await tick();
  assert.equal(p.player.active.audio,following);
  assert.equal(following.playCount,1);
  assert.equal(following.loadCount,1);
  following.onended(); await task;
  assert.equal(p.audios.length,2);
  assert.equal(p.requests.length,2);
});
