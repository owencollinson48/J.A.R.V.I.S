/* On-demand visual companions. Model output is data, never executable markup. */
(function (root) {
  'use strict';
  const kinds = new Set(['timeline', 'cards', 'bars']);
  const statuses = new Set(['planned', 'active', 'done']);
  const clean = (value, limit) => typeof value === 'string' ? value.trim().slice(0, limit) : '';

  function normalizeVisual(value) {
    if (!value || typeof value !== 'object' || !kinds.has(value.kind) || !Array.isArray(value.items)) return null;
    const items = value.items.slice(0, 12).flatMap(item => {
      if (!item || typeof item !== 'object') return [];
      const label = clean(item.label, 140);
      if (!label) return [];
      return [{
        label, detail: clean(item.detail, 500), time: clean(item.time, 60),
        value: typeof item.value === 'number' && Number.isFinite(item.value) ? item.value : null,
        status: statuses.has(item.status) ? item.status : 'planned',
      }];
    });
    if (!items.length) return null;
    return {
      id: clean(value.id, 120), kind: value.kind, title: clean(value.title, 160) || 'Your visual',
      subtitle: clean(value.subtitle, 320), unit: clean(value.unit, 32),
      created_at: clean(value.created_at, 80), items,
    };
  }

  function barPercent(value, maximum) {
    if (typeof value !== 'number' || !Number.isFinite(value) || !Number.isFinite(maximum) || maximum <= 0) return 0;
    return Math.max(0, Math.min(100, Math.abs(value) / maximum * 100));
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = { normalizeVisual, barPercent };
  if (typeof document === 'undefined') return;

  const panel = document.querySelector('#visual-panel');
  const stage = document.querySelector('#chat-view');
  if (!panel || !stage) return;
  let current = null;

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function clear() {
    current = null;
    panel.hidden = true;
    panel.replaceChildren();
    stage.classList.remove('has-visual');
  }

  function dismiss() {
    const id = current?.id;
    const restoreFocus = panel.contains(document.activeElement);
    clear();
    if (restoreFocus) document.querySelector('#jarvis-hud')?.focus({ preventScroll: true });
    if (id) document.dispatchEvent(new CustomEvent('jarvis:visual-dismiss', { detail: { id } }));
  }

  function addCopy(parent, item) {
    const copy = node('div', 'visual-item-copy');
    copy.append(node('strong', 'visual-item-label', item.label));
    if (item.detail) copy.append(node('p', 'visual-item-detail', item.detail));
    parent.append(copy);
    return copy;
  }

  function show(value) {
    const visual = normalizeVisual(value);
    if (!visual) return false;
    current = visual;
    panel.replaceChildren();
    panel.dataset.kind = visual.kind;
    const header = node('div', 'visual-heading');
    const headingCopy = node('div', 'visual-heading-copy');
    const eyebrow = node('span', 'visual-eyebrow', visual.kind === 'timeline' ? 'TIMELINE' : visual.kind === 'bars' ? 'AT A GLANCE' : 'YOUR OUTLINE');
    const title = node('h2', '', visual.title);
    title.id = 'visual-title';
    headingCopy.append(eyebrow, title);
    if (visual.subtitle) headingCopy.append(node('p', 'visual-subtitle', visual.subtitle));
    const close = node('button', 'visual-close', '×');
    close.type = 'button';
    close.setAttribute('aria-label', 'Dismiss visual');
    close.addEventListener('click', dismiss);
    header.append(headingCopy, close);
    panel.append(header);

    const list = node(visual.kind === 'bars' ? 'ul' : 'ol', 'visual-items visual-' + visual.kind);
    const maximum = Math.max(0, ...visual.items.map(item => Math.abs(item.value || 0)));
    const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
    visual.items.forEach((item, index) => {
      const entry = node('li', 'visual-item');
      entry.dataset.status = item.status;
      if (visual.kind === 'timeline') {
        const time = node('div', 'visual-time');
        if (item.time) time.append(node('span', '', item.time));
        else { const marker = node('span', 'visual-time-marker', ''); marker.setAttribute('aria-hidden', 'true'); time.append(marker); }
        entry.append(time);
        addCopy(entry, item);
      } else if (visual.kind === 'cards') {
        const indexLabel = node('span', 'visual-card-index', String(index + 1).padStart(2, '0'));
        indexLabel.setAttribute('aria-hidden', 'true');
        entry.append(indexLabel);
        const copy = addCopy(entry, item);
        if (item.time) copy.append(node('span', 'visual-card-time', item.time));
      } else {
        const top = node('div', 'visual-bar-heading');
        top.append(node('strong', 'visual-item-label', item.label));
        const amount = item.value === null ? '—' : number.format(item.value) + (visual.unit ? ' ' + visual.unit : '');
        top.append(node('span', 'visual-bar-value', amount));
        const track = node('div', 'visual-bar-track');
        track.setAttribute('aria-hidden', 'true');
        const fill = node('span', 'visual-bar-fill');
        fill.style.width = barPercent(item.value, maximum) + '%';
        if (item.value < 0) fill.classList.add('visual-bar-negative');
        track.append(fill);
        entry.append(top, track);
        if (item.detail) entry.append(node('p', 'visual-item-detail', item.detail));
      }
      if (item.status !== 'planned') {
        const status = node('span', 'visual-item-status', item.status === 'done' ? 'Done' : 'Current');
        entry.append(status);
      }
      list.append(entry);
    });
    panel.append(list);
    panel.hidden = false;
    stage.classList.add('has-visual');
    return true;
  }

  root.JarvisVisual = { show, clear, dismiss, get current() { return current; } };
})(typeof window === 'undefined' ? globalThis : window);
