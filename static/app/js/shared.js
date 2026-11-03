/* ============================================================
   共享模块：工具函数 / API / 全局状态
   ------------------------------------------------------------
   这个文件**没有任何副作用**（不会自动执行初始化），
   所以页面模块可以安全地 import 它，不会触发启动逻辑。
   core.js 才是有副作用的入口。
   ============================================================ */

// ---------- DOM ----------
export const $ = (id) => document.getElementById(id);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function el(tag, attrs = {}, html = '') {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') e.className = v;
    else if (k === 'style') e.style.cssText = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  if (html) e.innerHTML = html;
  return e;
}

// ---------- 格式化 ----------
export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export function fmt(v, d = 2) {
  if (v === null || v === undefined) return '—';
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(d) : '—';
}

export function pct(v, d = 2) {
  if (v === null || v === undefined) return '—';
  const n = Number(v);
  return Number.isFinite(n) ? (n * 100).toFixed(d) + '%' : '—';
}

export function dirCls(v) {
  if (v === null || v === undefined) return '';
  const n = Number(v);
  if (!Number.isFinite(n) || n === 0) return '';
  return n > 0 ? 'up' : 'down';
}

export function thousand(v, d = 2) {
  if (v === null || v === undefined) return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return n.toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d });
}

/** 数值转人话（自动带单位） */
export function human(v) {
  if (v === null || v === undefined) return '—';
  const n = Math.abs(Number(v));
  if (!Number.isFinite(n)) return '—';
  const sign = v < 0 ? '-' : '';
  if (n >= 1e12) return `${sign}${(n / 1e12).toFixed(2)}万亿`;
  if (n >= 1e8) return `${sign}${(n / 1e8).toFixed(2)}亿`;
  if (n >= 1e4) return `${sign}${(n / 1e4).toFixed(2)}万`;
  return `${sign}${n.toFixed(2)}`;
}

export function debounce(fn, ms = 300) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

export function dur(sec) {
  if (!sec) return '—';
  if (sec < 60) return `${Number(sec).toFixed(1)}s`;
  return `${Math.floor(sec / 60)}m${Math.round(sec % 60)}s`;
}

// ---------- API ----------
export const api = {
  async get(path, params) {
    const url = new URL(path, location.origin);
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v);
      }
    }
    const r = await fetch(url);
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${r.status}`);
    }
    return r.json();
  },

  async post(path, body) {
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body ?? {}),
    });
    if (!r.ok) {
      const b = await r.json().catch(() => ({}));
      throw new Error(b.detail || `HTTP ${r.status}`);
    }
    return r.json();
  },

  async put(path, body) {
    const r = await fetch(path, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body ?? {}),
    });
    if (!r.ok) {
      const b = await r.json().catch(() => ({}));
      throw new Error(b.detail || `HTTP ${r.status}`);
    }
    return r.json();
  },

  async del(path) {
    const r = await fetch(path, { method: 'DELETE' });
    if (!r.ok) {
      const b = await r.json().catch(() => ({}));
      throw new Error(b.detail || `HTTP ${r.status}`);
    }
    return r.json();
  },
};

// ---------- 全局状态 ----------
export const state = {
  profiles: [],
  markets: [],
  channels: [],
  settings: {},
  profile: 'balanced',
  marketsOn: new Set(['SH', 'SZ', 'HK', 'US']),
  currentSymbol: null,
  sectors: [],
};

// ---------- 本地存储 ----------
export function saveLocal(key, val) {
  try { localStorage.setItem('qt_' + key, JSON.stringify(val)); } catch (_) { /* 忽略 */ }
}
export function loadLocal(key, def = null) {
  try {
    const v = localStorage.getItem('qt_' + key);
    return v === null ? def : JSON.parse(v);
  } catch (_) { return def; }
}

// ---------- 导航（无副作用，只改 hash） ----------
export function navigate(page, params) {
  const q = params ? '?' + new URLSearchParams(params).toString() : '';
  location.hash = `#/${page}${q}`;
}

// ---------- 通用 UI 片段 ----------
export function pageHead(title, desc, actionsHTML = '') {
  return `
  <div class="page-head">
    <div>
      <div class="page-title">${esc(title)}</div>
      ${desc ? `<div class="page-desc">${esc(desc)}</div>` : ''}
    </div>
    <div class="flex center gap-8">${actionsHTML}</div>
  </div>`;
}

export function emptyBox(title, desc, actionHTML = '') {
  return `
  <div class="card"><div class="empty">
    <div class="empty-title">${esc(title)}</div>
    <div class="empty-desc">${desc}</div>
    ${actionHTML ? `<div class="mt-20">${actionHTML}</div>` : ''}
  </div></div>`;
}

export function alertBox(kind, html) {
  const cls = { error: 'alert-error', warn: 'alert-warn',
                ok: 'alert-ok', info: 'alert-info' }[kind] || 'alert-info';
  return `<div class="alert ${cls}">${html}</div>`;
}
