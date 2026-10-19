/* Optional natural speech; API credentials stay on the local JARVIS server. */
(function (root) {
  'use strict';

  // Speech needs phrases, not visual indentation. Keep list items in one
  // passage with a brief punctuation pause instead of speaking their markup.
  function speechText(text) {
    const lines = String(text).replace(/```[\s\S]*?```/g, 'The code is in our chat.')
      .replace(/<br\s*\/?\s*>/gi, '\n').replace(/&nbsp;|&#160;|&#xA0;/gi, ' ')
      .replace(/[\u200B-\u200D\u2060\uFEFF]/g, '').replace(/\r\n?/g, '\n')
      .replace(/\[([^\]]+)\]\((?:[^()]|\([^)]*\))*\)/g, '$1')
      .replace(/\*\*([^*]+)\*\*/g, '$1')
      .replace(/(^|\s)__([^_\n]+)__(?=\s|[.,!?;:]|$)/g, '$1$2')
      .replace(/`([^`]+)`/g, '$1')
      .replace(/(^|\s)\*([^*\n]+)\*(?=\s|[.,!?;:]|$)/g, '$1$2')
      .split('\n');
    const phrases = [];
    let paragraph = false;
    for (const line of lines) {
      let value = line.trim();
      if (!value || /^(?:[-*_]\s*){3,}$/.test(value)) { paragraph = true; continue; }
      const indent = line.search(/\S/);
      const list = /^(?:[-*+\u2022\u2023\u25E6\u25AA\u25CF]|\d{1,3}[.)])\s+/.test(value);
      value = value.replace(/^#{1,6}\s+/, '').replace(/^(?:[-*+\u2022\u2023\u25E6\u25AA\u25CF]|\d{1,3}[.)])\s+/, '')
        .replace(/^\[[ xX]\]\s+/, '').replace(/\s+/g, ' ').trim();
      if (!value) continue;
      const previous = phrases.at(-1);
      if (previous?.list && !list && !paragraph && indent > previous.indent) {
        previous.text += ' ' + value;
      } else {
        if (previous && (previous.list || list || paragraph) && !/[.!?;:\u2026]$/.test(previous.text)) {
          previous.text += previous.list ? ';' : '.';
        }
        phrases.push({text: value, list, indent});
      }
      paragraph = false;
    }
    const last = phrases.at(-1);
    if (last?.list && !/[.!?;:\u2026]$/.test(last.text)) last.text += '.';
    return phrases.map(phrase => phrase.text).join(' ');
  }

  function speechSegments(text, maxLength = 1200, firstLength = 240) {
    const parts = [];
    let remaining = speechText(text);
    while (remaining.length > maxLength || (!parts.length && remaining.length > firstLength + 100)) {
      const length = parts.length ? maxLength : Math.min(firstLength, maxLength);
      const limit = remaining.slice(0, length);
      const sentence = Math.max(limit.lastIndexOf('. '), limit.lastIndexOf('? '), limit.lastIndexOf('! '), limit.lastIndexOf('; '));
      const word = limit.lastIndexOf(' ');
      const end = sentence > length / 2 ? sentence + 1 : word > 0 ? word : length;
      parts.push(remaining.slice(0, end).trim());
      remaining = remaining.slice(end).trim();
    }
    if (remaining) parts.push(remaining);
    return parts;
  }

  class SpeechPlayer {
    constructor(options = {}) {
      this.fetch = options.fetch || root.fetch?.bind(root);
      this.audioFactory = options.audioFactory || (root.Audio ? () => new root.Audio() : null);
      this.createObjectURL = options.createObjectURL || root.URL?.createObjectURL?.bind(root.URL);
      this.revokeObjectURL = options.revokeObjectURL || root.URL?.revokeObjectURL?.bind(root.URL);
      this.getSettings = options.getSettings || (() => ({}));
      this.onEnergy = options.onEnergy || (() => {});
      this.AudioContext = options.AudioContext || root.AudioContext || root.webkitAudioContext;
      this.requestFrame = options.requestFrame || root.requestAnimationFrame?.bind(root);
      this.cancelFrame = options.cancelFrame || root.cancelAnimationFrame?.bind(root);
      this.delay = options.delay || ((callback, ms) => setTimeout(callback, ms));
      this.clear = options.clear || (id => clearTimeout(id));
      this.generation = 0;
      this.active = null;
    }

    canUse() {
      const settings = this.getSettings() || {};
      return settings.speech_mode !== 'browser' && !!settings.speech_available &&
        !!(this.fetch && this.audioFactory && this.createObjectURL);
    }

    unavailableReason() {
      const settings = this.getSettings() || {};
      if (settings.speech_mode !== 'neural' || this.canUse()) return '';
      return !settings.speech_available ?
        'The natural voice needs an OpenAI speech API key. Using the installed British voice.' :
        'Natural audio playback is unavailable in this browser. Using the installed British voice.';
    }

    play(text, options = {}) {
      this.cancel();
      const segments = speechSegments(text);
      if (!segments.length) return Promise.resolve();
      if (!this.canUse()) return Promise.reject(new Error('The natural voice is unavailable.'));
      const generation = this.generation;
      return new Promise((resolve, reject) => {
        const operation = {generation, resolve, reject, controller: new AbortController(), urls: new Set(), timers: new Set(), preparedAudio: new Set(), audio: null, completed: 0};
        this.active = operation;
        this.run(operation, segments, options).then(() => {
          if (this.current(operation)) { this.cleanup(operation); this.active = null; resolve(); }
        }).catch(() => {
          if (this.current(operation)) {
            this.cleanup(operation); this.active = null;
            const error = new Error('The natural voice could not play this reply.');
            error.remainingText = segments.slice(operation.completed).join(' ');
            reject(error);
          }
        });
      });
    }

    current(operation) { return this.active === operation && operation.generation === this.generation; }

    deadline(operation, task, milliseconds) {
      let timer;
      const timeout = new Promise((_resolve, reject) => {
        timer = this.delay(() => { operation.controller.abort(); reject(new Error('Speech timed out.')); }, milliseconds);
        operation.timers.add(timer);
      });
      return Promise.race([task, timeout]).finally(() => { this.clear(timer); operation.timers.delete(timer); });
    }

    prepare(operation, text, rate) {
      return this.deadline(operation, this.fetchAudio(operation, text, rate), 55000).then(url => {
        if (!this.current(operation) || !url) return null;
        const audio = this.audioFactory();
        operation.preparedAudio.add(audio);
        audio.preload = 'auto'; audio.src = url;
        // Loading now lets the browser decode the following passage while the
        // current passage plays, rather than waiting until its ended event.
        audio.load?.();
        return {url, audio};
      });
    }

    async fetchAudio(operation, text, rate) {
      const response = await this.fetch('/api/speech', {method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-Jarvis-Client': 'dashboard'},
        body: JSON.stringify({text, rate}), signal: operation.controller.signal});
      if (!this.current(operation)) return null;
      if (!response.ok) throw new Error('Speech generation failed.');
      const blob = await response.blob();
      if (!this.current(operation)) return null;
      if (!blob.size || blob.size > 10 * 1024 * 1024) throw new Error('Speech audio size is invalid.');
      const url = this.createObjectURL(blob);
      operation.urls.add(url);
      return url;
    }

    async run(operation, segments, options) {
      const rate = Number.isFinite(options.rate) ? Math.min(1.25, Math.max(0.75, options.rate)) : 1;
      const prepared = [];
      const prefetch = index => {
        if (index >= segments.length) return;
        prepared[index] = this.prepare(operation, segments[index], rate);
        // A later request may fail while the current audio is playing.
        prepared[index].catch(() => {});
      };
      // Do not wait for a long reply's complete audio before starting. Generate
      // the short opening and following passage together, with bounded lookahead.
      prefetch(0); prefetch(1);
      for (let index = 0; index < segments.length; index++) {
        const passage = await prepared[index];
        if (!this.current(operation) || !passage) return;
        prefetch(index + 2);
        await this.deadline(operation, this.playAudio(operation, passage.audio, options.onEnergy || this.onEnergy), 10 * 60 * 1000);
        if (!this.current(operation)) return;
        operation.completed = index + 1;
        this.revoke(operation, passage.url);
      }
    }

    playAudio(operation, audio, onEnergy) {
      return new Promise((resolve, reject) => {
        if (!this.current(operation)) { resolve(); return; }
        operation.audio = audio;
        operation.endAudio = resolve;
        const ended = error => {
          if (operation.audio !== audio) return;
          operation.stopEnergy?.(); operation.stopEnergy = null;
          operation.audio = null; operation.endAudio = null;
          operation.preparedAudio.delete(audio);
          audio.onended = null; audio.onerror = null;
          try { audio.removeAttribute?.('src'); audio.load?.(); } catch (_) {}
          if (error) reject(new Error('Audio playback failed.')); else resolve();
        };
        audio.onended = () => ended(false);
        audio.onerror = () => ended(true);
        try {
          Promise.resolve(audio.play()).then(() => {
            if (!this.current(operation) || operation.audio !== audio) return;
            this.startEnergy(operation, audio, onEnergy);
          }).catch(() => ended(true));
        } catch (_) { ended(true); }
      });
    }

    async startEnergy(operation, audio, onEnergy) {
      if (!this.AudioContext || !this.requestFrame) return;
      let context;
      try {
        context = new this.AudioContext();
        if (context.state === 'suspended') await context.resume();
        if (!this.current(operation) || operation.audio !== audio || context.state !== 'running') {
          await context.close(); return;
        }
        const source = context.createMediaElementSource(audio);
        const analyser = context.createAnalyser(); analyser.fftSize = 256;
        source.connect(analyser); analyser.connect(context.destination);
        const samples = new Uint8Array(analyser.fftSize);
        let frame, stopped = false;
        const update = () => {
          if (stopped || !this.current(operation)) return;
          analyser.getByteTimeDomainData(samples);
          let sum = 0;
          for (const value of samples) sum += ((value - 128) / 128) ** 2;
          onEnergy(Math.min(1, Math.sqrt(sum / samples.length) * 4));
          frame = this.requestFrame(update);
        };
        operation.stopEnergy = () => {
          stopped = true;
          if (frame !== undefined) this.cancelFrame?.(frame);
          source.disconnect(); analyser.disconnect();
          context.close().catch(() => {}); onEnergy(0);
        };
        update();
      } catch (_) { if (context) context.close().catch(() => {}); }
    }

    revoke(operation, url) {
      if (operation.urls.delete(url)) this.revokeObjectURL?.(url);
    }

    cleanup(operation) {
      operation.controller.abort();
      for (const timer of operation.timers) this.clear(timer);
      operation.timers.clear();
      operation.stopEnergy?.(); operation.stopEnergy = null;
      if (operation.audio) {
        operation.preparedAudio.delete(operation.audio);
        operation.audio.onended = null; operation.audio.onerror = null;
        try { operation.audio.pause(); operation.audio.removeAttribute?.('src'); operation.audio.load?.(); } catch (_) {}
        operation.audio = null;
      }
      for (const audio of operation.preparedAudio) {
        audio.onended = null; audio.onerror = null;
        try { audio.pause(); audio.removeAttribute?.('src'); audio.load?.(); } catch (_) {}
      }
      operation.preparedAudio.clear();
      operation.endAudio?.(); operation.endAudio = null;
      for (const url of Array.from(operation.urls)) this.revoke(operation, url);
    }

    cancel() {
      this.generation++;
      const operation = this.active;
      this.active = null;
      if (operation) { this.cleanup(operation); operation.resolve(); }
    }
  }

  const exported = {SpeechPlayer, speechSegments, speechText};
  if (typeof module !== 'undefined' && module.exports) module.exports = exported;
  else root.JarvisSpeech = exported;
})(typeof window !== 'undefined' ? window : globalThis);
