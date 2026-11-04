/* ============================================================
   个股分析页
   ============================================================ */

import {
  esc, fmt, pct, dirCls, thousand, emptyBox, alertBox, navigate, api,
} from '../shared.js';
import * as outlook from './analysis-outlook.js';

let charts = {};

export async function render(root, ctx) {
  const { state, params } = ctx;

  // 销毁上一轮图表，避免内存泄漏
  Object.values(charts).forEach(c => { try { c.dispose(); } catch (_) {} });
  charts = {};
  outlook.dispose();

  const symbol = params.symbol || state.currentSymbol;

  if (!symbol) {
    root.innerHTML = emptyState();
    root.querySelectorAll('[data-quick]').forEach(node => {
      node.addEventListener('click', () => navigate('analysis', { symbol: node.dataset.quick }));
    });
    return;
  }

  state.currentSymbol = symbol;
  root.innerHTML = loadingState(symbol);

  let data;
  try {
    data = await api.get(`/api/v2/analyze/${encodeURIComponent(symbol)}`, {
      profile: state.profile,
      use_ai: state.channels.length > 0,
      channel_id: state.channels[0]?.id,
    });
  } catch (err) {
    root.innerHTML = `
      <div class="card"><div class="card-pad">
        ${alertBox('error', `<b>分析失败：${esc(symbol)}</b><br>${esc(err.message)}`)}
        <div class="mt-16">
          <button class="btn ghost sm" onclick="location.hash='#/analysis'">返回</button>
        </div>
      </div></div>`;
    return;
  }

  root.innerHTML = buildHTML(data);
  drawCharts(data);

  // 推演与配仓建议单独拉取（它内部要再跑一次分析算锚点，串行会慢）
  const wrap = root.querySelector('#outlookWrap');
  if (wrap) {
    outlook.mountPlaceholder(root);
    api.get(`/api/v2/outlook/${encodeURIComponent(symbol)}`, {
      days: 20, sims: 1000, profile: state.profile,
    })
      .then(d => outlook.renderOutlook(root, d, ctx))
      .catch(err => outlook.renderError(root, err.message));
  }

  // 模式名显示在雷达卡头
  const modeEl = document.getElementById('radarMode');
  if (modeEl) {
    const p = state.profiles.find(x => x.key === state.profile);
    modeEl.textContent = p ? p.label : '';
  }
}

// ============================================================
// 状态页
// ============================================================
function emptyState() {
  return `
  <div class="card">
    <div class="empty">
      <svg class="empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
        <circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>
      </svg>
      <div class="empty-title">搜索一只股票开始分析</div>
      <div class="empty-desc">
        在顶部搜索框输入代码或名称，例如 <b>600519</b>、<b>茅台</b>、<b>AAPL</b><br>
        也可以按 <b>Ctrl K</b> 快速聚焦搜索框
      </div>
      <div class="flex center gap-8" style="justify-content:center;margin-top:20px;flex-wrap:wrap">
        <span class="chip" data-quick="600519.SH">贵州茅台</span>
        <span class="chip" data-quick="00700.HK">腾讯控股</span>
        <span class="chip" data-quick="AAPL.US">苹果</span>
        <span class="chip" data-quick="000858.SZ">五粮液</span>
      </div>
    </div>
  </div>`;
}

function loadingState(symbol) {
  return `
  <div class="hero">
    <div>
      <div class="hero-name">${esc(symbol)}</div>
      <div class="hero-meta"><span class="tag tag-neutral">正在获取数据…</span></div>
    </div>
  </div>
  <div class="loading"><div class="spinner"></div>
    <span>抓取行情、计算 76 项指标…（首次约 3-8 秒）</span></div>`;
}

// ============================================================
// 主结构
// ============================================================
function buildHTML(d) {
  const ai = d.ai;
  return `
  ${heroSection(d)}
  ${axisSection(d.final)}
  <div class="grid-2 mb-16">
    ${radarCard()}
    ${metricsCard(d)}
  </div>
  <div id="outlookWrap" class="mb-16"></div>
  ${chartCard(d)}
  ${ai ? aiSection(ai, d.meta) : aiErrorSection(d.meta)}
  ${extendedSection(d.extended_metrics || {})}
  ${disclaimer()}`;
}

function priceOf(d) {
  return d.final.current_price ?? d.price_metrics.latest_close;
}

// ---- 头部 ----
function heroSection(d) {
  const f = d.final, pm = d.price_metrics;
  const score = Number(f.composite) || 0;

  const C = 2 * Math.PI * 45;
  const offset = C * (1 - Math.min(score, 100) / 100);
  const ringColor = score >= 65 ? '#12a594' : score >= 50 ? '#4f46e5' : '#e5484d';

  const ratingCls = ['强烈关注', '关注'].includes(f.rating) ? 'tag-up'
    : ['回避', '强烈回避'].includes(f.rating) ? 'tag-down' : 'tag-neutral';

  return `
  <div class="hero">
    <div>
      <div class="hero-name">${esc(d.name || d.symbol)}</div>
      <div class="hero-meta">
        <span class="tag tag-primary">${esc(d.symbol)}</span>
        <span>${esc(d.market_label)}</span>
        <span style="color:var(--border-strong)">|</span>
        <span class="price-now num">${thousand(priceOf(d))}</span>
        <span class="price-chg num ${dirCls(pm.daily_change)}">
          ${(pm.daily_change ?? 0) >= 0 ? '+' : ''}${pct(pm.daily_change)}
        </span>
        <span style="font-size:11px;color:var(--text-3)">
          今日
        </span>
        <span class="tag ${ratingCls}">${esc(f.rating)}</span>
      </div>
    </div>

    <div class="score-wrap">
      <div class="score-detail">
        <div>量化 <b class="num">${fmt(f.quant_only, 1)}</b></div>
        ${f.ai_only !== null && f.ai_only !== undefined
          ? `<div>AI <b class="num">${fmt(f.ai_only, 1)}</b></div>` : ''}
        <div style="color:var(--text-3);font-size:11px">
          ${f.confidence ? `置信度 ${(f.confidence * 100).toFixed(0)}% · ` : ''}${esc(f.horizon || '')}
        </div>
      </div>
      <div class="ring">
        <svg width="104" height="104">
          <circle cx="52" cy="52" r="45" fill="none" stroke="#eef0f3" stroke-width="8"/>
          <circle cx="52" cy="52" r="45" fill="none" stroke="${ringColor}" stroke-width="8"
                  stroke-linecap="round"
                  stroke-dasharray="${C.toFixed(1)}"
                  stroke-dashoffset="${offset.toFixed(1)}"/>
        </svg>
        <div class="ring-center">
          <div class="ring-num num">${fmt(score, 1)}</div>
          <div class="ring-lbl">${esc(f.grade)}</div>
        </div>
      </div>
    </div>
  </div>`;
}

// ---- 价格轴 ----
function axisSection(f) {
  const price = f.current_price;
  if (!price) return '';

  const stop = f.stop_loss;
  const target = f.target_price;
  const er = f.entry_range || {};

  const values = [price, stop, target, er.low, er.high].filter(v => v > 0);
  const min = Math.min(...values) * 0.92;
  const max = Math.max(...values) * 1.08;
  const span = (max - min) || 1;
  const pos = v => (((v - min) / span) * 100).toFixed(2);

  const segs = [];
  if (er.low && er.high) {
    segs.push({ a: pos(er.low), b: pos(er.high), c: 'rgba(79,70,229,.12)' });
  }
  if (stop) segs.push({ a: pos(stop), b: pos(stop), c: 'rgba(18,165,148,.14)' });

  const marks = [];
  if (stop) marks.push({ cls: 'stop', p: Number(pos(stop)), label: '止损', val: thousand(stop) });
  if (er.low && er.high) {
    marks.push({ cls: 'buy', p: Number(pos((er.low + er.high) / 2)), label: '买入区间',
      val: `${thousand(er.low, 0)}~${thousand(er.high, 0)}` });
  }
  marks.push({ cls: 'now', p: Number(pos(price)), label: '现价', val: thousand(price) });
  if (target) {
    const up = ((target / price - 1) * 100).toFixed(1);
    marks.push({ cls: 'target', p: Number(pos(target)), label: '目标价',
      val: `${thousand(target)}<span style="font-size:10px;font-weight:500">
            ${up >= 0 ? '+' : ''}${up}%</span>` });
  }

  // 标签避让：两个标记位置太近时，把后一个标签下移一行，避免文字叠在一起
  const sorted = [...marks].sort((a, b) => a.p - b.p);
  let hasRow2 = false;
  for (let i = 1; i < sorted.length; i++) {
    if (sorted[i].p - sorted[i - 1].p < 12) {
      sorted[i].row2 = true;
      hasRow2 = true;
    }
  }
  const trackH = hasRow2 ? 106 : 74;

  return `
  <div class="card axis-card mb-16">
    <div class="axis-head">
      <div>
        <div class="card-title">价格锚点</div>
        <div class="card-sub" style="margin-top:2px">系统计算，AI 只能在这些价位中选择</div>
      </div>
      <div style="font-size:11.5px;color:var(--text-3)">
        ${f.risk_reward ? `风险收益比 <b class="num" style="color:var(--text);font-size:13px">${f.risk_reward}</b>　` : ''}
        ${f.position_pct ? `建议仓位 <b class="num" style="color:var(--text);font-size:13px">${fmt(f.position_pct, 1)}%</b>` : ''}
      </div>
    </div>

    <div class="axis-track" style="height:${trackH}px">
      ${segs.map(s => `<div class="axis-seg"
        style="left:${s.a}%;width:${Math.max(Number(s.b) - Number(s.a), 0.4)}%;background:${s.c}"></div>`).join('')}
      <div class="axis-line"></div>
      ${marks.map(m => `
        <div class="axis-mark ${m.cls} ${m.row2 ? 'row2' : ''}" style="left:${m.p}%">
          <div class="stem" ${m.cls === 'now' ? 'style="background:var(--text)"' : ''}></div>
          <div class="pip"></div>
          <div class="label" ${m.cls === 'now' ? 'style="color:var(--text);font-weight:600"' : ''}>${m.label}</div>
          <div class="val num">${m.val}</div>
        </div>`).join('')}
    </div>
  </div>`;
}

// ---- 雷达 ----
function radarCard() {
  return `
  <div class="card">
    <div class="card-head">
      <div class="card-title">六维度评分</div>
      <div class="card-sub" id="radarMode"></div>
    </div>
    <div id="radarChart" style="height:300px"></div>
  </div>`;
}

// ---- 核心指标 ----
function metricsCard(d) {
  const pm = d.price_metrics, fm = d.fundamental_metrics;

  const items = [
    ['年化收益', pct(pm.annual_return), dirCls(pm.annual_return)],
    ['最大回撤', pct(pm.max_drawdown), 'down'],
    ['年化波动', pct(pm.annual_volatility), ''],
    ['夏普比率', fmt(pm.sharpe), dirCls(pm.sharpe)],
    ['索提诺', fmt(pm.sortino), dirCls(pm.sortino)],
    ['卡玛', fmt(pm.calmar), dirCls(pm.calmar)],
    ['RSI(14)', fmt(pm.rsi_14, 1), ''],
    ['Beta', fmt(pm.beta), ''],
    ['Alpha', pm.alpha !== null ? pct(pm.alpha) : '—', dirCls(pm.alpha)],
  ];

  if (fm?.has_fundamentals) {
    items.push(
      ['ROE', fmt(fm.roe, 1) + '%', ''],
      ['毛利率', fmt(fm.gross_margin, 1) + '%', ''],
      ['PE (TTM)', fmt(fm.pe_ttm), ''],
      ['PB', fmt(fm.pb), ''],
      ['负债率', fmt(fm.debt_ratio, 1) + '%', ''],
      ['营收增速', fmt(fm.revenue_yoy, 1) + '%', dirCls(fm.revenue_yoy)],
      ['52周位置', pct(pm.position_52w, 0), ''],
      ['3月动量', pct(pm.mom_3m), dirCls(pm.mom_3m)],
      ['ATR', fmt(pm.atr_pct) + '%', ''],
      ['信息比率', fmt(pm.information_ratio), ''],
      ['Ulcer', fmt(pm.ulcer_index), ''],
    );
  } else {
    items.push(
      ['52周位置', pct(pm.position_52w, 0), ''],
      ['3月动量', pct(pm.mom_3m), dirCls(pm.mom_3m)],
      ['6月动量', pct(pm.mom_6m), dirCls(pm.mom_6m)],
      ['12月动量', pct(pm.mom_12m), dirCls(pm.mom_12m)],
      ['ATR', fmt(pm.atr_pct) + '%', ''],
      ['Ulcer', fmt(pm.ulcer_index), ''],
      ['日胜率', pct(pm.win_rate, 1), ''],
      ['盈亏比', fmt(pm.profit_loss_ratio), ''],
      ['信息比率', fmt(pm.information_ratio), ''],
      ['VaR(95%)', pct(pm.var_95), 'down'],
      ['CVaR', pct(pm.cvar_95), 'down'],
    );
  }

  // 补齐到 5 的整数倍，避免网格空格
  while (items.length % 5 !== 0) items.push(null);

  return `
  <div class="card">
    <div class="card-head">
      <div class="card-title">核心指标</div>
      <div class="card-sub">
        共 76 项 · <span style="cursor:pointer;color:var(--primary)"
        onclick="document.getElementById('extCard')?.scrollIntoView({behavior:'smooth'})">高级指标 ↓</span>
      </div>
    </div>
    <div class="card-pad" style="padding-top:16px">
      <div class="kv-grid">
        ${items.map(it => !it
          ? '<div class="kv" style="background:var(--surface-2)"></div>'
          : `<div class="kv"><div class="k">${esc(it[0])}</div>
              <div class="v num ${it[2]}">${it[1]}</div></div>`).join('')}
      </div>
    </div>
  </div>`;
}

// ---- 走势图 ----
function chartCard(d) {
  const pm = d.price_metrics;
  return `
  <div class="card mb-16" style="overflow:hidden">
    <div class="card-head" style="background:var(--chart-bg);border-bottom:1px solid var(--chart-border)">
      <div class="card-title" style="color:#e5e7eb">价格走势</div>
      <div class="card-sub" style="color:var(--chart-text)">
        ${esc(pm.start_date)} ~ ${esc(pm.end_date)} · ${pm.days} 个交易日 ·
        数据源 ${esc(d.meta.kline_source || '')}
      </div>
    </div>
    <div class="chart-dark"><div id="klineChart" class="chart"></div></div>
  </div>`;
}

// ---- AI ----
function aiSection(ai, meta) {
  const list = (arr, cls) => (arr && arr.length)
    ? `<ul class="${cls}">${arr.map(x => `<li>${esc(x)}</li>`).join('')}</ul>`
    : '<ul><li style="color:var(--text-3)">—</li></ul>';

  const warnings = (meta.ai_warnings || []).length
    ? alertBox('warn', `<b>输出校验：</b><br>${meta.ai_warnings.map(esc).join('<br>')}`)
    : '';

  return `
  <div class="card mb-16">
    <div class="card-head">
      <div class="card-title">AI 定性分析</div>
      <div class="card-sub">
        ${esc(meta.ai_channel || '')} · ${((meta.ai_latency_ms || 0) / 1000).toFixed(1)}s
        ${meta.ai_tokens ? '· ' + esc(meta.ai_tokens) + ' tokens' : ''}
      </div>
    </div>
    <div class="card-pad">
      ${ai.summary ? `<div style="font-size:14px;color:#1f2937;margin-bottom:14px;line-height:1.7">
        <b>结论：</b>${esc(ai.summary)}</div>` : ''}
      <div class="ai-grid">
        <div class="ai-block ai-bull">
          <h5 style="color:var(--up)">▲ 看多理由</h5>${list(ai.bull_points, 'bull')}
        </div>
        <div class="ai-block ai-bear">
          <h5 style="color:var(--down)">▼ 看空理由</h5>${list(ai.bear_points, 'bear')}
        </div>
        <div class="ai-block">
          <h5>⚡ 潜在催化剂</h5>${list(ai.catalysts, '')}
        </div>
        <div class="ai-block">
          <h5>⚠ 主要风险</h5>${list(ai.risks, '')}
        </div>
      </div>
      ${warnings ? `<div class="mt-12">${warnings}</div>` : ''}
    </div>
  </div>`;
}

function aiErrorSection(meta) {
  if (!meta.ai_error) return '';
  return alertBox('warn',
    `<b>AI 分析未完成：</b>${esc(meta.ai_error)}<br>
     <span style="font-size:11.5px">量化结果不受影响，可继续使用。</span>`);
}

// ---- 高级指标 ----
function extendedSection(ext) {
  const keys = Object.keys(ext);
  if (!keys.length) return '';

  const num = (v, d = 2) => {
    if (v === null || v === undefined) return '—';
    const n = Number(v);
    return Number.isFinite(n) ? n.toFixed(d) : String(v);
  };

  const groups = [
    ['统计特征', [
      ['Hurst指数', num(ext.hurst, 3), '>0.5 趋势持续'],
      ['收益自相关', num(ext.autocorr_1, 3), '>0 动量效应'],
      ['方差比(5日)', num(ext.variance_ratio_5, 3), '>1 趋势性'],
      ['均值回归半衰期', ext.half_life_days != null ? num(ext.half_life_days, 0) + '天' : '—', ''],
      ['JB统计量', num(ext.jarque_bera, 1), ext.is_normal_dist === false ? '非正态' : '接近正态'],
      ['最长连涨', ext.max_win_streak != null ? ext.max_win_streak + '天' : '—', ''],
      ['最长连跌', ext.max_loss_streak != null ? ext.max_loss_streak + '天' : '—', ''],
    ]],
    ['技术指标', [
      ['KDJ-K', num(ext.kdj?.k), ext.kdj?.signal || ''],
      ['KDJ-D', num(ext.kdj?.d), ''],
      ['布林 %B', num(ext.bollinger?.pct_b), '>1 突破上轨'],
      ['布林带宽', num(ext.bollinger?.bandwidth), '收窄预示变盘'],
      ['ADX', num(ext.adx?.adx), ext.adx?.trend || ''],
      ['CCI(20)', num(ext.cci_20), '>100 强势'],
      ['Williams %R', num(ext.williams_r), '>-20 超买'],
      ['量比', num(ext.volume_ratio), '>1.5 放量'],
      ['OBV', ext.obv?.obv_trend || '—', ext.obv?.divergence ? '量价背离' : ''],
    ]],
    ['相对基准', [
      ['相关性', num(ext.correlation, 3), '与基准同涨同跌'],
      ['R²', num(ext.r_squared, 3), '基准解释度'],
      ['上行捕获率', num(ext.up_capture), '基准涨1%时'],
      ['下行捕获率', num(ext.down_capture), '基准跌1%时'],
    ]],
    ['行为特征', [
      ['最强月份', ext.best_month || '—', '季节性'],
      ['最弱月份', ext.worst_month || '—', ''],
      ['最强星期', ext.best_weekday || '—', '星期效应'],
      ['最弱星期', ext.worst_weekday || '—', ''],
      ['疼痛指数', num(ext.pain_index), '回撤均方根'],
      ['恢复因子', num(ext.recovery_factor), '总收益/回撤'],
      ['尾部比率', num(ext.tail_ratio), '95分位/5分位'],
      ['M²测度', num(ext.m2), '波动率调整后'],
    ]],
  ];

  const html = groups.map(([title, rows]) => {
    const cells = rows.filter(([, v]) => v !== '—').map(([k, v, hint]) => `
      <div class="kv">
        <div class="k">${esc(k)}</div>
        <div class="v num" style="font-size:14px">${esc(v)}</div>
        ${hint ? `<div style="font-size:10px;color:var(--text-3);margin-top:1px">${esc(hint)}</div>` : ''}
      </div>`).join('');
    return cells ? `
      <div class="mb-16">
        <div class="fs-12 text-3 mb-8" style="font-weight:600;letter-spacing:.3px">${title}</div>
        <div class="kv-grid">${cells}</div>
      </div>` : '';
  }).join('');

  return `
  <div class="card mb-16" id="extCard">
    <div class="collapse-head" onclick="
      this.parentElement.classList.toggle('open');
      this.nextElementSibling.classList.toggle('hidden')">
      <div>
        <div class="card-title">高级指标</div>
        <div class="card-sub" style="margin-top:2px">
          统计特征 / 技术指标 / 相对基准 / 行为特征 · 共 ${keys.length} 项
        </div>
      </div>
      <svg class="collapse-arrow" width="16" height="16" viewBox="0 0 24 24"
           fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg>
    </div>
    <div class="hidden"><div class="card-pad">${html}</div></div>
  </div>`;
}

function disclaimer() {
  return `<div class="disclaimer">
    <b>用途声明：</b>本软件为个人科学研究工具，所有输出均为基于公开数据的量化计算结果与 AI 研究笔记，
    不构成对外投资建议。目标价与买卖点为算法计算结果，请自行判断风险。
  </div>`;
}

// ============================================================
// 图表
// ============================================================
function drawCharts(d) {
  // ---- 雷达 ----
  const radarEl = document.getElementById('radarChart');
  if (radarEl && window.echarts) {
    const dims = d.final.dimensions || {};
    const labels = {
      valuation: '估值', growth: '成长', quality: '质量',
      momentum: '动量', risk: '风险', sentiment: 'AI情绪',
    };
    const radar = echarts.init(radarEl);
    radar.setOption({
      radar: {
        indicator: Object.keys(labels).map(k => ({ name: labels[k], max: 100 })),
        radius: '56%',
        axisName: { color: '#6b7280', fontSize: 11 },
        splitLine: { lineStyle: { color: '#eef0f3' } },
        splitArea: { areaStyle: { color: ['#fafbfc', '#ffffff'] } },
        axisLine: { lineStyle: { color: '#eef0f3' } },
      },
      tooltip: {},
      series: [{
        type: 'radar', symbolSize: 4,
        data: [{
          value: Object.keys(labels).map(k => dims[k] ?? 0),
          areaStyle: { color: 'rgba(79,70,229,.16)' },
          lineStyle: { color: '#4f46e5', width: 2 },
          itemStyle: { color: '#4f46e5' },
        }],
      }],
    });
    charts.radar = radar;
  }

  // ---- K 线 ----
  const klineEl = document.getElementById('klineChart');
  if (klineEl && window.echarts && d.price_series?.length) {
    const series = d.price_series;
    const f = d.final;

    const marks = [
      f.current_price ? { yAxis: f.current_price,
        lineStyle: { color: '#e2e8f0' },
        label: { formatter: '现价', position: 'insideEndTop', color: '#cbd5e1', fontSize: 10 } } : null,
      f.target_price ? { yAxis: f.target_price,
        lineStyle: { color: '#f87171', type: 'dashed' },
        label: { formatter: '目标', position: 'insideEndTop', color: '#f87171', fontSize: 10 } } : null,
      f.stop_loss ? { yAxis: f.stop_loss,
        lineStyle: { color: '#34d399', type: 'dashed' },
        label: { formatter: '止损', position: 'insideEndTop', color: '#34d399', fontSize: 10 } } : null,
    ].filter(Boolean);

    const kline = echarts.init(klineEl);
    kline.setOption({
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(15,22,32,.95)',
        borderColor: '#1e2938',
        textStyle: { color: '#e5e7eb', fontSize: 12 },
        axisPointer: { lineStyle: { color: '#334155' } },
        formatter: p => `${p[0].axisValue}<br/><b>${thousand(p[0].data)}</b>`,
      },
      grid: { left: 58, right: 26, top: 18, bottom: 28 },
      xAxis: {
        type: 'category', data: series.map(x => x.date), boundaryGap: false,
        axisLabel: { color: '#64748b', fontSize: 10.5, hideOverlap: true },
        axisLine: { lineStyle: { color: '#1e2938' } },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value', scale: true,
        axisLabel: { color: '#64748b', fontSize: 10.5 },
        splitLine: { lineStyle: { color: '#1a2330' } },
      },
      dataZoom: [{ type: 'inside' }],
      series: [{
        type: 'line', showSymbol: false,
        data: series.map(x => x.close),
        lineStyle: { width: 1.8, color: '#818cf8' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(129,140,248,.28)' },
            { offset: 1, color: 'rgba(129,140,248,.02)' },
          ]),
        },
        markLine: { silent: true, symbol: 'none', data: marks },
      }],
    });
    charts.kline = kline;
  }

  if (!window.__qtResizeBound) {
    window.__qtResizeBound = true;
    window.addEventListener('resize', () => {
      Object.values(charts).forEach(c => { try { c.resize(); } catch (_) {} });
    });
  }
}
