/* ============================================================
   概念库管理
   ------------------------------------------------------------
   左侧：行业 / 概念 两栏，可搜索
   右侧：选中概念的成分股、别名、备注；可编辑
   用户自定义的概念可以改名、改类型、删除；内置的只能改成分股
   ============================================================ */

import {
  esc, api, state, navigate, pageHead, alertBox, debounce,
} from '../shared.js';

let currentId = null;
let current = null;      // 当前概念的完整数据
let filterKind = '';     // '' | 'industry' | 'concept'
let keyword = '';

export async function render(root, ctx) {
  currentId = ctx.params?.id ? Number(ctx.params.id) : null;
  filterKind = ctx.params?.kind || '';
  keyword = '';

  root.innerHTML = `
    ${pageHead('概念库', '行业与概念的分类体系，支持自定义',
      `<button class="btn" id="btnNewConcept">＋ 新建概念</button>
       <span class="fs-12 text-3" id="cptStats"></span>`)}

    <div class="card mb-16">
      <div class="card-pad" style="padding:14px 18px">
        <div class="flex center gap-8" style="flex-wrap:wrap">
          <div class="search" style="flex:1;min-width:220px;max-width:none">
            <svg class="search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
            <input type="text" id="cptSearch" placeholder="搜索概念名或别名…">
          </div>
          <div class="chips" id="cptKinds"></div>
        </div>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:260px 1fr;gap:16px;align-items:start">
      <div class="card" style="padding:10px;max-height:70vh;overflow-y:auto">
        <div class="side-list" id="cptList"></div>
      </div>
      <div id="cptMain"></div>
    </div>`;

  root.querySelector('#btnNewConcept').addEventListener('click', () => openNewConcept(root, ctx));
  root.querySelector('#cptSearch').addEventListener('input', debounce(() => {
    keyword = root.querySelector('#cptSearch').value.trim();
    loadList(root, ctx);
  }, 280));

  renderKinds(root, ctx);
  await loadList(root, ctx);
}

function renderKinds(root, ctx) {
  const box = root.querySelector('#cptKinds');
  const kinds = [
    ['', '全部'],
    ['industry', '行业'],
    ['concept', '概念'],
  ];
  box.innerHTML = kinds.map(([k, label]) =>
    `<span class="chip ${filterKind === k ? 'on' : ''}" data-kind="${k}">${label}</span>`).join('');

  box.querySelectorAll('.chip').forEach(c => {
    c.addEventListener('click', () => {
      filterKind = c.dataset.kind;
      renderKinds(root, ctx);
      loadList(root, ctx);
    });
  });
}

async function loadList(root, ctx) {
  let d;
  try {
    d = await api.get('/api/v2/concepts', {
      kind: filterKind || undefined,
      q: keyword || undefined,
    });
  } catch (err) {
    root.querySelector('#cptList').innerHTML =
      `<div class="alert alert-error" style="margin:6px">${esc(err.message)}</div>`;
    return;
  }

  const stat = root.querySelector('#cptStats');
  if (stat) {
    stat.textContent = `${d.stats.total} 个 · 行业 ${d.stats.industries} · `
      + `概念 ${d.stats.concepts} · 自定义 ${d.stats.custom} · 覆盖 ${d.stats.stocks} 只股`;
  }

  const list = d.concepts || [];
  const box = root.querySelector('#cptList');

  if (!list.length) {
    box.innerHTML = '<div class="empty-desc" style="padding:20px">没有匹配的概念</div>';
    return;
  }

  // 按类型分组渲染
  const groups = {};
  list.forEach(c => {
    (groups[c.kind_label] = groups[c.kind_label] || []).push(c);
  });

  box.innerHTML = Object.entries(groups).map(([label, arr]) => `
    <div class="fs-11 text-3" style="padding:8px 10px 4px;font-weight:600;letter-spacing:.4px">
      ${esc(label)}（${arr.length}）
    </div>
    ${arr.map(c => `
      <div class="side-item ${c.id === currentId ? 'active' : ''}" data-id="${c.id}">
        <span class="ellipsis">${esc(c.name)}</span>
        ${c.is_builtin ? '' : '<span class="tag tag-warn" style="font-size:9.5px;padding:0 4px">自建</span>'}
        <span class="si-count">${c.count}</span>
      </div>`).join('')}`).join('');

  box.querySelectorAll('.side-item').forEach(n => {
    n.addEventListener('click', async () => {
      currentId = Number(n.dataset.id);
      await loadList(root, ctx);
      await loadDetail(root, ctx);
    });
  });

  if (!currentId || !list.some(c => c.id === currentId)) {
    currentId = list[0].id;
  }
  await loadDetail(root, ctx);
}

async function loadDetail(root, ctx) {
  const main = root.querySelector('#cptMain');
  if (!currentId) {
    main.innerHTML = '<div class="card"><div class="empty">' +
      '<div class="empty-title">选一个概念</div></div></div>';
    return;
  }

  let d;
  try {
    d = await api.get(`/api/v2/concepts/${currentId}`);
  } catch (err) {
    main.innerHTML = alertBox('error', esc(err.message));
    return;
  }
  current = d.concept;
  renderDetail(root, ctx);
}

function renderDetail(root, ctx) {
  const c = current;
  const main = root.querySelector('#cptMain');

  const rows = (c.items || []).map(it => `
    <div class="sym-row" data-sym="${esc(it.symbol)}">
      <span class="sym-name">${esc(it.name || '—')}</span>
      <span class="sym-code">${esc(it.symbol)}</span>
      <span class="sym-actions">
        <button class="btn ghost sm" data-act="analyze">分析</button>
        <button class="btn danger sm" data-act="remove">移除</button>
      </span>
    </div>`).join('');

  const aliases = (c.aliases || []).map(a => `
    <span class="chip" data-alias="${esc(a)}" style="cursor:pointer" title="点击删除">
      ${esc(a)} <span style="color:var(--text-3);margin-left:3px">×</span>
    </span>`).join('') || '<span class="fs-12 text-3">还没有别名，加几个能提高新闻命中率</span>';

  main.innerHTML = `
  <div class="card mb-16">
    <div class="card-head">
      <div>
        <div class="card-title" style="font-size:15px">
          ${esc(c.name)}
          <span class="tag ${c.kind === 'industry' ? 'tag-primary' : 'tag-down'}"
                style="margin-left:8px">${esc(c.kind_label)}</span>
          ${c.is_builtin ? '<span class="tag tag-neutral" style="margin-left:6px">内置</span>'
                         : '<span class="tag tag-warn" style="margin-left:6px">自建</span>'}
        </div>
        <div class="card-sub" style="margin-top:3px">
          ${c.count} 只成分股 · ${(c.aliases || []).length} 个别名
        </div>
      </div>
      <div class="flex center gap-8">
        <button class="btn ghost sm" id="btnEdit">编辑</button>
        ${c.is_builtin ? '' : '<button class="btn danger sm" id="btnDel">删除</button>'}
      </div>
    </div>

    ${c.note ? `<div class="card-pad" style="padding:12px 20px;border-bottom:1px solid var(--border)">
      <span class="fs-12 text-2">${esc(c.note)}</span></div>` : ''}

    <div class="card-pad">
      <div class="flex between center mb-12" style="flex-wrap:wrap;gap:8px">
        <div class="fs-11 text-3" style="font-weight:600;letter-spacing:.3px">成分股</div>
        <button class="btn sm" id="btnAddStock">＋ 添加股票</button>
      </div>
      ${rows ? `<div style="border:1px solid var(--border);border-radius:11px;overflow:hidden">
        ${rows}</div>` : '<div class="fs-12 text-3" style="padding:16px">还没有成分股</div>'}

      <div class="mt-16">
        <div class="flex between center mb-8">
          <div class="fs-11 text-3" style="font-weight:600;letter-spacing:.3px">
            别名（新闻关键词匹配用）
          </div>
          <button class="btn ghost sm" id="btnAddAlias">＋ 加别名</button>
        </div>
        <div class="chips" id="aliasBox">${aliases}</div>
      </div>
    </div>
  </div>`;

  bindDetail(root, ctx);
}

function bindDetail(root, ctx) {
  const main = root.querySelector('#cptMain');

  main.querySelectorAll('.sym-row').forEach(row => {
    row.querySelectorAll('[data-act]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const sym = row.dataset.sym;
        if (btn.dataset.act === 'analyze') { navigate('analysis', { symbol: sym }); return; }
        try {
          await api.del(`/api/v2/concepts/${currentId}/items?symbols=${encodeURIComponent(sym)}`);
          ctx.toast('已移除', 'ok');
          await refresh(root, ctx);
        } catch (err) { ctx.toast(err.message, 'error'); }
      });
    });
  });

  main.querySelectorAll('[data-alias]').forEach(chip => {
    chip.addEventListener('click', async () => {
      try {
        await api.del(`/api/v2/concepts/${currentId}/aliases?alias=${encodeURIComponent(chip.dataset.alias)}`);
        ctx.toast('已删除别名', 'ok');
        await refresh(root, ctx);
      } catch (err) { ctx.toast(err.message, 'error'); }
    });
  });

  main.querySelector('#btnAddStock')?.addEventListener('click', () => openAddStock(root, ctx));
  main.querySelector('#btnAddAlias')?.addEventListener('click', () => openAddAlias(root, ctx));
  main.querySelector('#btnEdit')?.addEventListener('click', () => openEdit(root, ctx));
  main.querySelector('#btnDel')?.addEventListener('click', async () => {
    if (!ctx.confirm(`确定删除概念「${current.name}」？成分股记录会一起删除。`)) return;
    try {
      await api.del(`/api/v2/concepts/${currentId}`);
      ctx.toast('已删除', 'ok');
      currentId = null;
      await loadList(root, ctx);
    } catch (err) { ctx.toast(err.message, 'error'); }
  });
}

async function refresh(root, ctx) {
  await loadList(root, ctx);
}

// ============================================================
// 弹窗
// ============================================================
let modalEl = null;

function openModal(html, onMount) {
  closeModal();
  const mask = document.createElement('div');
  mask.className = 'modal-mask';
  mask.innerHTML = `<div class="modal" style="max-width:520px">${html}</div>`;
  document.body.appendChild(mask);
  modalEl = mask;
  mask.addEventListener('click', e => {
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

// ============================================================
// 新建 / 编辑
// ============================================================
function openNewConcept(root, ctx) {
  openModal(`
    <div class="modal-head">
      <div class="modal-title">新建概念</div>
      <button class="modal-close" data-close>×</button>
    </div>
    <div class="modal-body">
      <div class="field">
        <label>名称</label>
        <input class="input" id="ncName" placeholder="例如：固态电池 / 低空经济">
      </div>
      <div class="field">
        <label>类型</label>
        <select class="select" id="ncKind">
          <option value="concept">概念（跨行业的主题）</option>
          <option value="industry">行业（稳定的产业分类）</option>
        </select>
      </div>
      <div class="field">
        <label>别名（逗号分隔，可选）</label>
        <input class="input" id="ncAlias" placeholder="固态电池, 半固态, 全固态">
      </div>
      <div class="field">
        <label>成分股（可粘贴，也可先建空概念）</label>
        <textarea class="textarea" id="ncStocks" rows="4"
          placeholder="300750.SZ, 002074.SZ&#10;也可以写中文名：宁德时代"></textarea>
      </div>
      <div class="field">
        <label>备注（可选）</label>
        <input class="input" id="ncNote" placeholder="这个概念的判断依据">
      </div>
    </div>
    <div class="modal-foot">
      <button class="btn ghost" data-close>取消</button>
      <button class="btn" id="ncOk">创建</button>
    </div>`, (mask) => {
    mask.querySelector('#ncName').focus();
    mask.querySelector('#ncOk').addEventListener('click', async (btn) => {
      const name = mask.querySelector('#ncName').value.trim();
      if (!name) { ctx.toast('请填名称', 'warn'); return; }

      const aliases = mask.querySelector('#ncAlias').value
        .split(/[,，、\s]+/).map(s => s.trim()).filter(Boolean);
      const raw = mask.querySelector('#ncStocks').value.trim();

      let symbols = [];
      if (raw) {
        // 复用候选池的解析器，中文名也能转代码
        const f = await (await fetch('/api/v2/watchlists/parse', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: raw }),
        })).json();
        symbols = (f.ok || []).map(x => x.symbol);
      }

      try {
        const r = await api.post('/api/v2/concepts', {
          name, kind: mask.querySelector('#ncKind').value,
          note: mask.querySelector('#ncNote').value.trim(),
          aliases, symbols,
        });
        closeModal();
        ctx.toast(`已创建，加入 ${symbols.length} 只`, 'ok');
        currentId = r.id;
        await loadList(root, ctx);
      } catch (err) { ctx.toast(err.message, 'error'); }
    });
  });
}

function openEdit(root, ctx) {
  const c = current;
  openModal(`
    <div class="modal-head">
      <div class="modal-title">编辑「${esc(c.name)}」</div>
      <button class="modal-close" data-close>×</button>
    </div>
    <div class="modal-body">
      <div class="field">
        <label>名称</label>
        <input class="input" id="edName" value="${esc(c.name)}">
      </div>
      <div class="field">
        <label>类型</label>
        <select class="select" id="edKind">
          <option value="concept" ${c.kind === 'concept' ? 'selected' : ''}>概念</option>
          <option value="industry" ${c.kind === 'industry' ? 'selected' : ''}>行业</option>
        </select>
      </div>
      <div class="field">
        <label>备注</label>
        <textarea class="textarea" id="edNote" rows="3">${esc(c.note || '')}</textarea>
      </div>
    </div>
    <div class="modal-foot">
      <button class="btn ghost" data-close>取消</button>
      <button class="btn" id="edOk">保存</button>
    </div>`, (mask) => {
    mask.querySelector('#edOk').addEventListener('click', async () => {
      try {
        await api.put(`/api/v2/concepts/${currentId}`, {
          name: mask.querySelector('#edName').value.trim(),
          kind: mask.querySelector('#edKind').value,
          note: mask.querySelector('#edNote').value.trim(),
        });
        closeModal();
        ctx.toast('已保存', 'ok');
        await refresh(root, ctx);
      } catch (err) { ctx.toast(err.message, 'error'); }
    });
  });
}

// ============================================================
// 加股票 / 加别名
// ============================================================
function openAddStock(root, ctx) {
  openModal(`
    <div class="modal-head">
      <div class="modal-title">给「${esc(current.name)}」加成分股</div>
      <button class="modal-close" data-close>×</button>
    </div>
    <div class="modal-body">
      <div class="field">
        <label>粘贴代码（逗号/空格/换行都行，中文名也认）</label>
        <textarea class="textarea" id="asText" rows="6"
          placeholder="300750.SZ, 002074.SZ&#10;宁德时代"></textarea>
      </div>
    </div>
    <div class="modal-foot">
      <button class="btn ghost" data-close>取消</button>
      <button class="btn" id="asOk">添加</button>
    </div>`, (mask) => {
    mask.querySelector('#asText').focus();
    mask.querySelector('#asOk').addEventListener('click', async () => {
      const raw = mask.querySelector('#asText').value.trim();
      if (!raw) { ctx.toast('先粘代码', 'warn'); return; }

      const f = await (await fetch('/api/v2/watchlists/parse', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: raw }),
      })).json();

      const symbols = (f.ok || []).map(x => x.symbol);

      // 中文名走搜索
      for (const u of (f.unknown || [])) {
        if (u.reason !== 'name') continue;
        try {
          const s = await api.get('/api/v2/search', {
            q: u.name, markets: [...state.marketsOn].join(','), limit: 1,
          });
          if (s.results?.length) symbols.push(s.results[0].symbol);
        } catch (_) { /* 忽略 */ }
      }

      if (!symbols.length) { ctx.toast('没识别出代码', 'warn'); return; }

      try {
        const r = await api.post(`/api/v2/concepts/${currentId}/items`, { symbols });
        closeModal();
        ctx.toast(`已添加 ${r.added} 只`, 'ok');
        await refresh(root, ctx);
      } catch (err) { ctx.toast(err.message, 'error'); }
    });
  });
}

function openAddAlias(root, ctx) {
  openModal(`
    <div class="modal-head">
      <div class="modal-title">给「${esc(current.name)}」加别名</div>
      <button class="modal-close" data-close>×</button>
    </div>
    <div class="modal-body">
      <div class="field">
        <label>别名（逗号或空格分隔，可一次加多个）</label>
        <input class="input" id="alText" placeholder="算力租赁, 智算中心">
        <div class="hint">新闻标题里出现这些词，就会被归到这个概念下</div>
      </div>
    </div>
    <div class="modal-foot">
      <button class="btn ghost" data-close>取消</button>
      <button class="btn" id="alOk">添加</button>
    </div>`, (mask) => {
    mask.querySelector('#alText').focus();
    mask.querySelector('#alOk').addEventListener('click', async () => {
      const list = mask.querySelector('#alText').value
        .split(/[,，、\s]+/).map(s => s.trim()).filter(Boolean);
      if (!list.length) { ctx.toast('先填别名', 'warn'); return; }
      try {
        await api.post(`/api/v2/concepts/${currentId}/aliases`, { aliases: list });
        closeModal();
        ctx.toast(`已加 ${list.length} 个别名`, 'ok');
        await refresh(root, ctx);
      } catch (err) { ctx.toast(err.message, 'error'); }
    });
  });
}