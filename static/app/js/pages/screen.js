/* ============================================================
   选股筛选
   ------------------------------------------------------------
   数据来源：内置全部板块 / 任意候选池
   条件：估值、成长、质量、动量、风险、综合分
   ============================================================ */

import {
  esc, api, state, navigate, pageHead, alertBox, saveLocal, loadLocal,
} from '../shared.js';

let pools = [];
let currentPool = '';        // '' = 内置全部板块
let sortKey = 'composite';
let sortDesc = true;

const CONDITION_DEFS = [
  { key: 'pe_max', label: 'PE 上限', placeholder: '25', step: '0.1' },
  { key: 'pb_max', label: 'PB 上限', placeholder: '8', step: '0.1' },
  { key: 'roe_min', label: 'ROE 下限 %', placeholder: '10', step: '0.1' },
  { key: 'revenue_yoy_min', label: '营收增速下限 %', placeholder: '0', step: '0.1' },
  { key: 'debt_ratio_max', label: '负债率上限 %', placeholder: '70', step: '0.1' },
  { key: 'market_cap_min', label: '市值下限 亿', placeholder: '100', step: '1' },
  { key: 'mom_3m_min', label: '3月动量下限 %', placeholder: '0', step: '0.1' },
  { key: 'max_drawdown_max', label: '回撤上限 %', placeholder: '-40', step: '0.1' },
  { key: 'sharpe_min', label: '夏普下限', placeholder: '0.5', step: '0.01' },
  { key: 'score_min', label: '综合分下限', placeholder: '60', step: '0.1' },
];

export async function render(root, ctx) {
  sortKey = 'composite';
  sortDesc = true;

  const savedCond = loadLocal('screen_conditions', {});

  root.innerHTML = `
    ${pageHead('选股筛选', '从候选池里按条件批量筛，多线程并发',
      `<button class="btn" id="btnRun">开始筛选</button>`)}

    <div class="card mb-16">
      <div class="card-pad">
        <div class="grid-3 mb-16">
          <div class="field" style="margin-bottom:0">
            <label>数据来源</label>
            <select class="select" id="selPool">
              <option value="">内置 · 全部板块（约 91 只）</option>
            </select>
            <div class="hint" id="poolHint">选候选池可以只筛自己的股票</div>
          </div>
          <div class="field" style="margin-bottom:0">
            <label>分析模式</label>
            <select class="select" id="selProfile">
              ${state.profiles.map(p => `
                <option value="${p.key}" ${p.key === state.profile ? 'selected' : ''}>
                  ${esc(p.label)}
                </option>`).join('')}
            </select>
            <div class="hint">模式决定六维度权重</div>
          </div>
          <div class="field" style="margin-bottom:0">
            <label>筛选并发</label>
            <input class="input" type="number" min="1" max="16" id="selWorkers"
                   value="${state.settings.screen_concurrency ?? 8}">
            <div class="hint">太高易触发限流</div>
          </div>
        </div>

        <div class="fs-12 text-3 mb-8" style="font-weight:600;letter-spacing:.3px">
          筛选条件（留空表示不限制）
        </div>
        <div class="grid-3">
          ${CONDITION_DEFS.map(c => `
            <div class="field" style="margin-bottom:10px">
              <label>${esc(c.label)}</label>
              <input class="input cond" data-key="${c.key}" type="number" step="${c.step}"
                     placeholder="${c.placeholder}" value="${esc(savedCond[c.key] ?? '')}">
            </div>`).join('')}
        </div>

        <div class="flex center gap-8 mt-12" style="flex-wrap:wrap">
          <button class="btn ghost sm" id="btnClearCond">清空条件</button>
          <button class="btn ghost sm" id="btnPresetValue">价值型预设</button>
          <button class="btn ghost sm" id="btnPresetQuality">质量型预设</button>
          <span class="fs-11 text-3">预设只是填入常用阈值，可再改</span>
        </div>
      </div>
    </div>

    <div id="screenResult">${emptyHint()}</div>`;

  await loadPools(root);

  root.querySelector('#btnRun').addEventListener('click', () => runScreen(root, ctx));
  root.querySelector('#btnClearCond').addEventListener('click', () => {
    root.querySelectorAll('.cond').forEach(i => { i.value = ''; });
    saveLocal('screen_conditions', {});
  });
  root.querySelector('#btnPresetValue').addEventListener('click', () => {
    applyPreset(root, { pe_max: 20, pb_max: 3, roe_min: 12, debt_ratio_max: 60 });
  });
  root.querySelector('#btnPresetQuality').addEventListener('click', () => {
    applyPreset(root, { roe_min: 15, debt_ratio_max: 50, score_min: 60 });
  });

  root.querySelectorAll('.cond').forEach(inp => {
    inp.addEventListener('change', () => saveLocal('screen_conditions', collectConditions(root)));
  });

  root.querySelector('#selProfile').addEventListener('change', (e) => {
    state.profile = e.target.value;
    saveLocal('profile', e.target.value);
    document.dispatchEvent(new CustomEvent('profile-changed'));
  });
}

function emptyHint() {
  return `
  <div class="card"><div class="empty">
    <div class="empty-title">设置条件后点「开始筛选」</div>
    <div class="empty-desc">
      条件全部留空 = 全部评估一遍再按综合分排序。<br>
      91 只标的约需 13 秒。
    </div>
  </div></div>`;
}

async function loadPools(root) {
  try {
    const d = await api.get('/api/v2/watchlists');
    pools = d.watchlists || [];
  } catch (_) { pools = []; }

  const sel = root.querySelector('#selPool');
  const defaultPool = state.settings.default_watchlist;

  sel.innerHTML = '<option value="">内置 · 全部板块（约 91 只）</option>' +
    pools.filter(p => !p.is_builtin).map(p =>
      `<option value="${p.id}">${esc(p.name)}（${p.count} 只）</option>`).join('');

  if (defaultPool && pools.some(p => String(p.id) === String(defaultPool))) {
    sel.value = String(defaultPool);
    currentPool = String(defaultPool);
  }

  sel.addEventListener('change', () => {
    currentPool = sel.value;
    const hint = root.querySelector('#poolHint');
    if (currentPool) {
      const p = pools.find(x => String(x.id) === currentPool);
      hint.textContent = p ? `将筛选「${p.name}」里的 ${p.count} 只` : '';
    } else {
      hint.textContent = '选候选池可以只筛自己的股票';
    }
  });
}

function applyPreset(root, preset) {
  root.querySelectorAll('.cond').forEach(inp => {
    const v = preset[inp.dataset.key];
    inp.value = v === undefined ? '' : v;
  });
  saveLocal('screen_conditions', collectConditions(root));
}

function collectConditions(root) {
  const out = {};
  root.querySelectorAll('.cond').forEach(inp => {
    const v = inp.value.trim();
    if (v === '') return;
    const n = Number(v);
    if (!Number.isFinite(n)) return;

    // 界面用百分数，后端要小数
    if (inp.dataset.key === 'mom_3m_min') out.mom_3m_min = n / 100;
    else if (inp.dataset.key === 'max_drawdown_max') out.max_drawdown_max = n / 100;
    else out[inp.dataset.key] = n;
  });
  return out;
}

// ============================================================
// 执行筛选
// ============================================================
async function runScreen(root, ctx) {
  const btn = root.querySelector('#btnRun');
  const box = root.querySelector('#screenResult');

  btn.disabled = true;
  btn.textContent = '筛选中…';

  const conditions = collectConditions(root);
  conditions.markets = [...state.marketsOn];
  const workers = Number(root.querySelector('#selWorkers').value) || 8;

  let symbols = [];
  try {
    if (currentPool) {
      const d = await api.get(`/api/v2/watchlists/${currentPool}/items`);
      symbols = (d.items || []).map(x => x.symbol);
    } else {
      const u = await api.get('/api/v2/universe');
      symbols = (u.symbols || []).map(x => x.symbol);
    }
  } catch (err) {
    box.innerHTML = alertBox('error', `取标的失败：${esc(err.message)}`);
    btn.disabled = false; btn.textContent = '开始筛选';
    return;
  }

  if (!symbols.length) {
    box.innerHTML = alertBox('warn', '这个池是空的，先往里面加标的。');
    btn.disabled = false; btn.textContent = '开始筛选';
    return;
  }

  const est = Math.max(2, Math.ceil(symbols.length / Math.min(workers, 8)));
  box.innerHTML = `<div class="card"><div class="card-pad">
    <div class="loading"><div class="spinner"></div>
      <span>正在评估 ${symbols.length} 只标的…（并发 ${workers}，预计 ${est} 秒）</span></div>
  </div></div>`;

  try {
    const r = await api.post('/api/v2/screen', {
      symbols,
      profile: root.querySelector('#selProfile').value,
      conditions,
    });
    renderResult(root, ctx, r);
  } catch (err) {
    box.innerHTML = alertBox('error', `筛选失败：${esc(err.message)}`);
  }

  btn.disabled = false;
  btn.textContent = '开始筛选';
}

function renderResult(root, ctx, r) {
  const box = root.querySelector('#screenResult');
  const rows = [...(r.results || [])]
    .filter(x => !x.error)
    .sort((a, b) => {
      const av = a[sortKey] ?? -Infinity;
      const bv = b[sortKey] ?? -Infinity;
      if (typeof av === 'string' || typeof bv === 'string') {
        return sortDesc ? String(bv).localeCompare(String(av)) : String(av).localeCompare(String(bv));
      }
      return sortDesc ? bv - av : av - bv;
    });

  const passed = r.passed ?? rows.length;
  const errs = (r.errors || []).length;

  box.innerHTML = `
  <div class="card">
    <div class="card-head">
      <div>
        <div class="card-title">筛选结果</div>
        <div class="card-sub" style="margin-top:2px">
          共 ${r.total ?? rows.length} 只 · 成功评估 ${r.evaluated ?? '—'} 只 ·
          通过条件 ${passed} 只${errs ? ` · ${errs} 只抓取失败` : ''}
        </div>
      </div>
      <div class="flex center gap-8">
        <button class="btn ghost sm" id="btnExport">复制结果</button>
        <button class="btn ghost sm" id="btnAddToPool">加入候选池</button>
      </div>
    </div>

    ${rows.length ? `
    <div class="table-wrap">
      <table class="table">
        <thead><tr>
          <th style="cursor:pointer" data-sort="name">标的</th>
          <th style="cursor:pointer" data-sort="price">现价</th>
          <th style="cursor:pointer" data-sort="composite">综合分 ${sortIcon('composite')}</th>
          <th>评级</th>
          <th style="cursor:pointer" data-sort="pe_ttm">PE</th>
          <th style="cursor:pointer" data-sort="roe">ROE</th>
          <th style="cursor:pointer" data-sort="mom_3m">3月动量</th>
          <th style="cursor:pointer" data-sort="max_drawdown">回撤</th>
          <th></th>
        </tr></thead>
        <tbody>
          ${rows.map(x => `
            <tr>
              <td>
                <b>${esc(x.name || x.symbol)}</b>
                <div class="fs-11 text-3">${esc(x.symbol)}</div>
              </td>
              <td class="num">${x.price != null ? Number(x.price).toLocaleString('zh-CN') : '—'}</td>
              <td class="num fw-700">${x.composite ?? '—'}</td>
              <td><span class="tag ${gradeClass(x.grade)}">${esc(x.grade || '')}</span></td>
              <td class="num fs-12">${x.pe_ttm != null ? Number(x.pe_ttm).toFixed(1) : '—'}</td>
              <td class="num fs-12">${x.roe != null ? Number(x.roe).toFixed(1) + '%' : '—'}</td>
              <td class="num fs-12 ${x.mom_3m > 0 ? 'up' : x.mom_3m < 0 ? 'down' : ''}">
                ${x.mom_3m != null ? (x.mom_3m * 100).toFixed(1) + '%' : '—'}
              </td>
              <td class="num fs-12 down">
                ${x.max_drawdown != null ? (x.max_drawdown * 100).toFixed(1) + '%' : '—'}
              </td>
              <td><button class="btn ghost sm" data-open="${esc(x.symbol)}">详情</button></td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>` : `
    <div class="empty">
      <div class="empty-title">没有标的通过筛选</div>
      <div class="empty-desc">放宽条件试试，例如 PE 上限调高、ROE 下限调低</div>
    </div>`}
  </div>`;

  box.querySelectorAll('[data-open]').forEach(n => {
    n.addEventListener('click', () => navigate('analysis', { symbol: n.dataset.open }));
  });

  box.querySelectorAll('[data-sort]').forEach(th => {
    th.addEventListener('click', () => {
      const k = th.dataset.sort;
      if (sortKey === k) sortDesc = !sortDesc;
      else { sortKey = k; sortDesc = true; }
      renderResult(root, ctx, r);
    });
  });

  box.querySelector('#btnExport')?.addEventListener('click', () => {
    const text = rows.map(x =>
      `${x.symbol}\t${x.name || ''}\t${x.composite ?? ''}\t${x.grade || ''}`).join('\n');
    navigator.clipboard?.writeText(text);
    ctx.toast(`已复制 ${rows.length} 行`, 'ok');
  });

  box.querySelector('#btnAddToPool')?.addEventListener('click', () => {
    openAddToPool(root, ctx, rows.map(x => x.symbol));
  });
}

function sortIcon(key) {
  if (sortKey !== key) return '';
  return sortDesc ? '↓' : '↑';
}

function gradeClass(g) {
  if (['优秀', '良好'].includes(g)) return 'tag-down';
  if (['偏弱', '较差'].includes(g)) return 'tag-up';
  return 'tag-neutral';
}

// ============================================================
// 加入候选池
// ============================================================
function openAddToPool(root, ctx, symbols) {
  if (!pools.length) { ctx.toast('还没有候选池，先去「我的候选池」新建一个', 'warn'); return; }

  const options = pools.map(p =>
    `<option value="${p.id}">${esc(p.name)}（${p.count} 只）</option>`).join('');

  const mask = document.createElement('div');
  mask.className = 'modal-mask';
  mask.innerHTML = `
    <div class="modal" style="max-width:460px">
      <div class="modal-head">
        <div class="modal-title">把 ${symbols.length} 只加入候选池</div>
        <button class="modal-close" data-close>×</button>
      </div>
      <div class="modal-body">
        <div class="field">
          <label>选择候选池</label>
          <select class="select" id="targetPool">${options}</select>
        </div>
        <div class="fs-12 text-3 mt-8">或者新建一个（填了就用新池）：</div>
        <input class="input mt-8" id="newPoolName" placeholder="新池名称">
      </div>
      <div class="modal-foot">
        <button class="btn ghost" data-close>取消</button>
        <button class="btn" id="btnConfirmAdd">加入</button>
      </div>
    </div>`;
  document.body.appendChild(mask);

  const close = () => mask.remove();
  mask.addEventListener('click', e => {
    if (e.target === mask || e.target.hasAttribute('data-close')) close();
  });

  mask.querySelector('#btnConfirmAdd').addEventListener('click', async () => {
    const btn = mask.querySelector('#btnConfirmAdd');
    btn.disabled = true; btn.textContent = '加入中…';
    try {
      let poolId = mask.querySelector('#targetPool').value;
      const newName = mask.querySelector('#newPoolName').value.trim();
      if (newName) {
        const r = await api.post('/api/v2/watchlists', { name: newName, note: '' });
        poolId = r.id;
      }
      if (!poolId) {
        ctx.toast('请选择或新建一个池', 'warn');
        btn.disabled = false; btn.textContent = '加入';
        return;
      }
      const r = await api.post(`/api/v2/watchlists/${poolId}/items`, { symbols });
      close();
      ctx.toast(`已加入 ${r.added} 只`, 'ok');
    } catch (err) {
      ctx.toast(err.message, 'error');
      btn.disabled = false; btn.textContent = '加入';
    }
  });
}