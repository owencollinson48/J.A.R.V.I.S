/* Speech turn-taking is independent of the UI and model provider. */
(function (root) {
  'use strict';

  function chooseVoice(voices, selected = '') {
    const explicit = voices.find(v => (v.voiceURI || v.name) === selected);
    if (explicit) return explicit;
    const british = voices.filter(v => /^en[-_]GB$/i.test(v.lang));
    return british.find(v => /George|Ryan|Daniel|Arthur/i.test(v.name)) || british[0] || null;
  }

  function speechChunks(text) {
    const plain = text.replace(/```[\s\S]*?```/g, 'The code is in our chat.')
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1').replace(/[`*#]/g, '').replace(/\s+/g, ' ').trim();
    const chunks = [];
    let remaining = plain;
    while (remaining.length > 220) {
      const limit = remaining.slice(0, 220);
      const sentence = Math.max(limit.lastIndexOf('. '), limit.lastIndexOf('? '), limit.lastIndexOf('! '));
      const boundary = sentence > 70 ? sentence + 1 : limit.lastIndexOf(' ') > 0 ? limit.lastIndexOf(' ') : 220;
      chunks.push(remaining.slice(0, boundary).trim());
      remaining = remaining.slice(boundary).trim();
    }
    if (remaining) chunks.push(remaining);
    return chunks;
  }

  class VoiceSession {
    constructor(options) {
      this.makeRecognition = options.makeRecognition;
      this.synthesis = options.synthesis;
      this.makeUtterance = options.makeUtterance;
      this.onTurn = options.onTurn;
      this.onState = options.onState || (() => {});
      this.onTranscript = options.onTranscript || (() => {});
      this.onError = options.onError || (() => {});
      this.getBlocker = options.getBlocker || (() => null);
      this.getVoice = options.getVoice || (() => null);
      this.getRate = options.getRate || (() => 1);
      this.delay = options.delay || ((callback, ms) => setTimeout(callback, ms));
      this.clear = options.clear || (id => clearTimeout(id));
      this.silenceMs = options.silenceMs ?? 1200;
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
    }

    status(phase) {
      this.phase = phase;
      this.onState({enabled: this.enabled, phase});
    }

    start() {
      if (!this.makeRecognition || !this.synthesis || !this.makeUtterance) {
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
      this.clear(this.restartTimer);
      this.haltRecognition();
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
        this.cancelSpeech();
        this.status('paused');
      } else if (this.speaking) {
        this.status('speaking');
      } else if (this.processing || blocker) {
        this.haltRecognition();
        this.status(this.processing ? 'thinking' : blocker);
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
          if (this.transcript) this.silenceTimer = this.delay(() => this.commit(), this.silenceMs);
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
      const normalized = text.toLowerCase().replace(/[.!?,]/g, '').trim();
      if (/^(?:jarvis\s+)?(?:stop listening|pause listening|end conversation|stop conversation)$/.test(normalized)) {
        this.stop();
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
      this.haltRecognition();
      this.cancelSpeech();
      if (this.enabled) this.status('thinking');
    }

    async finishTurn(result) {
      this.processing = false;
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
      if (finish) finish();
    }

    speak(text) {
      this.haltRecognition();
      this.cancelSpeech();
      const chunks = speechChunks(text);
      if (!chunks.length || !this.synthesis || !this.makeUtterance) {
        this.refreshContext();
        return Promise.resolve();
      }
      const epoch = this.speechEpoch;
      this.speaking = true;
      this.status('speaking');
      return new Promise(resolve => {
        this.finishSpeech = resolve;
        const next = () => {
          if (epoch !== this.speechEpoch) return;
          this.clear(this.speechTimer);
          if (!chunks.length) {
            this.finishSpeech = null;
            this.speaking = false;
            this.utterance = null;
            resolve();
            if (this.enabled) this.refreshContext();
            else this.status('off');
            return;
          }
          const utterance = this.makeUtterance(chunks.shift());
          this.utterance = utterance;
          const selected = this.getVoice();
          if (selected) utterance.voice = selected;
          utterance.lang = selected?.lang || 'en-GB';
          utterance.rate = this.getRate();
          utterance.onend = next;
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
        next();
      });
    }

    skipReply() {
      this.cancelSpeech();
      this.refreshContext();
    }
  }

  const exported = {VoiceSession, chooseVoice, speechChunks};
  if (typeof module !== 'undefined' && module.exports) module.exports = exported;
  else root.JarvisVoice = exported;
})(typeof window !== 'undefined' ? window : globalThis);
