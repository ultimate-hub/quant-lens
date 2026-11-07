/* ============================================================
   我的持仓（持仓日志）
   ------------------------------------------------------------
   每条记录 = 一次买入（同一只股票可以有多笔，对应分批建仓）
   自动算当前价、市值、浮盈、占总仓比例
   可以设「目标配比」，和实际占比对比看超配/低配
   ============================================================ */

import {
  esc, api, state, navigate, pageHead, alertBox, thousand, saveLocal, loadLocal,
} from '../shared.js';

let chart = null;
let data = null;
let viewMode = 'symbol';   // symbol | lot

export async function render(root, ctx) {
  root.innerHTML = `
    ${pageHead('我的持仓', '记录每次买入，自动算市值、浮盈和配比',
      `<button class="btn" id="btnAdd">＋ 记录买入</button>`)}

    <div id="pfSummary" class="mb-16"></div>
    <div id="pfBody"><div class="loading"><div class="spinner"></div><span>计算中…</span></div></div>`;

  root.querySelector('#btnAdd').addEventListener('click', () => openEdit(root, ctx, null));
  await load(root, ctx);
}

async function load(root, ctx) {
  const body = root.querySelector('#pfBody');
  try {
    data = await api.get('/api/v2/positions');
  } catch (err) {
    body.innerHTML = alertBox('error', `读取持仓失败：${esc(err.message)}`);
    return;
  }
  renderSummary(root);
  renderBody(root, ctx);
}

// ============================================================
// 汇总
// ============================================================
function renderSummary(root) {
  const s = data.summary;
  const pnlCls = s.total_pnl > 0 ? 'up' : s.total_pnl < 0 ? 'down' : '';

  root.querySelector('#pfSummary').innerHTML = `
  <div class="grid-3" style="grid-template-columns:repeat(4,1fr)">
    <div class="card"><div class="card-pad" style="padding:16px 18px">
      <div class="fs-11 text-3">总市值</div>
      <div class="num fw-700 mt-8" style="font-size:22px">${thousand(s.total_value, 0)}</div>
      <div class="fs-11 text-3 mt-8">${s.symbol_count} 只 · ${s.count} 笔</div>
    </div></div>

    <div class="card"><div class="card-pad" style="padding:16px 18px">
      <div class="fs-11 text-3">总成本</div>
      <div class="num fw-700 mt-8" style="font-size:22px">${thousand(s.total_cost, 0)}</div>
      <div class="fs-11 text-3 mt-8">买入金额合计</div>
    </div></div>

    <div class="card"><div class="card-pad" style="padding:16px 18px">
      <div class="fs-11 text-3">浮动盈亏</div>
      <div class="num fw-700 mt-8 ${pnlCls}" style="font-size:22px">
        ${s.total_pnl > 0 ? '+' : ''}${thousand(s.total_pnl, 0)}
      </div>
      <div class="fs-11 mt-8 ${pnlCls}">
        ${s.total_pnl_pct > 0 ? '+' : ''}${s.total_pnl_pct}%
      </div>
    </div></div>

    <div class="card"><div class="card-pad" style="padding:16px 18px">
      <div class="fs-11 text-3">数据</div>
      <div class="num fw-700 mt-8" style="font-size:15px">${esc(s.updated_at.slice(11))}</div>
      <div class="fs-11 text-3 mt-8">
        ${s.price_missing ? `<span class="up">${s.price_missing} 笔取价失败</span>`
                          : '行情已更新'}
      </div>
    </div></div>
  </div>`;
}

// ============================================================
// 主体
// ============================================================
function renderBody(root, ctx) {
  const body = root.querySelector('#pfBody');

  if (!data.items.length) {
    body.innerHTML = `
    <div class="card"><div class="empty">
      <div class="empty-title">还没有持仓记录</div>
      <div class="empty-desc">
        点右上角「记录买入」，填上买入日期、价格和数量。<br>
        同一只股票分批买入就记多条，会分别显示。
      </div>
      <div class="mt-20"><button class="btn" id="btnAdd2">＋ 记录第一笔</button></div>
    </div></div>`;
    body.querySelector('#btnAdd2').addEventListener('click',
      () => openEdit(root, ctx, null));
    return;
  }

  body.innerHTML = `
    <div class="card mb-16">
      <div class="card-head">
        <div class="card-title">配比</div>
        <div class="card-sub">实际占比 vs 目标占比（灰色竖线是目标）</div>
      </div>
      <div id="pfChart" style="width:100%;height:${Math.max(180, data.by_symbol.length * 38 + 60)}px"></div>
    </div>

    <div class="card">
      <div class="card-head">
        <div class="flex center gap-12">
          <div class="card-title">持仓明细</div>
          <div class="chips" id="pfTabs"></div>
        </div>
        <button class="btn ghost sm" id="btnClear">清空全部</button>
      </div>
      <div class="table-wrap" id="pfTable"></div>
    </div>`;

  renderTabs(root, ctx);
  renderTable(root, ctx);
  drawChart(root);

  body.querySelector('#btnClear').addEventListener('click', async () => {
    if (!ctx.confirm('确定清空全部持仓记录？这个操作不能撤销。')) return;
    try {
      await api.del('/api/v2/positions');
      ctx.toast('已清空', 'ok');
      await load(root, ctx);
    } catch (err) { ctx.toast(err.message, 'error'); }
  });
}

function renderTabs(root, ctx) {
  const box = root.querySelector('#pfTabs');
  box.innerHTML = `
    <span class="chip ${viewMode === 'symbol' ? 'on' : ''}" data-v="symbol">按股票汇总</span>
    <span class="chip ${viewMode === 'lot' ? 'on' : ''}" data-v="lot">按买入记录</span>`;

  box.querySelectorAll('.chip').forEach(c => {
    c.addEventListener('click', () => {
      viewMode = c.dataset.v;
      saveLocal('pf_view', viewMode);
      renderTabs(root, ctx);
      renderTable(root, ctx);
    });
  });
}

function renderTable(root, ctx) {
  const box = root.querySelector('#pfTable');

  if (viewMode === 'lot') {
    const rows = data.items.map(it => `
      <tr>
        <td>
          <b>${esc(it.name || it.symbol)}</b>
          <div class="fs-11 text-3">${esc(it.symbol)}</div>
        </td>
        <td class="fs-12">${esc(it.buy_date)}
          <div class="fs-11 text-3">持有 ${it.hold_days ?? '—'} 天</div></td>
        <td class="num fs-12">${thousand(it.buy_price)}</td>
        <td class="num fs-12">${it.shares}</td>
        <td class="num fs-12">${thousand(it.current_price)}
          ${it.price_ok ? '' : '<span class="tag tag-warn" style="margin-left:4px">估</span>'}</td>
        <td class="num fs-12">${thousand(it.value, 0)}</td>
        <td class="num fs-12 ${it.pnl > 0 ? 'up' : it.pnl < 0 ? 'down' : ''}">
          ${it.pnl > 0 ? '+' : ''}${thousand(it.pnl, 0)}
          <div class="fs-11">${it.pnl_pct > 0 ? '+' : ''}${it.pnl_pct}%</div>
        </td>
        <td class="num fs-12">${it.target_ratio ? it.target_ratio + '%' : '—'}</td>
        <td>
          <button class="btn ghost sm" data-edit="${it.id}">改</button>
          <button class="btn danger sm" data-del="${it.id}">删</button>
        </td>
      </tr>`).join('');

    box.innerHTML = `
      <table class="table">
        <thead><tr>
          <th>标的</th><th>买入</th><th>买入价</th><th>数量</th>
          <th>现价</th><th>市值</th><th>盈亏</th><th>目标</th><th></th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  } else {
    const rows = data.by_symbol.map(b => `
      <tr>
        <td>
          <b>${esc(b.name || b.symbol)}</b>
          <div class="fs-11 text-3">${esc(b.symbol)}</div>
        </td>
        <td class="num fs-12">${b.shares}</td>
        <td class="num fs-12">${thousand(b.avg_cost)}
          <div class="fs-11 text-3">均价</div></td>
        <td class="num fs-12">${thousand(b.current_price)}</td>
        <td class="num fs-12">${thousand(b.value, 0)}</td>
        <td class="num fs-12 ${b.pnl > 0 ? 'up' : b.pnl < 0 ? 'down' : ''}">
          ${b.pnl > 0 ? '+' : ''}${thousand(b.pnl, 0)}
          <div class="fs-11">${b.pnl_pct > 0 ? '+' : ''}${b.pnl_pct}%</div>
        </td>
        <td class="num fs-12">${b.weight}%</td>
        <td class="num fs-12">
          ${b.target_ratio ? `${b.target_ratio}%` : '—'}
          ${b.gap !== null && b.gap !== undefined
            ? `<div class="fs-11 ${b.gap > 0 ? 'up' : b.gap < 0 ? 'down' : 'text-3'}">
                 ${b.gap > 0 ? '超' : '低'} ${Math.abs(b.gap)}%</div>` : ''}
        </td>
        <td class="fs-11 text-3">${b.lots} 笔</td>
        <td><button class="btn ghost sm" data-open="${esc(b.symbol)}">分析</button></td>
      </tr>`).join('');

    box.innerHTML = `
      <table class="table">
        <thead><tr>
          <th>标的</th><th>持股数</th><th>成本价</th><th>现价</th><th>市值</th>
          <th>盈亏</th><th>实际占比</th><th>目标 / 偏差</th><th>笔数</th><th></th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  box.querySelectorAll('[data-edit]').forEach(b => {
    b.addEventListener('click', () => {
      const id = Number(b.dataset.edit);
      openEdit(root, ctx, data.items.find(x => x.id === id));
    });
  });

  box.querySelectorAll('[data-del]').forEach(b => {
    b.addEventListener('click', async () => {
      if (!ctx.confirm('删除这条记录？')) return;
      try {
        await api.del(`/api/v2/positions/${b.dataset.del}`);
        ctx.toast('已删除', 'ok');
        await load(root, ctx);
      } catch (err) { ctx.toast(err.message, 'error'); }
    });
  });

  box.querySelectorAll('[data-open]').forEach(b => {
    b.addEventListener('click', () => navigate('analysis', { symbol: b.dataset.open }));
  });
}

// ============================================================
// 配比图
// ============================================================
function drawChart(root) {
  const el = document.getElementById('pfChart');
  if (!el || !window.echarts) return;

  if (chart) { try { chart.dispose(); } catch (_) {} }

  // 从大到小，方便读
  const list = [...data.by_symbol].sort((a, b) => a.weight - b.weight);

  const weights = list.map(b => b.weight);
  const targets = list.map(b => b.target_ratio || 0);

  chart = echarts.init(el);
  chart.setOption({
    grid: { left: 110, right: 60, top: 16, bottom: 24 },
    tooltip: {
      backgroundColor: 'rgba(17,24,39,.95)',
      borderWidth: 0,
      textStyle: { color: '#e5e7eb', fontSize: 12 },
      formatter: (p) => {
        const b = list.find(x => x.name === p.name) || {};
        return `<b>${p.name}</b><br/>
          实际占比 ${b.weight}%<br/>
          ${b.target_ratio ? `目标 ${b.target_ratio}%　偏差 ${b.gap > 0 ? '+' : ''}${b.gap}%`
                           : '未设目标配比'}<br/>
          市值 ${thousand(b.value, 0)}`;
      },
    },
    xAxis: {
      type: 'value',
      axisLabel: { color: '#9ca3af', fontSize: 11, formatter: '{value}%' },
      splitLine: { lineStyle: { color: '#f0f2f5' } },
    },
    yAxis: {
      type: 'category',
      data: list.map(b => (b.name || b.symbol).slice(0, 8)),
      axisLabel: { color: '#6b7280', fontSize: 11.5 },
      axisLine: { lineStyle: { color: '#e8eaed' } },
      axisTick: { show: false },
    },
    series: [
      {
        name: '实际占比', type: 'bar', data: weights,
        barWidth: '46%',
        itemStyle: {
          color: (p) => {
            const b = list[p.dataIndex];
            return b.pnl > 0 ? '#e5484d' : b.pnl < 0 ? '#12a594' : '#4f46e5';
          },
          borderRadius: [0, 4, 4, 0],
        },
        label: {
          show: true, position: 'right', fontSize: 11, color: '#6b7280',
          formatter: '{c}%',
        },
        markLine: {
          silent: true, symbol: 'none',
          lineStyle: { color: '#cbd5e1', type: 'dashed', width: 1.5 },
          label: { show: false },
          data: targets.filter(t => t > 0).map((t, i) => ({
            xAxis: t,
            lineStyle: { color: '#94a3b8', type: 'dashed', width: 1.5 },
          })),
        },
      },
    ],
  });
}

// ============================================================
// 添加 / 编辑
// ============================================================
let modalEl = null;

function openModal(html, onMount) {
  closeModal();
  const mask = document.createElement('div');
  mask.className = 'modal-mask';
  mask.innerHTML = `<div class="modal" style="max-width:560px">${html}</div>`;
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

function openEdit(root, ctx, item) {
  const isEdit = !!item;
  const today = new Date().toISOString().slice(0, 10);

  openModal(`
    <div class="modal-head">
      <div class="modal-title">${isEdit ? '修改记录' : '记录一笔买入'}</div>
      <button class="modal-close" data-close>×</button>
    </div>
    <div class="modal-body">
      <div class="field">
        <label>股票代码</label>
        <input class="input" id="pSymbol" placeholder="600519.SH / 00700.HK / AAPL.US"
               value="${esc(item?.symbol || '')}">
        <div class="hint">支持标准代码。不确定代码的话，先去搜索框搜一下</div>
      </div>

      <div class="grid-2" style="grid-template-columns:1fr 1fr;gap:12px">
        <div class="field">
          <label>买入日期</label>
          <input class="input" id="pDate" type="date" value="${esc(item?.buy_date || today)}">
        </div>
        <div class="field">
          <label>买入价</label>
          <input class="input" id="pPrice" type="number" step="0.001"
                 value="${item?.buy_price ?? ''}" placeholder="0.000">
        </div>
      </div>

      <div class="grid-2" style="grid-template-columns:1fr 1fr;gap:12px">
        <div class="field">
          <label>数量（股 / 份）</label>
          <input class="input" id="pShares" type="number" step="1"
                 value="${item?.shares ?? ''}" placeholder="100">
        </div>
        <div class="field">
          <label>目标配比 %（可选）</label>
          <input class="input" id="pTarget" type="number" step="0.1"
                 value="${item?.target_ratio ?? ''}" placeholder="留空表示不设">
        </div>
      </div>

      <div class="field">
        <label>备注（可选）</label>
        <input class="input" id="pNote" placeholder="为什么买、当时的想法"
               value="${esc(item?.note || '')}">
      </div>

      <div class="fs-11 text-3" style="line-height:1.7">
        提示：目标配比填的是「这只股票占总仓的百分比」。
        比如填 25，意思是希望它占四分之一。不填就不做配比对比。
      </div>
    </div>
    <div class="modal-foot">
      <button class="btn ghost" data-close>取消</button>
      <button class="btn" id="pOk">${isEdit ? '保存' : '记录'}</button>
    </div>`, (mask) => {
    mask.querySelector('#pSymbol').focus();

    mask.querySelector('#pOk').addEventListener('click', async () => {
      const body = {
        symbol: mask.querySelector('#pSymbol').value.trim().toUpperCase(),
        buy_date: mask.querySelector('#pDate').value,
        buy_price: Number(mask.querySelector('#pPrice').value),
        shares: Number(mask.querySelector('#pShares').value),
        target_ratio: mask.querySelector('#pTarget').value
          ? Number(mask.querySelector('#pTarget').value) : null,
        note: mask.querySelector('#pNote').value.trim(),
      };

      if (!body.symbol) { ctx.toast('请填股票代码', 'warn'); return; }
      if (!body.buy_date) { ctx.toast('请选买入日期', 'warn'); return; }
      if (!(body.buy_price > 0)) { ctx.toast('买入价要大于 0', 'warn'); return; }
      if (!(body.shares > 0)) { ctx.toast('数量要大于 0', 'warn'); return; }

      try {
        if (isEdit) {
          await api.put(`/api/v2/positions/${item.id}`, body);
        } else {
          await api.post('/api/v2/positions', body);
        }
        closeModal();
        ctx.toast(isEdit ? '已保存' : '已记录', 'ok');
        await load(root, ctx);
      } catch (err) {
        ctx.toast(err.message, 'error');
      }
    });
  });
}