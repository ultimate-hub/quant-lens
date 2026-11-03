/* ============================================================
   新闻情报
   ------------------------------------------------------------
   关键词 → 4 源聚合 → 去重 → AI 归纳成结构化情报
   AI 不可用时后端自动走规则引擎，页面无需特殊处理
   ============================================================ */

import {
  esc, api, state, navigate, pageHead, alertBox, saveLocal, loadLocal, debounce,
} from '../shared.js';

let lastQuery = '';
let lastBundle = null;

export async function render(root, ctx) {
  const history = loadLocal('news_history', []);

  root.innerHTML = `
    ${pageHead('新闻情报', '关键词检索，聚合东财 / 华尔街见闻 / 新浪 / Google News',
      `<span class="fs-12 text-3" id="newsMeta"></span>`)}

    <div class="card mb-16">
      <div class="card-pad">
        <div class="flex center gap-8" style="flex-wrap:wrap">
          <div class="search" style="flex:1;min-width:260px;max-width:none">
            <svg class="search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
            <input type="text" id="newsInput" placeholder="输入关键词，例如：半导体、白酒、光伏、降息">
          </div>
          <select class="select" id="newsDays" style="width:auto;min-width:120px">
            <option value="1">近 1 天</option>
            <option value="3">近 3 天</option>
            <option value="7" selected>近 7 天</option>
            <option value="30">近 30 天</option>
          </select>
          <label class="flex center gap-6 fs-12 text-2" style="cursor:pointer;white-space:nowrap">
            <input type="checkbox" class="cb" id="useAI"
                   ${state.channels.length ? 'checked' : ''}>
            AI 归纳
          </label>
          <button class="btn" id="btnNews">检索</button>
        </div>

        <div class="chips mt-12" id="quickWords"></div>
      </div>
    </div>

    <div id="newsBody">${emptyHint()}</div>`;

  // 快捷词：板块 + 历史
  renderQuickWords(root, ctx, history);

  root.querySelector('#btnNews').addEventListener('click', () => runSearch(root, ctx));
  root.querySelector('#newsInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') runSearch(root, ctx);
  });

  // 支持 #/news?q=半导体 直达并自动检索
  const preset = ctx.params?.q;
  if (preset) {
    root.querySelector('#newsInput').value = preset;
    if (ctx.params?.days) root.querySelector('#newsDays').value = ctx.params.days;
    await runSearch(root, ctx);
  } else if (history.length) {
    root.querySelector('#newsInput').value = history[0];
  }
}

function emptyHint() {
  return `
  <div class="card"><div class="empty">
    <div class="empty-title">输入关键词开始检索</div>
    <div class="empty-desc">
      输入板块名（半导体、白酒）或事件词（降息、重组）<br>
      也可以点上面的快捷词
    </div>
  </div></div>`;
}

function renderQuickWords(root, ctx, history) {
  const box = root.querySelector('#quickWords');
  const sectors = (state.sectors || []).slice(0, 10);
  const recent = (history || []).slice(0, 4);

  box.innerHTML =
    (recent.length ? `<span class="fs-11 text-3" style="align-self:center;margin-right:4px">最近</span>` : '') +
    recent.map(w => `<span class="chip" data-w="${esc(w)}">🕐 ${esc(w)}</span>`).join('') +
    sectors.map(s => `<span class="chip" data-w="${esc(s)}">${esc(s)}</span>`).join('');

  box.querySelectorAll('.chip').forEach(chip => {
    chip.addEventListener('click', () => {
      root.querySelector('#newsInput').value = chip.dataset.w;
      runSearch(root, ctx);
    });
  });
}

// ============================================================
// 检索
// ============================================================
async function runSearch(root, ctx) {
  const input = root.querySelector('#newsInput');
  const q = input.value.trim();
  if (!q) { ctx.toast('先输入关键词', 'warn'); return; }

  const days = Number(root.querySelector('#newsDays').value) || 7;
  const useAI = root.querySelector('#useAI').checked;

  const btn = root.querySelector('#btnNews');
  const body = root.querySelector('#newsBody');
  btn.disabled = true; btn.textContent = '检索中…';

  body.innerHTML = `<div class="card"><div class="card-pad">
    <div class="loading"><div class="spinner"></div>
      <span>正在抓取多个来源${useAI ? '并做 AI 归纳' : ''}…（约 5-20 秒）</span></div>
  </div></div>`;

  try {
    let d;
    if (useAI) {
      // 分开请求：
      //   新闻列表要 20 条（给人看）
      //   给 AI 归纳的只送 8 条 —— 送太多会导致输出过长、超时或解析失败，
      //   后端会静默降级成规则引擎，看起来像「AI 没工作」
      const [listRes, sumRes] = await Promise.all([
        api.get('/api/v2/news', { q, days, limit: 20 }),
        api.get('/api/v2/news/summary', {
          q, days, limit: 8, use_ai: true,
          channel_id: state.channels[0]?.id,
        }),
      ]);
      d = { ...listRes, summary: sumRes.summary };
    } else {
      d = await api.get('/api/v2/news', { q, days, limit: 20 });
    }

    lastQuery = q;
    lastBundle = d;

    // 记录历史
    const hist = loadLocal('news_history', []).filter(x => x !== q);
    hist.unshift(q);
    saveLocal('news_history', hist.slice(0, 8));
    renderQuickWords(root, ctx, hist);

    renderResult(root, ctx, d, q, useAI);
  } catch (err) {
    body.innerHTML = alertBox('error', `检索失败：${esc(err.message)}`);
  }

  btn.disabled = false; btn.textContent = '检索';
}

// ============================================================
// 结果
// ============================================================
function renderResult(root, ctx, d, q, useAI) {
  const body = root.querySelector('#newsBody');
  const items = d.items || [];
  const summary = d.summary || null;

  const meta = root.querySelector('#newsMeta');
  if (meta) {
    // sources 是 {来源名: 条数} 的字典，不是数组
    const srcCount = Array.isArray(d.sources)
      ? d.sources.length
      : Object.keys(d.sources || {}).length;
    meta.textContent = `${items.length} 条 · ${srcCount} 个来源` +
      (d.cached ? ' · 缓存命中' : '');
  }

  body.innerHTML = `
    ${summary ? summaryCard(summary, d) : ''}
    ${items.length ? newsList(items, q) : `
      <div class="card"><div class="empty">
        <div class="empty-title">没有找到相关新闻</div>
        <div class="empty-desc">换个关键词，或把时间范围放宽到 30 天</div>
      </div></div>`}`;

  // 相关个股可点击
  body.querySelectorAll('[data-sym]').forEach(n => {
    n.addEventListener('click', () => navigate('analysis', { symbol: n.dataset.sym }));
  });
}

function summaryCard(s, bundle) {
  const senti = s.overall_sentiment || '中性';
  const score = s.sentiment_score ?? 0;
  const isRule = s.method === 'rule';

  const sentiCls = senti === '利好' ? 'tag-up' : senti === '利空' ? 'tag-down' : 'tag-neutral';

  // 规则引擎版本：只列标题，不显示「未分析影响」这种占位文案
  const events = isRule
    ? (s.key_events || []).map(e => `
        <div class="sym-row" style="padding:9px 12px">
          <span class="fs-13" style="flex:1;line-height:1.6">${esc(e.event || '')}</span>
        </div>`).join('')
    : (s.key_events || []).map(e => `
    <div style="padding:11px 13px;background:var(--surface-2);border-radius:10px;margin-bottom:8px">
      <div class="fs-13" style="font-weight:600;line-height:1.6">${esc(e.event || '')}</div>
      ${e.impact ? `<div class="fs-12 text-2 mt-8" style="line-height:1.65">${esc(e.impact)}</div>` : ''}
      <div class="flex center gap-6 mt-8" style="flex-wrap:wrap">
        ${(e.sectors || []).map(x => `<span class="tag tag-primary">${esc(x)}</span>`).join('')}
        ${e.sentiment ? `<span class="tag ${e.sentiment === '利好' ? 'tag-up'
          : e.sentiment === '利空' ? 'tag-down' : 'tag-neutral'}">${esc(e.sentiment)}</span>` : ''}
      </div>
    </div>`).join('');

  const symbols = (s.related_symbols || []).map(x => `
    <div class="sym-row" data-sym="${esc(x.symbol)}" style="cursor:pointer;padding:8px 10px">
      <span class="sym-name">${esc(x.name || x.symbol)}</span>
      <span class="sym-code">${esc(x.symbol)}</span>
      <span class="fs-11 text-3 ellipsis" style="max-width:220px">${esc(x.reason || '')}</span>
    </div>`).join('');

  // 关键词匹配到的板块（来自聚合层，不是 AI 生成）
  const matchedSectors = (bundle?.sectors || []).map(x => x.sector);

  return `
  <div class="card mb-16">
    <div class="card-head">
      <div>
        <div class="card-title">
          情报归纳
          ${isRule ? '<span class="tag tag-warn" style="margin-left:8px">规则引擎</span>'
                   : '<span class="tag tag-primary" style="margin-left:8px">AI</span>'}
        </div>
        <div class="card-sub" style="margin-top:2px">
          ${isRule
            ? '未使用 AI（渠道不可用或未勾选），按关键词情绪打分，仅供参考'
            : `共 ${(s.key_events || []).length} 个关键事件 · 可信度 ${Math.round((s.credibility ?? 0) * 100)}%`}
        </div>
      </div>
      <span class="tag ${sentiCls}" style="font-size:12.5px;padding:4px 12px">
        ${esc(senti)} ${score > 0 ? '+' : ''}${score}
      </span>
    </div>
    <div class="card-pad">
      ${s.summary ? `
        <div class="fs-14 mb-16" style="line-height:1.75;color:#1f2937">
          ${esc(s.summary)}
        </div>` : ''}

      ${matchedSectors.length ? `
        <div class="mb-16">
          <div class="fs-11 text-3 mb-8" style="font-weight:600;letter-spacing:.3px">
            命中板块（来自关键词匹配）
          </div>
          <div class="chips">
            ${matchedSectors.map(x => `<span class="chip on">${esc(x)}</span>`).join('')}
          </div>
        </div>` : ''}

      ${events ? `
        <div class="mb-16">
          <div class="fs-11 text-3 mb-8" style="font-weight:600;letter-spacing:.3px">
            ${isRule ? '相关新闻标题' : '关键事件'}
          </div>
          ${isRule
            ? `<div style="border:1px solid var(--border);border-radius:11px;overflow:hidden">${events}</div>`
            : events}
        </div>` : ''}

      ${symbols ? `
        <div>
          <div class="fs-11 text-3 mb-8" style="font-weight:600;letter-spacing:.3px">
            相关个股（点击直接分析）
          </div>
          <div style="border:1px solid var(--border);border-radius:11px;overflow:hidden">
            ${symbols}
          </div>
        </div>` : ''}
    </div>
  </div>`;
}

function newsList(items, q) {
  const rows = items.map(it => {
    const hl = (txt) => {
      if (!txt) return '';
      const safe = esc(txt);
      return safe.replace(new RegExp(`(${esc(q).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi'),
        '<mark style="background:#fef3c7;color:inherit;padding:0 1px">$1</mark>');
    };

    return `
    <div class="sym-row" style="align-items:flex-start;padding:14px 16px;gap:12px">
      <div style="flex:1;min-width:0">
        <a href="${esc(it.url || '#')}" target="_blank" rel="noopener"
           style="font-size:13.5px;font-weight:600;color:var(--text);text-decoration:none;
                  line-height:1.6;display:block">
          ${hl(it.title || '')}
        </a>
        ${it.summary ? `<div class="fs-12 text-2 mt-8" style="line-height:1.7;
          display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">
          ${esc(it.summary)}</div>` : ''}
        <div class="fs-11 text-3 mt-8 flex center gap-8" style="flex-wrap:wrap">
          <span class="tag tag-neutral">${esc(it.media || it.source || '')}</span>
          <span>${esc(it.published_at || '')}</span>
        </div>
      </div>
    </div>`;
  }).join('');

  return `
  <div class="card">
    <div class="card-head">
      <div class="card-title">新闻原文</div>
      <div class="card-sub">${items.length} 条 · 已按相关性排序并去重</div>
    </div>
    <div>${rows}</div>
  </div>`;
}