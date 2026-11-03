/* ============================================================
   我的候选池
   ------------------------------------------------------------
   左侧：池列表（含内置池），可新建/删除
   右侧：池内标的，按行业分组，支持全选/批量移除
   右上：批量添加（粘贴解析 / 按板块挑）
   ============================================================ */

import {
  esc, api, state, navigate, pageHead, emptyBox, alertBox, debounce,
} from '../shared.js';

let currentId = null;
let poolsCache = [];         // 最近一次拉到的池列表（用于判断内置/自建）
let selected = new Set();    // 勾选的 symbol

export async function render(root, ctx) {
  selected = new Set();
  // 支持 #/watchlist?id=2 直接定位到某个池
  if (ctx.params?.id) currentId = Number(ctx.params.id);

  root.innerHTML = `
    ${pageHead('我的候选池', '自定义股票池，按行业自动分组',
      `<button class="btn" id="btnCreatePool">＋ 新建候选池</button>`)}
    <div style="display:grid;grid-template-columns:230px 1fr;gap:16px;align-items:start">
      <div class="card" style="padding:10px">
        <div class="side-list" id="poolList"></div>
      </div>
      <div id="poolMain"></div>
    </div>`;

  root.querySelector('#btnCreatePool').addEventListener('click', () => openCreatePool(root, ctx));
  await loadPools(root, ctx);
}

function isBuiltinCurrent() {
  return poolsCache.some(p => p.id === currentId && p.is_builtin);
}

// ============================================================
// 池列表
// ============================================================
async function loadPools(root, ctx) {
  let data;
  try {
    data = await api.get('/api/v2/watchlists');
  } catch (err) {
    root.querySelector('#poolList').innerHTML =
      `<div class="alert alert-error" style="margin:6px">${esc(err.message)}</div>`;
    return;
  }

  const pools = data.watchlists || [];
  poolsCache = pools;
  const box = root.querySelector('#poolList');

  if (!pools.length) {
    box.innerHTML = '<div class="empty-desc" style="padding:20px">还没有候选池</div>';
    return;
  }

  if (!currentId || !pools.some(p => p.id === currentId)) currentId = pools[0].id;

  box.innerHTML = pools.map(p => `
    <div class="side-item ${p.id === currentId ? 'active' : ''}" data-id="${p.id}">
      <span class="ellipsis">${esc(p.name)}</span>
      <span class="si-count">${p.count}</span>
    </div>`).join('');

  box.querySelectorAll('.side-item').forEach(node => {
    node.addEventListener('click', async () => {
      currentId = Number(node.dataset.id);
      selected.clear();
      await loadPools(root, ctx);
      await loadItems(root, ctx);
    });
  });

  await loadItems(root, ctx);
}

// ============================================================
// 池内容
// ============================================================
async function loadItems(root, ctx) {
  const main = root.querySelector('#poolMain');
  if (!currentId) {
    main.innerHTML = emptyBox('还没有候选池', '点右上角「新建候选池」开始');
    return;
  }

  main.innerHTML = '<div class="loading"><div class="spinner"></div><span>加载中…</span></div>';

  let d;
  try {
    d = await api.get(`/api/v2/watchlists/${currentId}/items`);
  } catch (err) {
    main.innerHTML = alertBox('error', esc(err.message));
    return;
  }
  renderItems(main, root, ctx, d);
}

function poolNameOf(root) {
  let name = '候选池';
  root.querySelectorAll('.side-item').forEach(n => {
    if (Number(n.dataset.id) === currentId) {
      name = n.querySelector('.ellipsis')?.textContent?.trim() || name;
    }
  });
  return name;
}

function renderItems(main, root, ctx, d) {
  const poolName = poolNameOf(root);
  const groups = d.groups || [];

  const head = `
  <div class="card mb-16">
    <div class="card-head">
      <div>
        <div class="card-title">${esc(poolName)}</div>
        <div class="card-sub" style="margin-top:2px">
          共 ${d.count} 只 · ${groups.length} 个行业分组
        </div>
      </div>
      <div class="flex center gap-8">
        ${isBuiltinCurrent() ? '<span class="tag tag-neutral">内置池</span>' : ''}
        <button class="btn ghost sm" id="btnDelPool"
          ${isBuiltinCurrent() ? 'style="display:none"' : ''}>删除此池</button>
        <button class="btn sm" id="btnAdd">＋ 批量添加</button>
      </div>
    </div>`;

  if (!d.count) {
    main.innerHTML = head + `
      <div class="empty">
        <div class="empty-title">这个池还是空的</div>
        <div class="empty-desc">
          点「批量添加」，可以一次粘一串代码，或按板块整组挑
        </div>
        <div class="mt-20"><button class="btn" id="btnAdd2">＋ 批量添加</button></div>
      </div></div>`;
    main.querySelector('#btnAdd')?.addEventListener('click', () => openAddModal(root, ctx));
    main.querySelector('#btnAdd2')?.addEventListener('click', () => openAddModal(root, ctx));
    main.querySelector('#btnDelPool')?.addEventListener('click', () => deletePool(root, ctx, poolName));
    return;
  }

  const body = groups.map(g => {
    const rows = g.items.map(it => `
      <div class="sym-row" data-sym="${esc(it.symbol)}">
        <input type="checkbox" class="cb sym-cb" data-sym="${esc(it.symbol)}">
        <span class="sym-name">${esc(it.name || '—')}</span>
        <span class="sym-code">${esc(it.symbol)}</span>
        <span class="sym-actions">
          <button class="btn ghost sm" data-act="analyze">分析</button>
          <button class="btn danger sm" data-act="remove">移除</button>
        </span>
      </div>`).join('');

    return `
    <div class="group-block">
      <div class="group-head">
        <input type="checkbox" class="cb group-cb" data-industry="${esc(g.industry)}">
        <span>${esc(g.industry)}</span>
        <span class="g-count">${g.items.length} 只</span>
      </div>
      ${rows}
    </div>`;
  }).join('');

  main.innerHTML = head + `
    <div class="card-pad" style="padding:16px 18px">
      <div class="flex between center mb-12" style="flex-wrap:wrap;gap:8px">
        <div class="flex center gap-8 fs-12 text-2">
          <label class="flex center gap-6" style="cursor:pointer">
            <input type="checkbox" class="cb" id="cbAll"> 全选
          </label>
          <span id="selInfo" class="text-3"></span>
        </div>
        <div class="flex center gap-8">
          <button class="btn ghost sm" id="btnBatchAnalyze" disabled>批量量化分析</button>
          <button class="btn danger sm" id="btnBatchRemove" disabled>移除所选</button>
        </div>
      </div>
      ${body}
    </div></div>`;

  bindItemEvents(main, root, ctx);
}

function bindItemEvents(main, root, ctx) {
  const updateSel = () => {
    const n = selected.size;
    const info = main.querySelector('#selInfo');
    if (info) info.textContent = n ? `已选 ${n} 只` : '';
    const br = main.querySelector('#btnBatchRemove');
    const ba = main.querySelector('#btnBatchAnalyze');
    if (br) br.disabled = n === 0;
    if (ba) {
      ba.disabled = n === 0;
      ba.textContent = n ? `批量量化分析（${n} 只）` : '批量量化分析';
    }
  };

  main.querySelectorAll('.sym-cb').forEach(cb => {
    cb.addEventListener('change', () => {
      if (cb.checked) selected.add(cb.dataset.sym);
      else selected.delete(cb.dataset.sym);
      updateSel();
    });
  });

  main.querySelectorAll('.group-cb').forEach(gcb => {
    gcb.addEventListener('change', () => {
      gcb.closest('.group-block').querySelectorAll('.sym-cb').forEach(cb => {
        cb.checked = gcb.checked;
        if (gcb.checked) selected.add(cb.dataset.sym);
        else selected.delete(cb.dataset.sym);
      });
      updateSel();
    });
  });

  const cbAll = main.querySelector('#cbAll');
  cbAll?.addEventListener('change', () => {
    main.querySelectorAll('.sym-cb').forEach(cb => {
      cb.checked = cbAll.checked;
      if (cbAll.checked) selected.add(cb.dataset.sym);
      else selected.delete(cb.dataset.sym);
    });
    main.querySelectorAll('.group-cb').forEach(g => { g.checked = cbAll.checked; });
    updateSel();
  });

  main.querySelectorAll('.sym-row').forEach(row => {
    row.querySelectorAll('[data-act]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const sym = row.dataset.sym;
        if (btn.dataset.act === 'analyze') navigate('analysis', { symbol: sym });
        else await removeSymbols(root, ctx, [sym]);
      });
    });
  });

  main.querySelector('#btnBatchRemove')?.addEventListener('click', async () => {
    if (!selected.size) return;
    if (!ctx.confirm(`确定从候选池移除这 ${selected.size} 只？`)) return;
    await removeSymbols(root, ctx, [...selected]);
  });

  main.querySelector('#btnBatchAnalyze')?.addEventListener('click', async () => {
    const syms = [...selected];
    if (syms.length) await runBatchScreen(root, ctx, syms);
  });

  main.querySelector('#btnAdd')?.addEventListener('click', () => openAddModal(root, ctx));
  main.querySelector('#btnDelPool')?.addEventListener('click',
    () => deletePool(root, ctx, poolNameOf(root)));

  updateSel();
}

// ============================================================
// 批量量化分析
// ============================================================
async function runBatchScreen(root, ctx, symbols) {
  const main = root.querySelector('#poolMain');
  main.innerHTML = `
    <div class="card"><div class="card-pad">
      <div class="loading"><div class="spinner"></div>
        <span>正在评估 ${symbols.length} 只标的…（8 线程并发，约 ${Math.ceil(symbols.length / 8)} 秒）</span>
      </div>
    </div></div>`;

  try {
    const r = await api.post('/api/v2/screen', {
      symbols, profile: state.profile, conditions: {},
    });
    main.innerHTML = renderScreenResult(r, symbols);
    main.querySelector('#btnBack')?.addEventListener('click', () => loadItems(root, ctx));
    main.querySelectorAll('[data-open]').forEach(n => {
      n.addEventListener('click', () => navigate('analysis', { symbol: n.dataset.open }));
    });
  } catch (err) {
    main.innerHTML = alertBox('error', `评估失败：${esc(err.message)}`) +
      `<div class="mt-12"><button class="btn ghost sm" id="btnBack2">返回候选池</button></div>`;
    main.querySelector('#btnBack2')?.addEventListener('click', () => loadItems(root, ctx));
  }
}

function renderScreenResult(r, symbols) {
  const rows = (r.results || []).map(x => {
    const ok = !x.error;
    return `
    <tr>
      <td><b>${esc(x.name || x.symbol)}</b><br>
          <span class="fs-11 text-3">${esc(x.symbol)}</span></td>
      <td class="num">${x.price != null ? Number(x.price).toLocaleString('zh-CN') : '—'}</td>
      <td class="num fw-700">${ok ? (x.composite ?? '—') : '—'}</td>
      <td>${ok && x.grade ? `<span class="tag ${gradeClass(x.grade)}">${esc(x.grade)}</span>` : ''}</td>
      <td class="num fs-12">${x.pe_ttm != null ? Number(x.pe_ttm).toFixed(1) : '—'}</td>
      <td class="num fs-12">${x.roe != null ? Number(x.roe).toFixed(1) + '%' : '—'}</td>
      <td class="num fs-12">${ok && x.dimensions ? dimsText(x.dimensions) : '—'}</td>
      <td>${ok
        ? `<button class="btn ghost sm" data-open="${esc(x.symbol)}">详情</button>`
        : `<span class="fs-11 up">${esc(x.error || '')}</span>`}</td>
    </tr>`;
  }).join('');

  return `
  <div class="card">
    <div class="card-head">
      <div>
        <div class="card-title">批量量化分析结果</div>
        <div class="card-sub" style="margin-top:2px">
          共 ${r.total ?? symbols.length} 只 · 成功评估 ${r.evaluated ?? '—'} 只 · 通过条件 ${r.passed ?? '—'} 只
        </div>
      </div>
      <button class="btn ghost sm" id="btnBack">返回候选池</button>
    </div>
    <div class="table-wrap">
      <table class="table">
        <thead><tr>
          <th>标的</th><th>现价</th><th>综合分</th><th>评级</th>
          <th>PE</th><th>ROE</th><th>六维度</th><th></th>
        </tr></thead>
        <tbody>${rows || '<tr><td colspan="8" class="text-3">无结果</td></tr>'}</tbody>
      </table>
    </div>
  </div>`;
}

/** 评级配色：优秀/良好 绿，一般 灰，偏弱/较差 红 */
function gradeClass(g) {
  if (['优秀', '良好'].includes(g)) return 'tag-down';
  if (['偏弱', '较差'].includes(g)) return 'tag-up';
  return 'tag-neutral';
}

function dimsText(d) {
  const L = { valuation: '估', growth: '长', quality: '质',
              momentum: '动', risk: '险', sentiment: '情' };
  return Object.entries(L).map(([k, v]) => {
    const val = d[k];
    return val === null || val === undefined ? '' : `${v}${Math.round(val)}`;
  }).filter(Boolean).join(' ');
}

// ============================================================
// 删除 / 移除
// ============================================================
async function removeSymbols(root, ctx, symbols) {
  try {
    await api.del(`/api/v2/watchlists/${currentId}/items?symbols=${encodeURIComponent(symbols.join(','))}`);
    symbols.forEach(s => selected.delete(s));
    ctx.toast(`已移除 ${symbols.length} 只`, 'ok');
    await loadPools(root, ctx);
  } catch (err) {
    ctx.toast(`移除失败：${err.message}`, 'error');
  }
}

async function deletePool(root, ctx, name) {
  if (!ctx.confirm(`确定删除候选池「${name}」？池内标的记录会一起删除。`)) return;
  try {
    await api.del(`/api/v2/watchlists/${currentId}`);
    ctx.toast('已删除', 'ok');
    currentId = null;
    selected.clear();
    await loadPools(root, ctx);
  } catch (err) {
    ctx.toast(err.message, 'error');
  }
}

// ============================================================
// 新建池
// ============================================================
function openCreatePool(root, ctx) {
  openModal(`
    <div class="modal-head">
      <div class="modal-title">新建候选池</div>
      <button class="modal-close" data-close>×</button>
    </div>
    <div class="modal-body">
      <div class="field">
        <label>池名称</label>
        <input class="input" id="newPoolName" placeholder="例如：核心持仓 / 白酒观察 / 半导体">
      </div>
      <div class="field">
        <label>备注（可选）</label>
        <input class="input" id="newPoolNote" placeholder="这个池用来做什么">
      </div>
    </div>
    <div class="modal-foot">
      <button class="btn ghost" data-close>取消</button>
      <button class="btn" id="btnOkCreate">创建</button>
    </div>`, (mask) => {
    const input = mask.querySelector('#newPoolName');
    const ok = mask.querySelector('#btnOkCreate');
    input.focus();

    ok.addEventListener('click', async () => {
      const name = input.value.trim();
      if (!name) { ctx.toast('请填写池名称', 'warn'); return; }
      try {
        const r = await api.post('/api/v2/watchlists', {
          name, note: mask.querySelector('#newPoolNote').value.trim(),
        });
        closeModal();
        currentId = r.id;
        selected.clear();
        ctx.toast('已创建', 'ok');
        await loadPools(root, ctx);
      } catch (err) {
        ctx.toast(err.message, 'error');
      }
    });
    input.addEventListener('keydown', e => { if (e.key === 'Enter') ok.click(); });
  });
}

// ============================================================
// 批量添加
// ============================================================
function openAddModal(root, ctx) {
  openModal(`
    <div class="modal-head">
      <div class="modal-title">批量添加标的</div>
      <button class="modal-close" data-close>×</button>
    </div>
    <div class="modal-body">
      <div class="tabs">
        <div class="tab active" data-tab="paste">粘贴代码</div>
        <div class="tab" data-tab="sector">按板块挑</div>
      </div>

      <div id="tabPaste">
        <div class="field">
          <label>把代码粘进来，逗号 / 空格 / 换行混合都行</label>
          <textarea class="textarea" id="rawText" rows="7"
            placeholder="600519.SH, 000858&#10;00700.HK&#10;AAPL.US&#10;也可以直接写中文名：茅台 五粮液"></textarea>
          <div class="hint">支持 600519 / 600519.SH / SH600519 / 中文名，识别不了的会列出来。</div>
        </div>
        <div id="parsePreview"></div>
      </div>

      <div id="tabSector" class="hidden">
        <div class="field">
          <label>选择板块（可多选，会把该板块全部代表个股加进来）</label>
          <div class="sector-grid" id="sectorGrid"></div>
        </div>
        <div class="fs-12 text-3" id="sectorInfo">已选 0 个板块</div>
      </div>
    </div>
    <div class="modal-foot">
      <button class="btn ghost" data-close>取消</button>
      <button class="btn" id="btnDoAdd">添加</button>
    </div>`, (mask) => {
    let mode = 'paste';
    const chosen = new Set();

    mask.querySelectorAll('.tab').forEach(t => {
      t.addEventListener('click', () => {
        mask.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
        t.classList.add('active');
        mode = t.dataset.tab;
        mask.querySelector('#tabPaste').classList.toggle('hidden', mode !== 'paste');
        mask.querySelector('#tabSector').classList.toggle('hidden', mode !== 'sector');
      });
    });

    // 实时解析预览
    const ta = mask.querySelector('#rawText');
    const preview = mask.querySelector('#parsePreview');
    const doParse = debounce(async (text) => {
      if (!text.trim()) { preview.innerHTML = ''; return; }
      try {
        const p = await api.post('/api/v2/watchlists/parse', { text });
        const okChips = p.ok.slice(0, 40).map(x =>
          `<span class="parse-chip ok">${esc(x.symbol)}</span>`).join('');
        const badChips = p.unknown.slice(0, 20).map(x =>
          `<span class="parse-chip bad">${esc(x.raw)}</span>`).join('');
        preview.innerHTML = `
          <div class="parse-preview">
            <div>识别成功 <b>${p.count}</b> 个</div>
            ${okChips ? `<div style="margin-top:6px">${okChips}${p.count > 40 ? ' …' : ''}</div>` : ''}
            ${p.unknown.length ? `
              <div style="margin-top:8px;color:#b91c1c">无法直接识别 ${p.unknown.length} 个（中文名会自动搜索转换）：</div>
              <div style="margin-top:4px">${badChips}</div>` : ''}
          </div>`;
      } catch (err) {
        preview.innerHTML = `<div class="alert alert-error mt-8">${esc(err.message)}</div>`;
      }
    }, 320);
    ta.addEventListener('input', () => doParse(ta.value));

    // 板块
    const grid = mask.querySelector('#sectorGrid');
    const info = mask.querySelector('#sectorInfo');
    grid.innerHTML = (state.sectors || []).map(s =>
      `<div class="sector-chip" data-sector="${esc(s)}">${esc(s)}</div>`).join('');
    grid.querySelectorAll('.sector-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        const s = chip.dataset.sector;
        if (chosen.has(s)) { chosen.delete(s); chip.classList.remove('on'); }
        else { chosen.add(s); chip.classList.add('on'); }
        info.textContent = `已选 ${chosen.size} 个板块`;
      });
    });

    // 提交
    mask.querySelector('#btnDoAdd').addEventListener('click', async () => {
      const btn = mask.querySelector('#btnDoAdd');
      btn.disabled = true; btn.textContent = '添加中…';

      try {
        let added = 0;
        let unresolved = [];

        if (mode === 'paste') {
          const text = ta.value.trim();
          if (!text) { ctx.toast('请先粘贴代码', 'warn'); return; }
          const r = await api.post(`/api/v2/watchlists/${currentId}/items`, { raw_text: text });
          added = r.added || 0;
          unresolved = r.unresolved || [];
        } else {
          if (!chosen.size) { ctx.toast('请至少选一个板块', 'warn'); return; }
          for (const s of chosen) {
            const u = await api.get('/api/v2/universe', { sector: s });
            const r = await api.post(`/api/v2/watchlists/${currentId}/items`,
              { symbols: (u.symbols || []).map(x => x.symbol), industry: s });
            added += r.added || 0;
          }
        }

        closeModal();
        ctx.toast(`已添加 ${added} 只`, 'ok');
        if (unresolved.length) setTimeout(() => ctx.toast(`${unresolved.length} 个未能识别`, 'warn'), 1300);
        await loadPools(root, ctx);
      } catch (err) {
        ctx.toast(`添加失败：${err.message}`, 'error');
        btn.disabled = false; btn.textContent = '添加';
      }
    });

    ta.focus();
  });
}

// ============================================================
// 弹窗工具
// ============================================================
let modalEl = null;

function openModal(html, onMount) {
  closeModal();
  const mask = document.createElement('div');
  mask.className = 'modal-mask';
  mask.innerHTML = `<div class="modal">${html}</div>`;
  document.body.appendChild(mask);
  modalEl = mask;

  mask.addEventListener('click', (e) => {
    if (e.target === mask || e.target.hasAttribute('data-close')) closeModal();
  });
  document.addEventListener('keydown', escClose);
  onMount?.(mask);
}

function closeModal() {
  if (modalEl) { modalEl.remove(); modalEl = null; }
  document.removeEventListener('keydown', escClose);
}

function escClose(e) { if (e.key === 'Escape') closeModal(); }