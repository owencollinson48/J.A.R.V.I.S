/* Speech turn-taking is independent of the UI and model provider. */
(function (root) {
  'use strict';

  const {speechText} = typeof module !== 'undefined' && module.exports ? require('./speech.js') : root.JarvisSpeech;

  function chooseVoice(voices, selected = '') {
    const explicit = voices.find(v => (v.voiceURI || v.name) === selected);
    if (explicit) return explicit;
    const british = voices.filter(v => /^en[-_]GB(?:[-_].*)?$/i.test(v.lang));
    const quality = voice => (/natural|neural|enhanced|premium/i.test(voice.name) ? 100 : 0) +
      (/Ryan/i.test(voice.name) ? 40 : /Daniel/i.test(voice.name) ? 30 : /George|Arthur/i.test(voice.name) ? 20 : 0);
    return british.sort((left, right) => quality(right) - quality(left))[0] || null;
  }

  function speechChunks(text) {
    const plain = speechText(text);
    const chunks = [];
    let remaining = plain;
    while (remaining.length > 220) {
      const limit = remaining.slice(0, 220);
      const sentence = Math.max(limit.lastIndexOf('. '), limit.lastIndexOf('? '), limit.lastIndexOf('! '), limit.lastIndexOf('; '));
      const boundary = sentence > 70 ? sentence + 1 : limit.lastIndexOf(' ') > 0 ? limit.lastIndexOf(' ') : 220;
      chunks.push(remaining.slice(0, boundary).trim());
      remaining = remaining.slice(boundary).trim();
    }
    if (remaining) chunks.push(remaining);
    return chunks;
  }

  function stopIntent(text) {
    const value = text.toLowerCase().replace(/[.!?,]/g, '').trim().replace(/^jarvis\s+/, '')
      .replace(/^(?:can|could|would) you\s+(?:please\s+)?/, '').replace(/^please\s+/, '').replace(/\s+please$/, '');
    if (/^(?:stop listening|pause listening|end conversation|stop conversation)$/.test(value)) return 'listening';
    if (/^(?:stop|stop everything|stop working|stop the task|stop task|cancel|cancel it|cancel that|cancel task|cancel the task|hold on|hang on)$/.test(value)) return 'task';
    return null;
  }

  function tradingStopIntent(text) {
    if(typeof text!=='string')return null;
    const value=text.toLowerCase().replace(/[’]/g,"'").replace(/[.!?,]/g,' ').replace(/\s+/g,' ').trim()
      .replace(/^(?:hey\s+)?(?:jarvis\s+)?(?:(?:please|can you|could you|would you|i want you to)\s+)*/,'')
      .replace(/\s+(?:please|sir)$/,'');
    if(/\b(?:don't|do not|dont|never|not|why|explain|what if|how to|example|quote|when|unless|if)\b/.test(value))return null;
    if(!/^(?:(?:emergency|immediately)\s+)?(?:stop|halt|pause|disable|turn off|shut down)\s+(?:(?:all|my|the|automated|automatic|paper|new|futures|nq|nasdaq)\s+)*(?:trading|trades|automated entries)(?:\s+(?:now|right now|immediately|at once|straight away|for now|for this session|today|for today|for the day|for the rest of (?:the )?day|for the rest of today|for the day today|for the remainder of (?:the )?day|for (?:the )?rest of (?:the )?day|please|sir))*$/.test(value))return null;
    if(/\b(?:emergency|immediately)\b/.test(value))return 'emergency';
    return /\b(?:today|day)\b/.test(value)?'pause_today':'off';
  }

  class VoiceSession {
    constructor(options) {
      this.makeRecognition = options.makeRecognition;
      this.synthesis = options.synthesis;
      this.makeUtterance = options.makeUtterance;
      this.speechPlayer = options.speechPlayer;
      this.onTurn = options.onTurn;
      this.onCancel = options.onCancel;
      this.onTradingStop = options.onTradingStop;
      this.onBoundary = options.onBoundary || (() => {});
      this.onState = options.onState || (() => {});
      this.onTranscript = options.onTranscript || (() => {});
      this.onError = options.onError || (() => {});
      this.getBlocker = options.getBlocker || (() => null);
      this.getVoice = options.getVoice || (() => null);
      this.getRate = options.getRate || (() => 1);
      this.delay = options.delay || ((callback, ms) => setTimeout(callback, ms));
      this.clear = options.clear || (id => clearTimeout(id));
      this.silenceMs = options.silenceMs ?? 1200;
      this.finalSilenceMs = options.finalSilenceMs ?? Math.min(this.silenceMs, 650);
      this.restartMs = options.restartMs ?? 400;
      this.enabled = false;
      this.processing = false;
      this.speaking = false;
      this.phase = 'off';
      this.recognition = null;
      this.transcript = '';
      this.emptyEnds = 0;
      this.epoch = 0;
      this.speechEpoch = 0;
      this.controlStopSequence = 0;
      this.tradingStopRequested = false;
    }

    status(phase) {
      this.phase = phase;
      this.onState({enabled: this.enabled, phase});
    }

    start() {
      if (!this.makeRecognition || (!(this.synthesis && this.makeUtterance) && !this.speechPlayer?.canUse())) {
        this.onError('Hands-free voice needs speech recognition and playback. Open JARVIS in Chrome or Edge.');
        return false;
      }
      this.enabled = true;
      this.emptyEnds = 0;
      this.epoch++;
      this.refreshContext(true);
      return this.enabled;
    }

    stop(message = '') {
      this.enabled = false;
      this.epoch++;
      this.tradingStopRequested = false;
      this.controlStopSequence++;
      this.clear(this.restartTimer);
      this.haltRecognition();
      this.stopControlListener();
      this.cancelSpeech();
      this.onTranscript('');
      this.status('off');
      if (message) this.onError(message);
    }

    haltRecognition() {
      this.clear(this.silenceTimer);
      this.clear(this.restartTimer);
      const recognition = this.recognition;
      this.recognition = null; // Ignore late browser callbacks after abort.
      this.transcript = '';
      if (recognition) {
        try { recognition.abort(); } catch (_) { /* Already ended. */ }
      }
    }

    refreshContext(immediate = false) {
      if (!this.enabled) return;
      const blocker = this.getBlocker();
      if (blocker === 'paused') {
        this.haltRecognition();
        this.stopControlListener();
        this.cancelSpeech();
        this.status('paused');
      } else if (this.speaking) {
        this.status('speaking');
      } else if (this.processing || blocker) {
        this.haltRecognition();
        this.status(this.processing ? 'thinking' : blocker);
        if (this.processing) this.scheduleControlListener();
      } else if (!this.recognition) {
        this.clear(this.restartTimer);
        this.status('starting');
        if (immediate) this.listen();
        else this.restartTimer = this.delay(() => this.listen(), this.restartMs);
      }
    }

    listen() {
      if (!this.enabled || this.processing || this.speaking || this.getBlocker() || this.recognition) return;
      let recognition;
      try {
        recognition = this.makeRecognition();
        this.recognition = recognition;
        this.transcript = '';
        recognition.lang = 'en-GB';
        recognition.continuous = true;
        recognition.interimResults = true;
        const current = () => this.recognition === recognition && this.enabled;
        const startedAt = Date.now();
        recognition.onstart = () => { if (current()) this.status('listening'); };
        recognition.onresult = event => {
          if (!current()) return;
          this.emptyEnds = 0;
          this.transcript = Array.from(event.results).map(result => result[0].transcript).join(' ').trim();
          this.onTranscript(this.transcript);
          this.clear(this.silenceTimer);
          const final = Array.from(event.results).every(result => result.isFinal === true);
          if (this.transcript) this.silenceTimer = this.delay(() => this.commit(), final ? this.finalSilenceMs : this.silenceMs);
        };
        recognition.onend = () => {
          if (!current()) return;
          const text = this.transcript;
          this.recognition = null;
          this.clear(this.silenceTimer);
          if (text) { this.commit(text); return; }
          if (Date.now() - startedAt < 800 && ++this.emptyEnds >= 5) {
            this.stop('The speech service keeps ending. Check your microphone, then click the circle to retry.');
            return;
          }
          this.refreshContext();
        };
        recognition.onerror = event => {
          if (!current()) return;
          if (event.error === 'no-speech') {
            this.haltRecognition();
            this.refreshContext();
            return;
          }
          const messages = {
            'not-allowed': 'Microphone access was denied. Allow the microphone in your browser, then click the circle again.',
            'service-not-allowed': 'The browser speech service is unavailable. Try Chrome or Edge and check speech permissions.',
            'audio-capture': 'No usable microphone was found. Check your Windows input device.',
            'network': 'The browser speech service lost its connection. Check Internet access, then click the circle to retry.',
          };
          this.stop(messages[event.error] || 'Speech recognition stopped. Click the circle to try again.');
        };
        recognition.start();
      } catch (_) {
        this.stop('Could not start the microphone. Check browser permissions and use Chrome or Edge.');
      }
    }

    async commit(text = this.transcript) {
      if (!this.enabled || this.processing || this.speaking || this.getBlocker() || !text.trim()) return;
      const intent = stopIntent(text);
      if (intent === 'listening') {
        this.stop();
        return;
      }
      if (intent === 'task' && this.onCancel) {
        this.haltRecognition();
        try { await this.onCancel(); } catch (_) { this.onError('Could not request cancellation. Use Stop task or the Windows emergency hotkey.'); }
        this.refreshContext();
        return;
      }
      const epoch = this.epoch;
      this.beginTurn();
      try {
        const result = await this.onTurn(text.trim());
        // Ending the session during a request suppresses that reply's audio.
        if (epoch !== this.epoch) { this.processing = false; this.refreshContext(); return; }
        await this.finishTurn(result || {});
      } catch (_) {
        this.processing = false;
        this.stop('This voice turn could not finish. Check the chat message before retrying.');
      }
    }

    beginTurn() {
      this.processing = true;
      this.cancelRequested = false;
      this.tradingStopRequested = false;
      this.controlStopSequence = (this.controlStopSequence || 0)+1;
      this.haltRecognition();
      this.stopControlListener();
      this.cancelSpeech();
      if (this.enabled) this.status('thinking');
      this.scheduleControlListener();
    }

    async finishTurn(result) {
      this.processing = false;
      this.stopControlListener();
      if (result.error) {
        if (this.enabled) this.stop(result.error);
        return;
      }
      if (!this.enabled) return;
      if (this.getBlocker() === 'paused') { this.refreshContext(); return; }
      const text = result.text || (result.approvals ? 'Please review the computer action on screen. I will wait for your approval.' : '');
      if (text) await this.speak(text);
      else this.refreshContext();
    }

    cancelSpeech() {
      this.speechEpoch++;
      this.clear(this.speechTimer);
      const finish = this.finishSpeech;
      this.finishSpeech = null;
      this.speaking = false;
      this.utterance = null;
      if (this.synthesis) this.synthesis.cancel();
      this.speechPlayer?.cancel();
      if (finish) finish();
    }

    speak(text) {
      this.haltRecognition();
      this.stopControlListener();
      this.cancelSpeech();
      const chunks = speechChunks(text);
      const neural = this.speechPlayer?.canUse();
      const browser = this.synthesis && this.makeUtterance;
      const unavailable = this.speechPlayer?.unavailableReason?.();
      if (unavailable && !this.notifiedSpeechFallback) {
        this.notifiedSpeechFallback = true;
        this.onError(unavailable);
      }
      if (!chunks.length || (!neural && !browser)) {
        this.refreshContext();
        return Promise.resolve();
      }
      const epoch = this.speechEpoch;
      this.speaking = true;
      this.status('speaking');
      return new Promise(resolve => {
        this.finishSpeech = resolve;
        const complete = () => {
          this.finishSpeech = null;
          this.speaking = false;
          this.utterance = null;
          resolve();
          if (this.enabled) this.refreshContext();
          else this.status('off');
        };
        const next = () => {
          if (epoch !== this.speechEpoch) return;
          this.clear(this.speechTimer);
          if (!chunks.length) {
            complete();
            return;
          }
          const utterance = this.makeUtterance(chunks.shift());
          this.utterance = utterance;
          const selected = this.getVoice();
          if (selected) utterance.voice = selected;
          utterance.lang = selected?.lang || 'en-GB';
          utterance.rate = this.getRate();
          utterance.onend = next;
          utterance.onboundary = event => { if (epoch === this.speechEpoch) this.onBoundary(event); };
          utterance.onerror = () => {
            if (epoch !== this.speechEpoch) return;
            this.stop('Speech playback failed. Check the selected voice in Settings, then try again.');
          };
          this.speechTimer = this.delay(() => {
            if (epoch === this.speechEpoch) this.stop('Speech playback stalled. Choose another voice in Settings and retry.');
          }, 60000);
          try { this.synthesis.speak(utterance); }
          catch (_) { utterance.onerror(); }
        };
        if (neural) {
          // One generated passage preserves natural cadence across sentences;
          // the transport handles the service's input limit and prefetches more.
          this.speechPlayer.play(chunks.join(' '), {rate: this.getRate(), onBoundary: this.onBoundary}).then(() => {
            if (epoch === this.speechEpoch) complete();
          }).catch(error => {
            if (epoch !== this.speechEpoch) return;
            if (!browser) { this.stop('The natural voice could not play this reply. Check the speech settings.'); return; }
            if (!this.notifiedSpeechFallback) {
              this.notifiedSpeechFallback = true;
              this.onError('The natural voice is unavailable for this reply. Using the installed British voice.');
            }
            if (error.remainingText) chunks.splice(0, chunks.length, ...speechChunks(error.remainingText));
            next();
          });
        } else next();
      });
    }

    skipReply() {
      this.cancelSpeech();
      this.refreshContext();
    }

    stopControlListener() {
      this.clear(this.controlTimer);
      const microphone = this.controlRecognition;
      this.controlRecognition = null;
      if (microphone) { try { microphone.abort(); } catch (_) {} }
    }

    scheduleControlListener() {
      if (!(this.onCancel || this.onTradingStop) || !this.enabled || !this.processing || this.speaking || this.cancelRequested || this.tradingStopRequested || this.controlRecognition) return;
      const blocker = this.getBlocker();
      if (blocker && blocker !== 'thinking') return;
      this.clear(this.controlTimer);
      this.controlTimer = this.delay(() => {
        if (!this.enabled || !this.processing || this.speaking || this.cancelRequested || this.tradingStopRequested || (this.getBlocker() && this.getBlocker() !== 'thinking')) return;
        try {
          const microphone = this.makeRecognition();
          this.controlRecognition = microphone;
          microphone.lang = 'en-GB'; microphone.continuous = true; microphone.interimResults = false;
          const current = () => this.controlRecognition === microphone && this.enabled && this.processing;
          microphone.onstart = () => { if (current()) this.status('thinking'); };
          microphone.onresult = event => {
            if (!current()) return;
            const latest = event.results[event.results.length - 1];
            if (!latest || latest.isFinal === false) return;
            const intent = stopIntent(latest[0].transcript);
            if (intent === 'listening') { this.stop(); return; }
            const trading=tradingStopIntent(latest[0].transcript);
            if(trading && this.onTradingStop){
              const text=latest[0].transcript.trim(),epoch=this.epoch,sequence=++this.controlStopSequence;
              this.tradingStopRequested=true;
              this.stopControlListener();
              Promise.resolve().then(()=>this.onTradingStop(text)).then(async result=>{
                if(epoch!==this.epoch || sequence!==this.controlStopSequence || !this.enabled)return;
                if(result?.error){this.onError(result.error);return;}
                if(result?.text && this.processing && !this.speaking)await this.speak(result.text);
              }).catch(()=>{if(epoch===this.epoch)this.onError('Could not reach the trading stop control. Use Emergency stop in Trading or Ctrl+C in the JARVIS terminal.');}).finally(()=>{
                if(sequence!==this.controlStopSequence)return;
                this.tradingStopRequested=false;
                if(epoch===this.epoch && this.enabled && this.processing && !this.speaking)this.scheduleControlListener();
              });
              return;
            }
            if (intent !== 'task' || !this.onCancel) return; // Busy recognition accepts stops only, never new entries.
            this.cancelRequested = true;
            this.stopControlListener();
            Promise.resolve(this.onCancel()).catch(() => this.onError('Could not request cancellation. Use Stop task or the Windows emergency hotkey.'));
          };
          microphone.onend = () => { if (current()) { this.controlRecognition = null; this.scheduleControlListener(); } };
          microphone.onerror = event => {
            if (!current()) return;
            this.stopControlListener();
            if (event.error === 'no-speech') this.scheduleControlListener();
            else this.onError('Voice cancellation is unavailable. Use Stop task, Escape, or Ctrl+Alt+F8.');
          };
          microphone.start();
        } catch (_) { this.stopControlListener(); this.onError('Voice cancellation could not start. Use Stop task or Ctrl+Alt+F8.'); }
      }, this.restartMs);
    }
  }

  const exported = {VoiceSession, chooseVoice, speechChunks, stopIntent, tradingStopIntent};
  if (typeof module !== 'undefined' && module.exports) module.exports = exported;
  else root.JarvisVoice = exported;
})(typeof window !== 'undefined' ? window : globalThis);
