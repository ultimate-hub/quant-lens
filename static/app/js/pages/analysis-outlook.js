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
    ${tsCard(data.ts)}
    ${adviceCard(adv)}`;

  drawFanChart(fc, data.ts);

  el.querySelector('#btnCalib')?.addEventListener('click', (e) => {
    runCalibration(e.target, data.symbol, fc.days);
  });
}

// ============================================================
// 校准校验
// ============================================================
async function runCalibration(btn, symbol, days) {
  const box = document.getElementById('calibBox');
  if (!box) return;

  btn.disabled = true;
  btn.textContent = '校验中…';
  box.innerHTML = `<div class="loading" style="padding:20px">
    <div class="spinner"></div><span>正在做走查回测…</span></div>`;

  try {
    const r = await fetch(
      `/api/v2/forecast/${encodeURIComponent(symbol)}/calibration`
      + `?days=${days}&sims=300&tests=12`).then(x => x.json());

    if (r.ok === false) {
      box.innerHTML = `<div class="alert alert-warn" style="font-size:12px">
        ${esc(r.reason || '校验失败')}</div>`;
    } else {
      box.innerHTML = calibrationResult(r);
    }
  } catch (err) {
    box.innerHTML = `<div class="alert alert-error" style="font-size:12px">
      校验失败：${esc(err.message)}</div>`;
  }

  btn.disabled = false;
  btn.textContent = '重新校验';
}

function calibrationResult(r) {
  const bar = (label, rate, expect, verdict) => {
    const ok = Math.abs(rate - expect) <= 0.12;
    const color = ok ? 'var(--down)' : 'var(--warn)';
    return `
    <div style="margin-bottom:10px">
      <div class="flex between fs-12" style="margin-bottom:4px">
        <span class="text-2">${esc(label)}</span>
        <span><b class="num" style="color:${color}">${(rate * 100).toFixed(0)}%</b>
          <span class="text-3">/ 预期 ${(expect * 100).toFixed(0)}%</span>
          <span class="tag ${ok ? 'tag-down' : 'tag-warn'}" style="margin-left:6px">
            ${esc(verdict)}</span></span>
      </div>
      <div style="position:relative;height:7px;background:var(--surface-3);
                  border-radius:4px;overflow:hidden">
        <div style="height:100%;width:${Math.min(rate * 100, 100)}%;
             background:${color};border-radius:4px"></div>
        <div style="position:absolute;top:-2px;left:${expect * 100}%;
             width:2px;height:11px;background:var(--text)"></div>
      </div>
    </div>`;
  };

  const rows = (r.details || []).slice(0, 8).map(d => `
    <tr>
      <td class="fs-12">${esc(d.cutoff)}</td>
      <td class="num fs-12">${thousand(d.start)}</td>
      <td class="num fs-12">${thousand(d.actual)}</td>
      <td class="num fs-11">${thousand(d.band50[0])}~${thousand(d.band50[1])}</td>
      <td class="num fs-11">${thousand(d.band90[0])}~${thousand(d.band90[1])}</td>
      <td style="color:${d.in50 ? 'var(--down)' : 'var(--up)'}">${d.in50 ? '✓' : '✗'}</td>
      <td style="color:${d.in90 ? 'var(--down)' : 'var(--up)'}">${d.in90 ? '✓' : '✗'}</td>
    </tr>`).join('');

  return `
    <div style="padding:14px 16px;border-radius:11px;background:var(--surface-2)">
      <div class="fs-12 fw-600 mb-12">
        过去 ${r.tests} 次回测（每次提前 ${r.days} 个交易日推演，与真实价对照）
      </div>
      ${bar('50% 区间', r.hit50, r.expected50, r.verdict50)}
      ${bar('90% 区间', r.hit90, r.expected90, r.verdict90)}
      <div class="fs-11 text-3 mt-12" style="line-height:1.7">${esc(r.how_to_read)}</div>
    </div>

    <div class="table-wrap mt-12">
      <table class="table" style="font-size:12px">
        <thead><tr>
          <th>截止日</th><th>起点</th><th>实际</th>
          <th>50% 区间</th><th>90% 区间</th><th>50%</th><th>90%</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
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

    <div class="chart-dark"><div id="fanChart" class="chart" style="height:300px"></div></div>

    <div class="card-pad" style="border-top:1px solid var(--border)">
      ${rangeBlock(fc)}

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

      ${volModelRow(fc)}

      <div class="mt-16" style="padding-top:14px;border-top:1px solid var(--border)">
        <div class="flex between center" style="flex-wrap:wrap;gap:8px">
          <div>
            <div class="fs-12 fw-600">置信度校验</div>
            <div class="fs-11 text-3">用历史回测检验这个区间到底罩不罩得住真实价</div>
          </div>
          <button class="btn ghost sm" id="btnCalib">校验模型（约 15 秒）</button>
        </div>
        <div id="calibBox" class="mt-12"></div>
      </div>
    </div>
  </div>`;
}

/** 波动率模型一行 */
function volModelRow(fc) {
  const v = fc.vol_model;
  if (!v || !v.ok) {
    return `<div class="fs-11 text-3 mt-12">
      波动率模型未启用${v?.reason ? '（' + esc(v.reason) + '）' : ''}，区间按历史平均波动绘制。
    </div>`;
  }
  const ratio = v.vol_ratio ?? 1;
  const state = ratio > 1.1 ? '当前波动高于常态'
    : ratio < 0.9 ? '当前波动低于常态' : '当前波动接近常态';

  return `
  <div class="mt-16" style="padding:12px 14px;border-radius:11px;background:var(--surface-2)">
    <div class="flex between center" style="flex-wrap:wrap;gap:8px">
      <div>
        <div class="fs-12 fw-600">波动率模型：${esc(v.method)}</div>
        <div class="fs-11 text-3" style="margin-top:3px">
          ${esc(state)}（${ratio.toFixed(2)}× 同窗口均值）·
          持续度 ${(v.persistence ?? 0).toFixed(3)} ·
          冲击半衰期 ${v.half_life ?? '—'} 个交易日
        </div>
      </div>
      <div style="text-align:right">
        <div class="fs-11 text-3">当前条件波动</div>
        <div class="num fw-700" style="font-size:16px">
          ${((v.cond_vol ?? 0) * 100).toFixed(1)}%
        </div>
        <div class="fs-11 text-3">
          同窗口均值 ${((v.uncond_vol ?? 0) * 100).toFixed(1)}%
        </div>
      </div>
    </div>
    <div class="fs-11 text-3" style="margin-top:8px;line-height:1.6">
      α=${v.alpha} β=${v.beta}，模拟时按 ${v.scale_applied}× 缩放了收益幅度 ——
      高波动期区间会变宽，低波动期会收窄。<br>
      注：这里的波动率基于上面推演所用的那一段历史（${fc.history_days} 个交易日），
      与「核心指标」里的年化波动（全部历史）口径不同，不要直接对比。
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
// 区间展示（区间要显示成区间，而不是三个单点）
// ============================================================
function rangeBlock(fc) {
  const b = fc.bands;
  const start = fc.start_price;
  const lo90 = b.p05[b.p05.length - 1];
  const hi90 = b.p95[b.p95.length - 1];
  const lo50 = b.p25[b.p25.length - 1];
  const hi50 = b.p75[b.p75.length - 1];
  const mid = b.p50[b.p50.length - 1];

  // 以 90% 区间为画布，把 50% 区间和中位按比例放进去
  const span = Math.max(hi90 - lo90, 1);
  const posOf = (v) => Math.max(0, Math.min(100, (v - lo90) / span * 100));

  const pct = (v) => {
    const r = (v / start - 1) * 100;
    return `${r > 0 ? '+' : ''}${r.toFixed(1)}%`;
  };

  const band = (lo, hi, color) => {
    const left = posOf(lo);
    const width = Math.max(posOf(hi) - left, 1.5);
    return `<div style="position:absolute;left:${left}%;width:${width}%;height:22px;
            background:${color};border-radius:4px;top:50%;transform:translateY(-50%)"></div>`;
  };

  return `
  <div style="padding:14px 16px;background:var(--surface-2);border-radius:12px">
    <div class="flex between center mb-12">
      <div class="fs-12 fw-600">${fc.days} 个交易日后的价格区间</div>
      <div class="fs-11 text-3">起点 ${thousand(start)}</div>
    </div>

    <div style="position:relative;height:40px;margin:0 4px 10px">
      ${band(lo90, hi90, 'rgba(79,70,229,.14)')}
      ${band(lo50, hi50, 'rgba(79,70,229,.30)')}
      <div style="position:absolute;left:${posOf(mid)}%;top:2px;bottom:2px;width:2px;
           background:var(--primary);transform:translateX(-1px)"></div>
    </div>

    <div class="grid-3" style="gap:10px">
      <div style="padding:10px 12px;border-radius:10px;background:#fff;
                  border:1px solid var(--border)">
        <div class="fs-11 text-3">90% 区间（5% ~ 95%）</div>
        <div class="num fw-700 mt-8" style="font-size:14px">
          ${thousand(lo90)} <span class="text-3">~</span> ${thousand(hi90)}
        </div>
        <div class="fs-11 text-3 mt-8">${pct(lo90)} ~ ${pct(hi90)}</div>
      </div>

      <div style="padding:10px 12px;border-radius:10px;background:#fff;
                  border:1px solid var(--border)">
        <div class="fs-11 text-3">50% 区间（25% ~ 75%）</div>
        <div class="num fw-700 mt-8" style="font-size:14px">
          ${thousand(lo50)} <span class="text-3">~</span> ${thousand(hi50)}
        </div>
        <div class="fs-11 text-3 mt-8">${pct(lo50)} ~ ${pct(hi50)}</div>
      </div>

      <div style="padding:10px 12px;border-radius:10px;background:#fff;
                  border:1px solid var(--border)">
        <div class="fs-11 text-3">中位路径（50% 分位）</div>
        <div class="num fw-700 mt-8" style="font-size:14px">${thousand(mid)}</div>
        <div class="fs-11 ${mid >= start ? 'up' : 'down'} mt-8">${pct(mid)}</div>
      </div>
    </div>
  </div>`;
}

// ============================================================
// 时序模型对比
// ============================================================
function tsCard(ts) {
  if (!ts || ts.error || !ts.backtest) {
    return `<div class="card mb-16"><div class="card-pad">
      <div class="fs-12 text-3">时序模型对比暂不可用${ts?.error ? '：' + esc(ts.error) : ''}</div>
    </div></div>`;
  }

  const bt = ts.backtest;
  if (!bt.ok) {
    return `<div class="card mb-16"><div class="card-pad">
      <div class="fs-12 text-3">${esc(bt.reason || '无法回测')}</div>
    </div></div>`;
  }

  const rows = bt.models.map(m => {
    const isBest = m.key === bt.best;
    const isBase = m.key === 'naive';
    const v = m.vs_naive;
    const cls = v > 0.5 ? 'up' : v < -0.5 ? 'down' : 'text-3';
    return `
    <tr style="${isBest ? 'background:var(--primary-soft)' : ''}">
      <td>
        <span class="${isBest ? 'fw-600' : ''}">${esc(m.name)}</span>
        ${isBase ? '<span class="tag tag-neutral" style="margin-left:6px;font-size:10px">基准</span>' : ''}
        ${isBest && !isBase ? '<span class="tag tag-primary" style="margin-left:6px;font-size:10px">最优</span>' : ''}
      </td>
      <td class="num fs-12">${m.mae.toFixed(2)}</td>
      <td class="num fs-12">${m.mape.toFixed(2)}%</td>
      <td class="num fs-12">${m.dir_hit == null ? '—' : m.dir_hit.toFixed(0) + '%'}</td>
      <td class="num fs-12 ${cls}">${isBase ? '—' : (v > 0 ? '+' : '') + v.toFixed(1) + '%'}</td>
    </tr>`;
  }).join('');

  return `
  <div class="card mb-16">
    <div class="card-head">
      <div>
        <div class="card-title">时序模型对比</div>
        <div class="card-sub" style="margin-top:2px">
          ${bt.windows} 个检验窗口的样本外误差 · 与「随机游走」基准对比
        </div>
      </div>
      <span class="tag ${bt.best_usable ? 'tag-down' : 'tag-warn'}">
        ${bt.best_usable ? '有模型优于基准' : '未跑赢基准'}
      </span>
    </div>
    <div class="card-pad" style="padding:14px 18px">
      <div class="table-wrap">
        <table class="table" style="font-size:12.5px">
          <thead><tr>
            <th>模型</th><th>MAE</th><th>MAPE</th><th>方向命中</th><th>vs 基准</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>

      <div class="alert ${bt.best_usable ? 'alert-info' : 'alert-warn'} mt-12" style="font-size:12px">
        ${esc(bt.conclusion)}
      </div>

      <div class="fs-11 text-3 mt-12" style="line-height:1.7">
        MAE = 平均绝对误差（越小越好）· MAPE = 平均绝对百分比误差 ·
        方向命中 = 涨跌方向猜对的占比（随机游走永远预测持平，不参与统计）·
        「vs 基准」为正表示比随机游走更准。<br>
        <b>注意：模型排名依赖样本区间。</b>同一只股票换一段时间回测，
        排名可能完全不同 —— 这也是为什么只用它做参考，不用它做决策。
      </div>
    </div>
  </div>`;
}

// ============================================================
// 扇形图
// ============================================================
function drawFanChart(fc, ts) {
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
    // 单条时序模型路径（只有它在回测里真的跑赢基准时才画）
      // 不赢基准还画出来，就等于拿一条没有依据的线误导人
      ...(ts && ts.backtest?.ok && ts.backtest.best_usable && ts.paths?.[ts.backtest.best]
        ? [{
            name: ts.labels?.[ts.backtest.best] || '时序模型',
            type: 'line',
            data: ts.paths[ts.backtest.best],
            symbol: 'none',
            z: 6,
            lineStyle: { width: 1.6, color: '#fbbf24', type: 'dashed' },
          }]
        : []),
    ],
  });
}

export function dispose() {
  if (chart) { try { chart.dispose(); } catch (_) {} chart = null; }
}