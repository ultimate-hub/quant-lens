/* ============================================================
   走势推演 + 配仓建议（个股分析页的附加区块）
   ------------------------------------------------------------
   数据来自 GET /api/v2/outlook/{symbol}
   推演 = 蒙特卡洛自举模拟（不是预测，图上会写明）
   建议 = 规则引擎（每条都带依据数字）
   ============================================================ */

import { esc, fmt, pct, thousand } from '../shared.js';

let chart = null;

/** 挂载：先占位，数据回来后填充 */
export function mountPlaceholder(root) {
  const el = root.querySelector('#outlookWrap');
  if (!el) return;
  el.innerHTML = `
    <div class="card mb-16">
      <div class="card-head">
        <div class="card-title">走势推演与配仓建议</div>
        <div class="card-sub">正在模拟未来路径…</div>
      </div>
      <div class="card-pad">
        <div class="loading"><div class="spinner"></div>
          <span>蒙特卡洛模拟中（1000 条路径）…</span></div>
      </div>
    </div>`;
}

export function renderError(root, msg) {
  const el = root.querySelector('#outlookWrap');
  if (!el) return;
  el.innerHTML = `
    <div class="card mb-16"><div class="card-pad">
      <div class="alert alert-warn">
        <b>走势推演未完成：</b>${esc(msg)}<br>
        <span style="font-size:11.5px">量化和评分结果不受影响。</span>
      </div>
    </div></div>`;
}

/** 主渲染 */
export function renderOutlook(root, data, ctx) {
  const el = root.querySelector('#outlookWrap');
  if (!el) return;

  const fc = data.forecast;
  const adv = data.advice;
  const scen = data.scenarios || [];

  el.innerHTML = `
    ${forecastCard(fc, scen)}
    ${adviceCard(adv)}`;

  drawFanChart(fc);
}

// ============================================================
// 推演卡片（扇形图）
// ============================================================
function forecastCard(fc, scen) {
  const start = fc.start_price;
  const medianEnd = fc.bands.p50[fc.bands.p50.length - 1];
  const retCls = fc.median_return > 0 ? 'up' : fc.median_return < 0 ? 'down' : '';

  const probBar = (label, p, color) => `
    <div style="margin-bottom:8px">
      <div class="flex between fs-12" style="margin-bottom:4px">
        <span class="text-2">${esc(label)}</span>
        <b class="num" style="color:${color}">${(p * 100).toFixed(1)}%</b>
      </div>
      <div style="height:5px;background:var(--surface-3);border-radius:3px;overflow:hidden">
        <div style="height:100%;width:${Math.min(p * 100, 100)}%;background:${color};
             border-radius:3px"></div>
      </div>
    </div>`;

  return `
  <div class="card mb-16" style="overflow:hidden">
    <div class="card-head" style="background:var(--chart-bg);border-bottom:1px solid var(--chart-border)">
      <div>
        <div class="card-title" style="color:#e5e7eb">
          走势推演 · 未来 ${fc.days} 个交易日
        </div>
        <div class="card-sub" style="color:var(--chart-text)">
          蒙特卡洛自举模拟 ${fc.sims} 条路径 · 块长 ${fc.block} 日 · 基于 ${fc.history_days} 天历史
        </div>
      </div>
      <span class="tag tag-neutral" style="background:#1e2938;color:#94a3b8">
        非价格预测
      </span>
    </div>

    <div class="chart-dark"><div id="fanChart" class="chart" style="height:320px"></div></div>

    <div class="card-pad" style="border-top:1px solid var(--border)">
      <div class="grid-3" style="gap:12px">
        ${scen.map(s => `
          <div style="padding:12px 14px;border-radius:11px;background:var(--surface-2)">
            <div class="fs-11 text-3">${esc(s.name)}情景（${fc.days} 日）</div>
            <div class="num fw-700 mt-8" style="font-size:17px">${thousand(s.price)}</div>
            <div class="fs-12 num mt-8 ${s.ret > 0 ? 'up' : s.ret < 0 ? 'down' : 'text-3'}">
              ${s.ret > 0 ? '+' : ''}${(s.ret * 100).toFixed(1)}%
            </div>
            <div class="fs-11 text-3 mt-8">${esc(s.prob)}</div>
          </div>`).join('')}
      </div>

      <div class="grid-2 mt-16" style="grid-template-columns:1fr 1fr">
        <div>
          <div class="fs-11 text-3 mb-8" style="font-weight:600;letter-spacing:.3px">概率</div>
          ${probBar('上涨概率', fc.prob_up, 'var(--up)')}
          ${fc.prob_hit_target != null
            ? probBar('触及目标价', fc.prob_hit_target, '#4f46e5') : ''}
          ${fc.prob_hit_stop != null
            ? probBar('触及止损价', fc.prob_hit_stop, 'var(--down)') : ''}
        </div>
        <div>
          <div class="fs-11 text-3 mb-8" style="font-weight:600;letter-spacing:.3px">
            先到哪边（${fc.sims} 条路径里）
          </div>
          ${fc.first_hit ? `
            <div class="fs-13" style="line-height:2">
              <div>先到目标价：<b class="num up">${fc.first_hit.target_first}</b> 条</div>
              <div>先到止损价：<b class="num down">${fc.first_hit.stop_first}</b> 条</div>
              <div>两边都没碰到：<b class="num text-3">${fc.first_hit.neither}</b> 条</div>
            </div>` : '<div class="fs-12 text-3">未设置目标价/止损价，无法统计</div>'}
        </div>
      </div>

      <div class="alert alert-warn mt-16" style="font-size:11.5px">
        <b>这不是价格预测。</b>格子里的数字是"如果未来日收益分布和过去相似，
        价格可能落在哪里"。它不考虑政策、业绩突变、流动性枯竭。
        把区间当参考，不要当承诺。
      </div>
    </div>
  </div>`;
}

// ============================================================
// 建议卡片
// ============================================================
function adviceCard(adv) {
  const toneColor = {
    good: 'var(--down)', info: 'var(--primary)',
    warn: 'var(--warn)', danger: 'var(--up)',
  }[adv.action_tone] || 'var(--primary)';

  const levelIcon = { good: '▲', info: '·', warn: '!', danger: '✕' };
  const levelColor = {
    good: 'var(--down)', info: 'var(--text-2)',
    warn: 'var(--warn)', danger: 'var(--up)',
  };

  const items = (adv.items || []).map(it => `
    <div style="display:flex;gap:9px;padding:9px 0;border-bottom:1px solid var(--border)">
      <span style="color:${levelColor[it.level] || 'var(--text-3)'};font-size:12px;
                   width:12px;flex-shrink:0;text-align:center">${levelIcon[it.level] || '·'}</span>
      <div style="flex:1;min-width:0">
        <div class="fs-13" style="font-weight:600;line-height:1.6">${esc(it.title)}</div>
        <div class="fs-12 text-2 mt-8" style="line-height:1.65">${esc(it.detail)}</div>
        ${it.basis ? `<div class="fs-11 text-3 mt-8">依据：${esc(it.basis)}</div>` : ''}
      </div>
    </div>`).join('');

  const plan = (adv.plan || []).length ? `
    <div>
      <div class="fs-11 text-3 mb-8" style="font-weight:600;letter-spacing:.3px">分批计划</div>
      <table class="table" style="font-size:12.5px">
        <thead><tr><th>档位</th><th>价位</th><th>占总仓</th><th>实际仓位</th><th>说明</th></tr></thead>
        <tbody>
          ${adv.plan.map(p => `
            <tr>
              <td>第 ${p.tier} 档</td>
              <td class="num fw-700">${thousand(p.price)}</td>
              <td class="num">${(p.ratio * 100).toFixed(0)}%</td>
              <td class="num">${p.amount_pct}%</td>
              <td class="fs-12 text-2">${esc(p.note)}</td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>` : '';

  const triggers = (adv.triggers || []).length ? `
    <div class="mt-16">
      <div class="fs-11 text-3 mb-8" style="font-weight:600;letter-spacing:.3px">触发条件</div>
      ${adv.triggers.map(t => {
        const c = t.type === 'stop' ? 'var(--down)'
          : t.type === 'target' ? 'var(--up)' : 'var(--primary)';
        const label = t.type === 'stop' ? '止损' : t.type === 'target' ? '止盈' : '回踩';
        return `
        <div class="flex center gap-8" style="padding:7px 0;font-size:12.5px">
          <span class="tag" style="background:${c}1a;color:${c}">${label}</span>
          <b class="num">${thousand(t.price)}</b>
          <span class="text-2">${esc(t.action)}</span>
        </div>`;
      }).join('')}
    </div>` : '';

  return `
  <div class="card mb-16">
    <div class="card-head">
      <div>
        <div class="card-title">配仓建议</div>
        <div class="card-sub" style="margin-top:2px">
          规则引擎生成 · 每条都附依据，可逐条核对
        </div>
      </div>
      <div class="flex center gap-12">
        <div style="text-align:right">
          <div class="fs-11 text-3">目标仓位</div>
          <div class="num fw-700" style="font-size:19px">${adv.position_pct}%</div>
        </div>
        <span class="tag" style="background:${toneColor}1a;color:${toneColor};
              font-size:13px;padding:6px 14px">${esc(adv.action)}</span>
      </div>
    </div>

    <div class="card-pad">
      <div class="fs-14" style="line-height:1.75;color:#1f2937;margin-bottom:16px">
        ${esc(adv.summary)}
      </div>

      <div class="grid-2" style="grid-template-columns:1fr 1fr;gap:20px;align-items:start">
        <div>
          <div class="fs-11 text-3 mb-8" style="font-weight:600;letter-spacing:.3px">判断依据</div>
          ${items || '<div class="fs-12 text-3">暂无</div>'}
        </div>
        <div>
          ${plan}
          ${triggers}
        </div>
      </div>

      <div class="fs-11 text-3 mt-16" style="line-height:1.7;padding-top:12px;
           border-top:1px solid var(--border)">
        ${esc(adv.disclaimer)}
      </div>
    </div>
  </div>`;
}

// ============================================================
// 扇形图
// ============================================================
function drawFanChart(fc) {
  const el = document.getElementById('fanChart');
  if (!el || !window.echarts) return;

  if (chart) { try { chart.dispose(); } catch (_) {} }

  const dates = fc.dates;
  const b = fc.bands;

  // 历史最后一个点 + 未来各分位
  const p05 = [fc.start_price, ...b.p05];
  const p25 = [fc.start_price, ...b.p25];
  const p50 = [fc.start_price, ...b.p50];
  const p75 = [fc.start_price, ...b.p75];
  const p95 = [fc.start_price, ...b.p95];

  // 用叠加面积画区间带
  const bandOuter = p05.map((v, i) => +(p95[i] - v).toFixed(4));
  const bandInner = p25.map((v, i) => +(p75[i] - v).toFixed(4));

  const splitIndex = 1;   // 0 是历史锚点，1 起是未来

  chart = echarts.init(el);
  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(15,22,32,.95)',
      borderColor: '#1e2938',
      textStyle: { color: '#e5e7eb', fontSize: 12 },
      axisPointer: { lineStyle: { color: '#334155' } },
      formatter: (ps) => {
        const i = ps[0].dataIndex;
        if (i === 0) return `${dates[0]}<br/>现价 <b>${thousand(fc.start_price)}</b>`;
        return `${dates[i]}<br/>
          中位 <b style="color:#a5b4fc">${thousand(p50[i])}</b><br/>
          50% 区间 ${thousand(p25[i])} ~ ${thousand(p75[i])}<br/>
          90% 区间 ${thousand(p05[i])} ~ ${thousand(p95[i])}`;
      },
    },
    grid: { left: 62, right: 26, top: 26, bottom: 34 },
    xAxis: {
      type: 'category', data: dates, boundaryGap: false,
      axisLabel: { color: '#64748b', fontSize: 10.5, hideOverlap: true },
      axisLine: { lineStyle: { color: '#1e2938' } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value', scale: true,
      axisLabel: { color: '#64748b', fontSize: 10.5 },
      splitLine: { lineStyle: { color: '#1a2330' } },
    },
    series: [
      // 90% 区间（浅）
      {
        name: 'p05', type: 'line', stack: 'band90', data: p05,
        lineStyle: { opacity: 0 }, symbol: 'none', silent: true, z: 1,
      },
      {
        name: '90% 区间', type: 'line', stack: 'band90', data: bandOuter,
        lineStyle: { opacity: 0 }, symbol: 'none', silent: true, z: 1,
        areaStyle: { color: 'rgba(129,140,248,.13)' },
      },
      // 50% 区间（深）
      {
        name: 'p25', type: 'line', stack: 'band50', data: p25,
        lineStyle: { opacity: 0 }, symbol: 'none', silent: true, z: 2,
      },
      {
        name: '50% 区间', type: 'line', stack: 'band50', data: bandInner,
        lineStyle: { opacity: 0 }, symbol: 'none', silent: true, z: 2,
        areaStyle: { color: 'rgba(129,140,248,.28)' },
      },
      // 中位线
      {
        name: '中位路径', type: 'line', data: p50, z: 5,
        symbol: 'none',
        lineStyle: { width: 2, color: '#a5b4fc' },
        markLine: {
          silent: true, symbol: 'none',
          label: { fontSize: 10, color: '#94a3b8' },
          data: [
            { xAxis: dates[0], lineStyle: { color: '#475569', type: 'dashed' },
              label: { formatter: '今天', position: 'insideEndTop' } },
          ],
        },
      },
    ],
  });
}

export function dispose() {
  if (chart) { try { chart.dispose(); } catch (_) {} chart = null; }
}