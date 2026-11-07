/* ============================================================
   市场全景（热力图）
   ------------------------------------------------------------
   Finviz 风格的树状图：面积 = 市值，颜色 = 涨跌幅
   每天第一次打开时抓一次，当天不变（后端缓存），可一键导出封面图
   ============================================================ */

import { esc, api, state, navigate, thousand } from '../shared.js';

let chart = null;
let currentData = null;
let marketFilter = '';   // '' = 全部
let sizeMode = 'cap';    // cap = 按市值 / equal = 等权

export async function render(root, ctx) {
  root.innerHTML = `
    <div class="page-head">
      <div>
        <div class="page-title">市场全景</div>
        <div class="page-desc" id="hmDesc">正在获取今日行情…</div>
      </div>
      <div class="flex center gap-8">
        <button class="btn ghost sm" id="btnRefresh">刷新行情</button>
        <button class="btn sm" id="btnExport">下载封面图</button>
      </div>
    </div>

    <div id="hmStats" class="mb-16"></div>

    <div class="card mb-16">
      <div class="card-head">
        <div class="flex center gap-12">
          <div class="card-title">板块热力图</div>
          <div class="chips" id="hmMarkets"></div>
        </div>
        <div class="card-sub">
          面积 = 市值 · 颜色 = 涨跌幅 · 点击板块可放大
        </div>
      </div>
      <div id="hmChart" style="width:100%;height:640px"></div>
    </div>

    <div id="hmGroups"></div>`;

  await load(root, ctx, false);

  root.querySelector('#btnRefresh').addEventListener('click', async () => {
    const b = root.querySelector('#btnRefresh');
    b.disabled = true; b.textContent = '抓取中…';
    await load(root, ctx, true);
    b.disabled = false; b.textContent = '刷新行情';
  });

  root.querySelector('#btnExport').addEventListener('click', () => {
    if (!chart) return;
    const url = chart.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#fff' });
    const a = document.createElement('a');
    a.href = url;
    a.download = `量镜-市场全景-${currentData?.day || 'today'}.png`;
    a.click();
    ctx.toast('封面图已保存', 'ok');
  });
}

async function load(root, ctx, force) {
  let d;
  try {
    d = await api.get('/api/v2/market/heatmap', { force: force ? 'true' : undefined });
  } catch (err) {
    root.querySelector('#hmChart').innerHTML =
      `<div class="empty"><div class="empty-title">行情获取失败</div>
       <div class="empty-desc">${esc(err.message)}</div></div>`;
    return;
  }
  currentData = d;
  renderAll(root, ctx);
}

function renderAll(root, ctx) {
  const d = currentData;
  renderStats(root);
  renderMarkets(root, ctx);
  drawChart(root, ctx);
  renderGroupList(root, ctx);
}

// ============================================================
// 顶部统计
// ============================================================
function renderStats(root) {
  const d = currentData;
  const s = d.stats;

  root.querySelector('#hmDesc').textContent =
    `${d.day} · 数据更新于 ${d.updated_at} · 覆盖 ${s.total} 只标的`;

  const upRate = s.total ? (s.up / s.total * 100) : 0;
  const avgCls = s.avg_change > 0 ? 'up' : s.avg_change < 0 ? 'down' : '';

  root.querySelector('#hmStats').innerHTML = `
  <div class="grid-3" style="grid-template-columns:repeat(4,1fr)">
    <div class="card"><div class="card-pad" style="padding:16px 18px">
      <div class="fs-11 text-3">加权涨幅</div>
      <div class="num fw-700 mt-8 ${avgCls}" style="font-size:24px">
        ${s.avg_change > 0 ? '+' : ''}${s.avg_change}%
      </div>
      <div class="fs-11 text-3 mt-8">按市值加权</div>
    </div></div>

    <div class="card"><div class="card-pad" style="padding:16px 18px">
      <div class="fs-11 text-3">涨跌家数</div>
      <div class="num fw-700 mt-8" style="font-size:24px">
        <span class="up">${s.up}</span>
        <span class="text-3" style="font-size:16px"> / </span>
        <span class="down">${s.down}</span>
      </div>
      <div class="fs-11 text-3 mt-8">上涨占比 ${upRate.toFixed(0)}%</div>
    </div></div>

    <div class="card"><div class="card-pad" style="padding:16px 18px">
      <div class="fs-11 text-3">总市值</div>
      <div class="num fw-700 mt-8" style="font-size:24px">
        ${(s.total_cap / 1e12).toFixed(2)}<span style="font-size:14px">万亿</span>
      </div>
      <div class="fs-11 text-3 mt-8">${d.groups.length} 个行业</div>
    </div></div>

    <div class="card"><div class="card-pad" style="padding:16px 18px">
      <div class="fs-11 text-3">市场分布</div>
      <div class="mt-8" style="line-height:1.9">
        ${d.markets.map(m => `
          <span class="fs-12" style="margin-right:10px">
            ${esc(marketLabel(m.market))}
            <b class="num ${m.change > 0 ? 'up' : m.change < 0 ? 'down' : ''}">
              ${m.change > 0 ? '+' : ''}${m.change}%</b>
          </span>`).join('')}
      </div>
    </div></div>
  </div>`;
}

function marketLabel(code) {
  return { SH: '沪市', SZ: '深市', BJ: '北交所', HK: '港股', US: '美股' }[code] || code;
}

function renderMarkets(root, ctx) {
  const box = root.querySelector('#hmMarkets');
  const mkts = [{ code: '', label: '全部' }].concat(
    (currentData.markets || []).map(m => ({ code: m.market, label: marketLabel(m.market) })));

  // 面积口径：按市值（真实权重）还是等权（看家数分布）
  const sizes = [
    { code: 'cap', label: '按市值' },
    { code: 'equal', label: '等权' },
  ];

  box.innerHTML = mkts.map(m => `
    <span class="chip ${marketFilter === m.code ? 'on' : ''}" data-mkt="${m.code}">
      ${esc(m.label)}</span>`).join('')
    + '<span style="width:1px;height:16px;background:var(--border);margin:0 4px"></span>'
    + sizes.map(s => `
    <span class="chip ${sizeMode === s.code ? 'on' : ''}" data-size="${s.code}">
      ${esc(s.label)}</span>`).join('');

  box.querySelectorAll('[data-mkt]').forEach(c => {
    c.addEventListener('click', () => {
      marketFilter = c.dataset.mkt;
      renderMarkets(root, ctx);
      drawChart(root, ctx);
    });
  });

  box.querySelectorAll('[data-size]').forEach(c => {
    c.addEventListener('click', () => {
      sizeMode = c.dataset.size;
      renderMarkets(root, ctx);
      drawChart(root, ctx);
    });
  });
}

// ============================================================
// 颜色：中国习惯 红涨绿跌，幅度越大颜色越深
// ============================================================
function colorFor(pct) {
  if (pct === null || pct === undefined) return '#9ca3af';
  const v = Math.max(-10, Math.min(10, pct)) / 10;   // 归一到 [-1, 1]
  if (Math.abs(v) < 0.005) return '#6b7280';

  const t = Math.min(Math.abs(v), 1);
  // 深色端固定，浅色端随幅度变化
  if (v > 0) {
    return `rgb(${Math.round(150 + 95 * t)},${Math.round(40 - 30 * t)},${Math.round(45 - 35 * t)})`;
  }
  return `rgb(${Math.round(30 + 10 * t)},${Math.round(100 + 70 * t)},${Math.round(85 + 40 * t)})`;
}

function textColorFor(pct) {
  return Math.abs(pct) > 2.5 ? '#fff' : '#f3f4f6';
}

// ============================================================
// 树状图
// ============================================================
function drawChart(root, ctx) {
  const el = document.getElementById('hmChart');
  if (!el || !window.echarts || !currentData) return;

  if (chart) { try { chart.dispose(); } catch (_) {} }

  const groups = currentData.groups
    .map(g => ({
      ...g,
      stocks: marketFilter
        ? g.stocks.filter(s => s.symbol.endsWith('.' + marketFilter))
        : g.stocks,
    }))
    .filter(g => g.stocks.length > 0);

  // 格子面积：按市值（真实权重）或等权（每只算一份，看家数分布）
  const weightOf = (s) => (sizeMode === 'equal' ? 1 : Math.max(s.market_cap, 1));

  const data = groups.map(g => ({
    name: g.name,
    value: g.stocks.reduce((a, s) => a + weightOf(s), 0),
    change: g.change,
    itemStyle: { color: colorFor(g.change) },
    children: g.stocks.map(s => ({
      name: s.name,
      symbol: s.symbol,
      value: weightOf(s),
      market_cap: s.market_cap,
      change: s.change,
      price: s.price,
      itemStyle: { color: colorFor(s.change) },
    })),
  }));

  chart = echarts.init(el);
  chart.setOption({
    backgroundColor: '#fff',
    tooltip: {
      backgroundColor: 'rgba(17,24,39,.95)',
      borderWidth: 0,
      textStyle: { color: '#e5e7eb', fontSize: 12 },
      formatter: (info) => {
        const dd = info.data;
        if (!dd) return '';
        if (dd.symbol) {
          const c = dd.change;
          return `<b>${dd.name}</b> <span style="opacity:.6">${dd.symbol}</span><br/>
            价 ${thousand(dd.price)}　涨跌 <b style="color:${c >= 0 ? '#f87171' : '#34d399'}">
            ${c > 0 ? '+' : ''}${c}%</b><br/>
            市值 ${(dd.market_cap / 1e8).toFixed(0)} 亿`;
        }
        const cap = (dd.children || []).reduce((a, x) => a + (x.market_cap || 0), 0);
        return `<b>${dd.name}</b><br/>板块涨跌 ${dd.change > 0 ? '+' : ''}${dd.change}%<br/>
          ${dd.children?.length || 0} 只 · 市值 ${(cap / 1e8).toFixed(0)} 亿`;
      },
    },
    series: [{
      type: 'treemap',
      roam: false,
      nodeClick: 'zoomToNode',
      breadcrumb: { show: true, height: 22, top: 0, itemStyle: { color: '#eef2ff' } },
      leafDepth: 1,
      width: '100%',
      height: '100%',
      top: 26,
      data,
      levels: [
        // 第 0 层：板块
        {
          itemStyle: { borderWidth: 2, borderColor: '#fff', gapWidth: 2 },
          upperLabel: {
            show: true, height: 20, color: '#111827', fontSize: 11,
            fontWeight: 600, backgroundColor: 'transparent',
          },
          label: { show: false },
        },
        // 第 1 层：个股
        {
          itemStyle: { borderWidth: 1, borderColor: 'rgba(255,255,255,.55)', gapWidth: 1 },
          label: {
            show: true, fontSize: 10.5, color: '#fff', lineHeight: 13,
            formatter: (p) => {
              const d = p.data;
              // 等权模式下每块大小接近，都显示；按市值时太小的格子不写字，否则糊成一团
              if (!d || (sizeMode === 'cap' && d.market_cap < 8e10)) return '';
              const nm = (d.name || '').slice(0, 5);
              return `{n|${nm}}\n{c|${d.change > 0 ? '+' : ''}${d.change}%}`;
            },
            rich: {
              n: { fontSize: 10.5, color: '#fff', fontWeight: 600 },
              c: { fontSize: 10, color: 'rgba(255,255,255,.92)' },
            },
          },
        },
      ],
    }],
  });

  // 点击个股 → 跳个股分析
  chart.on('click', (p) => {
    if (p.data && p.data.symbol) {
      navigate('analysis', { symbol: p.data.symbol });
    }
  });

  if (!window.__hmResize) {
    window.__hmResize = true;
    window.addEventListener('resize', () => { try { chart && chart.resize(); } catch (_) {} });
  }
}

// ============================================================
// 下方板块列表（补充可读性，热力图太小的格子看不清）
// ============================================================
function renderGroupList(root, ctx) {
  const groups = [...currentData.groups].sort((a, b) => b.change - a.change);

  const rows = groups.map(g => {
    const top = g.stocks.slice(0, 3)
      .map(s => `<span class="fs-11 text-3">${esc(s.name.slice(0, 6))}
        <b class="num ${s.change > 0 ? 'up' : s.change < 0 ? 'down' : ''}">
        ${s.change > 0 ? '+' : ''}${s.change}%</b></span>`).join('　');

    return `
    <tr>
      <td><b>${esc(g.name)}</b><div class="fs-11 text-3">${g.count} 只</div></td>
      <td class="num fw-700 ${g.change > 0 ? 'up' : g.change < 0 ? 'down' : ''}">
        ${g.change > 0 ? '+' : ''}${g.change}%</td>
      <td class="num fs-12">${(g.market_cap / 1e8).toFixed(0)} 亿</td>
      <td>${top}</td>
      <td><button class="btn ghost sm" data-g="${esc(g.name)}">查看</button></td>
    </tr>`;
  }).join('');

  root.querySelector('#hmGroups').innerHTML = `
  <div class="card">
    <div class="card-head">
      <div class="card-title">行业明细</div>
      <div class="card-sub">按涨跌幅排序</div>
    </div>
    <div class="table-wrap">
      <table class="table">
        <thead><tr><th>行业</th><th>涨跌幅</th><th>市值</th><th>领涨/领跌</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  </div>`;

  root.querySelectorAll('[data-g]').forEach(b => {
    b.addEventListener('click', () => {
      const name = b.dataset.g;
      const g = currentData.groups.find(x => x.name === name);
      if (!g) return;
      // 用该板块最有代表性的一只股票跳到分析页
      const first = g.stocks[0];
      if (first) navigate('analysis', { symbol: first.symbol });
    });
  });
}