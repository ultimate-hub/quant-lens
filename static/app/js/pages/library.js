/* ============================================================
   市场百科（知识库）
   ------------------------------------------------------------
   纯静态内容，断网可用。
   每条都带「对分析的影响」—— 这是最有价值的一栏。
   语言跟随设置：zh / en / both
   ============================================================ */

import { esc, api, state, pageHead, alertBox, debounce } from '../shared.js';

let cache = null;       // {categories, entries, stats}
let activeCat = '';
let keyword = '';

export async function render(root, ctx) {
  activeCat = '';
  keyword = '';

  root.innerHTML = `
    ${pageHead('市场百科', '中国金融市场的规则、机制、产品与数据口径',
      `<span class="fs-12 text-3" id="libCount"></span>`)}
    <div class="card mb-16">
      <div class="card-pad" style="padding:16px 20px">
        <div class="search" style="max-width:none">
          <svg class="search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
          <input type="text" id="libSearch"
                 placeholder="搜索规则或概念，例如：T+1、涨跌停、复权、融资融券…">
        </div>
        <div class="chips mt-12" id="libCats"></div>
      </div>
    </div>
    <div id="libBody"><div class="loading"><div class="spinner"></div><span>加载知识库…</span></div></div>`;

  try {
    cache = await api.get('/api/v2/library');
  } catch (err) {
    root.querySelector('#libBody').innerHTML = alertBox('error', `加载失败：${esc(err.message)}`);
    return;
  }

  renderCats(root, ctx);
  renderList(root, ctx);

  const input = root.querySelector('#libSearch');
  input.addEventListener('input', debounce(async () => {
    keyword = input.value.trim();
    if (!keyword) { renderList(root, ctx); return; }
    try {
      const d = await api.get('/api/v2/library', { q: keyword });
      renderEntries(root, ctx, d.entries, `搜索「${keyword}」`);
    } catch (err) {
      ctx.toast(err.message, 'error');
    }
  }, 320));

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      input.value = ''; keyword = '';
      activeCat = '';
      renderCats(root, ctx);
      renderList(root, ctx);
    }
  });
}

// ============================================================
// 语言
// ============================================================
function lang() {
  return state.settings.library_lang || 'zh';
}

/** 按语言取主文本 */
function T(obj) {
  if (!obj) return '';
  if (lang() === 'en') return obj.en || obj.zh || '';
  return obj.zh || '';
}

/** both 模式下显示英文副行 */
function Tsub(obj) {
  if (lang() !== 'both' || !obj) return '';
  return obj.en || '';
}

// ============================================================
// 分类
// ============================================================
function renderCats(root, ctx) {
  const box = root.querySelector('#libCats');
  const cats = cache.categories || [];

  box.innerHTML = `<span class="chip ${!activeCat ? 'on' : ''}" data-cat="">全部（${cache.stats.total}）</span>` +
    cats.map(c => {
      const n = cache.stats.by_category[c.key] || 0;
      return `<span class="chip ${activeCat === c.key ? 'on' : ''}" data-cat="${c.key}">
        ${esc(c.label[lang()] || c.label.zh)}（${n}）</span>`;
    }).join('');

  box.querySelectorAll('.chip').forEach(chip => {
    chip.addEventListener('click', () => {
      activeCat = chip.dataset.cat;
      keyword = '';
      const inp = root.querySelector('#libSearch');
      if (inp) inp.value = '';
      renderCats(root, ctx);
      renderList(root, ctx);
    });
  });

  const cnt = root.querySelector('#libCount');
  if (cnt) cnt.textContent = `${cache.stats.total} 条 · ${cats.length} 个分类`;
}

// ============================================================
// 列表
// ============================================================
function renderList(root, ctx) {
  const entries = activeCat
    ? (cache.entries || []).filter(e => e.category === activeCat)
    : (cache.entries || []);

  const cat = (cache.categories || []).find(c => c.key === activeCat);
  const title = cat ? (cat.label[lang()] || cat.label.zh) : '全部条目';
  renderEntries(root, ctx, entries, title);
}

function renderEntries(root, ctx, entries, title) {
  const body = root.querySelector('#libBody');

  if (!entries.length) {
    body.innerHTML = `
      <div class="card"><div class="empty">
        <div class="empty-title">没有找到条目</div>
        <div class="empty-desc">换个关键词试试，例如「T+1」「涨跌停」「ETF」</div>
      </div></div>`;
    return;
  }

  const catLabel = (key) => {
    const c = (cache.categories || []).find(x => x.key === key);
    return c ? (c.label[lang()] || c.label.zh) : key;
  };

  body.innerHTML = `
    <div class="fs-12 text-3 mb-12" style="font-weight:600;letter-spacing:.3px">
      ${esc(title)} · ${entries.length} 条
    </div>
    ${entries.map(e => {
      const sub = Tsub(e.title);
      return `
      <div class="card mb-12">
        <div class="card-head" style="align-items:flex-start">
          <div style="min-width:0">
            <div class="card-title" style="font-size:14px">
              ${esc(T(e.title))}
              ${sub ? `<span class="fs-11 text-3" style="font-weight:400;margin-left:8px">
                ${esc(sub)}</span>` : ''}
            </div>
            <div class="fs-11 text-3" style="margin-top:3px">${esc(catLabel(e.category))}</div>
          </div>
          <span class="tag tag-neutral">${esc((e.tags || [])[0] || '')}</span>
        </div>
        <div class="card-pad" style="padding:16px 20px">
          <div class="fs-13" style="line-height:1.8;color:var(--text)">
            ${esc(T(e.summary))}
            ${Tsub(e.summary)
              ? `<div class="fs-12 text-3 mt-8" style="line-height:1.7">${esc(Tsub(e.summary))}</div>`
              : ''}
          </div>
          <div style="margin-top:14px;padding:12px 14px;border-radius:10px;
                      background:var(--primary-soft);border-left:3px solid var(--primary)">
            <div class="fs-11" style="font-weight:700;color:var(--primary);
                 letter-spacing:.3px;margin-bottom:5px">
              ⚠ 对量化分析的影响
            </div>
            <div class="fs-13" style="line-height:1.8;color:#3730a3">
              ${esc(T(e.impact))}
            </div>
            ${Tsub(e.impact)
              ? `<div class="fs-12 mt-8" style="line-height:1.7;color:#6366f1;opacity:.85">
                   ${esc(Tsub(e.impact))}</div>`
              : ''}
          </div>
        </div>
      </div>`;
    }).join('')}`;
}