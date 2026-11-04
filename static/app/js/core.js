/* ============================================================
   应用入口（有副作用，只在 index.html 加载一次）
   工具函数 / API / 状态 都在 shared.js
   ============================================================ */

import {
  $, $$, esc, api, state, saveLocal, loadLocal,
  debounce, navigate, emptyBox,
} from './shared.js';

// ============================================================
// 路由
// ============================================================
const PAGES = {
  analysis:  { module: './pages/analysis.js' },
  screen:    { module: './pages/screen.js' },
  watchlist: { module: './pages/watchlist.js' },
  concepts:  { module: './pages/concepts.js' },
  news:      { module: './pages/news.js' },
  library:   { module: './pages/library.js' },
  catalog:   { module: './pages/catalog.js' },
  settings:  { module: './pages/settings.js' },
};

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [path, query] = raw.split('?');
  return {
    page: path || 'analysis',
    params: Object.fromEntries(new URLSearchParams(query || '')),
  };
}

/** 页面上下文：统一传给每个页面模块 */
export function makeCtx(params) {
  return {
    params,
    api, state, navigate,
    confirm: (msg) => window.confirm(msg),
    toast: showToast,
  };
}

async function renderPage() {
  const { page, params } = parseHash();
  const def = PAGES[page] || PAGES.analysis;

  $$('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.page === page));

  // 顶栏元素按页面显隐
  const showMarkets = ['analysis', 'screen'].includes(page);
  $('marketChips').classList.toggle('hidden', !showMarkets);
  $('profileSelect').classList.toggle('hidden', !['analysis', 'screen'].includes(page));

  const content = $('content');
  content.innerHTML = '<div class="loading"><div class="spinner"></div><span>加载中…</span></div>';

  try {
    const mod = await import(def.module + '?v=' + Date.now());
    await mod.render(content, makeCtx(params));
  } catch (err) {
    console.error('[page error]', err);
    content.innerHTML = `
      <div class="card"><div class="card-pad">
        <div class="alert alert-error">
          <b>页面加载失败</b><br>${esc(err.message)}
          <div class="mt-8 fs-12" style="opacity:.7">按 F12 打开控制台可看详细堆栈</div>
        </div>
      </div></div>`;
  }
}

// ============================================================
// Toast
// ============================================================
let toastTimer = null;
function showToast(msg, kind = 'info') {
  let box = $('qtToast');
  if (!box) {
    box = document.createElement('div');
    box.id = 'qtToast';
    box.style.cssText = `position:fixed;left:50%;bottom:36px;transform:translateX(-50%);
      padding:11px 20px;border-radius:11px;font-size:13px;font-weight:500;
      box-shadow:0 8px 28px rgba(16,24,40,.16);z-index:9999;transition:opacity .2s;
      pointer-events:none;max-width:80vw;`;
    document.body.appendChild(box);
  }
  const colors = {
    info: ['#111827', '#fff'],
    ok: ['#0f766e', '#fff'],
    warn: ['#92400e', '#fff'],
    error: ['#b91c1c', '#fff'],
  }[kind] || ['#111827', '#fff'];
  box.style.background = colors[0];
  box.style.color = colors[1];
  box.textContent = msg;
  box.style.opacity = '1';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.style.opacity = '0'; }, 2400);
}

// ============================================================
// 顶栏
// ============================================================
function renderMarketChips() {
  const box = $('marketChips');
  box.innerHTML = state.markets.map(m => {
    const on = state.marketsOn.has(m.code);
    return `<span class="chip ${on ? 'on' : ''}" data-mkt="${m.code}"
      title="${esc(m.label)}${m.fundamentals ? '' : '（无财务数据）'}">${esc(m.label)}</span>`;
  }).join('');

  $$('.chip', box).forEach(c => {
    c.addEventListener('click', () => {
      const code = c.dataset.mkt;
      if (state.marketsOn.has(code)) state.marketsOn.delete(code);
      else state.marketsOn.add(code);
      saveLocal('markets', [...state.marketsOn]);
      renderMarketChips();
      document.dispatchEvent(new CustomEvent('markets-changed'));
    });
  });
}

function renderProfileSelect() {
  const sel = $('profileSelect');
  sel.innerHTML = state.profiles.map(p =>
    `<option value="${p.key}" ${p.key === state.profile ? 'selected' : ''}>${esc(p.label)}</option>`
  ).join('');
  sel.addEventListener('change', () => {
    state.profile = sel.value;
    saveLocal('profile', sel.value);
    document.dispatchEvent(new CustomEvent('profile-changed'));
  });
}

function renderAIStatus() {
  const ok = state.channels.length > 0;
  $('aiDot').className = 'dot' + (ok ? '' : ' off');
  $('aiLabel').textContent = ok
    ? `AI 已连接 · ${state.channels[0].name || ''}` : 'AI 未配置';
}

// ============================================================
// 全局搜索（板块 / 个股 / 最近搜索）
// ============================================================
const RECENT_KEY = 'recent_searches';
const getRecent = () => loadLocal(RECENT_KEY, []);

function pushRecent(q) {
  if (!q) return;
  const list = getRecent().filter(x => x !== q);
  list.unshift(q);
  saveLocal(RECENT_KEY, list.slice(0, 10));
}

let suggestItems = [];
let suggestIndex = -1;

async function doSuggest(q) {
  const box = $('searchSuggest');
  if (!q || !q.trim()) { box.classList.add('hidden'); return; }

  q = q.trim();
  const groups = [];

  // 1) 板块匹配（本地）
  const hitSectors = (state.sectors || []).filter(s => s.includes(q) || q.includes(s));
  if (hitSectors.length) {
    groups.push({
      label: '板块',
      items: hitSectors.slice(0, 4).map(s => ({
        type: 'sector', name: s, desc: '查看该板块股票', icon: '◆',
      })),
    });
  }

  // 2) 最近搜索（本地）
  const recent = getRecent().filter(x => x !== q && x.includes(q));
  if (recent.length) {
    groups.push({
      label: '最近搜索',
      items: recent.slice(0, 3).map(x => ({
        type: 'recent', name: x, desc: '', icon: '🕐',
      })),
    });
  }

  // 3) 联网搜标的
  try {
    const mkts = [...state.marketsOn].join(',');
    const d = await api.get('/api/v2/search', { q, markets: mkts, limit: 8 });
    if (d.results?.length) {
      groups.push({
        label: '个股 / 标的',
        items: d.results.slice(0, 8).map(r => ({
          type: 'symbol', name: r.name || r.code,
          code: r.symbol, market: r.market,
        })),
      });
    }
  } catch (_) { /* 联网失败不影响本地结果 */ }

  suggestItems = [];
  suggestIndex = -1;

  if (!groups.length) {
    box.innerHTML = '<div class="suggest-empty">没有找到匹配项</div>';
    box.classList.remove('hidden');
    return;
  }

  let html = '';
  groups.forEach(g => {
    html += `<div class="suggest-group-label">${esc(g.label)}</div>`;
    g.items.forEach(it => {
      const idx = suggestItems.length;
      suggestItems.push(it);
      if (it.type === 'symbol') {
        html += `<div class="suggest-item" data-idx="${idx}">
          <span class="si-name">${esc(it.name)}</span>
          <span class="si-code">${esc(it.code)}</span>
          <span class="tag tag-primary">${esc(it.market)}</span>
        </div>`;
      } else {
        html += `<div class="suggest-item" data-idx="${idx}">
          <span style="width:14px;color:var(--text-3)">${it.icon || ''}</span>
          <span class="si-name">${esc(it.name)}</span>
          <span class="si-desc">${esc(it.desc || '')}</span>
        </div>`;
      }
    });
  });

  box.innerHTML = html;
  box.classList.remove('hidden');

  $$('.suggest-item', box).forEach(node => {
    node.addEventListener('click', () => pickSuggest(suggestItems[+node.dataset.idx]));
  });
}

function pickSuggest(it) {
  $('searchSuggest').classList.add('hidden');
  if (!it) return;
  $('searchInput').value = '';

  if (it.type === 'symbol') {
    pushRecent(it.name);
    navigate('analysis', { symbol: it.code });
  } else if (it.type === 'sector') {
    pushRecent(it.name);
    navigate('screen', { sector: it.name });
  } else if (it.type === 'recent') {
    $('searchInput').value = it.name;
    doSuggest(it.name);
  }
}

function initSearch() {
  const input = $('searchInput');
  const box = $('searchSuggest');
  const debounced = debounce(doSuggest, 280);

  input.addEventListener('input', () => debounced(input.value));
  input.addEventListener('focus', () => { if (input.value.trim()) debounced(input.value); });

  input.addEventListener('keydown', (e) => {
    const items = $$('.suggest-item', box);
    if (e.key === 'ArrowDown' && items.length) {
      e.preventDefault();
      suggestIndex = Math.min(suggestIndex + 1, items.length - 1);
      items.forEach((n, i) => n.classList.toggle('active', i === suggestIndex));
      items[suggestIndex]?.scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'ArrowUp' && items.length) {
      e.preventDefault();
      suggestIndex = Math.max(suggestIndex - 1, 0);
      items.forEach((n, i) => n.classList.toggle('active', i === suggestIndex));
      items[suggestIndex]?.scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (suggestIndex >= 0 && items[suggestIndex]) items[suggestIndex].click();
      else if (items[0]) items[0].click();
      else {
        const q = input.value.trim();
        if (q) { pushRecent(q); navigate('analysis', { symbol: q.toUpperCase() }); }
      }
    } else if (e.key === 'Escape') {
      box.classList.add('hidden');
      input.blur();
    }
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('.search')) box.classList.add('hidden');
  });

  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      input.focus();
      input.select();
    }
  });
}

// ============================================================
// 启动
// ============================================================
async function boot() {
  const savedProfile = loadLocal('profile');
  const savedMarkets = loadLocal('markets');

  const [profRes, mktRes, chRes, setRes, secRes] = await Promise.allSettled([
    api.get('/api/v2/profiles'),
    api.get('/api/v2/markets'),
    api.get('/api/v2/channels'),
    api.get('/api/v2/settings'),
    api.get('/api/v2/sectors'),
  ]);

  if (profRes.status === 'fulfilled') state.profiles = profRes.value.profiles || [];
  if (mktRes.status === 'fulfilled') state.markets = mktRes.value.markets || [];
  if (chRes.status === 'fulfilled') {
    state.channels = (chRes.value.channels || []).filter(c => c.enabled);
  }
  if (setRes.status === 'fulfilled') state.settings = setRes.value.settings || {};
  if (secRes.status === 'fulfilled') state.sectors = secRes.value.sectors || [];

  // 偏好优先级：设置的默认值 > 本地记忆 > 默认
  const prefProfile = state.settings.default_profile || savedProfile;
  if (prefProfile && state.profiles.some(p => p.key === prefProfile)) {
    state.profile = prefProfile;
  }

  const prefMarkets = state.settings.enabled_markets || savedMarkets;
  if (Array.isArray(prefMarkets) && prefMarkets.length) {
    state.marketsOn = new Set(prefMarkets);
  }

  renderMarketChips();
  renderProfileSelect();
  renderAIStatus();
  initSearch();

  $$('.nav-item').forEach(n => {
    n.addEventListener('click', () => navigate(n.dataset.page));
  });

  window.addEventListener('hashchange', renderPage);
  await renderPage();
}

boot();
