/* ============================================================
   设置
   ------------------------------------------------------------
   四个分区：市场与数据源 / AI 渠道 / 界面与默认值 / 关于
   改动自动保存（防抖 500ms）
   ============================================================ */

import {
  esc, api, state, pageHead, alertBox, debounce, saveLocal,
} from '../shared.js';

let draft = null;      // 本地草稿（跟随服务端）

export async function render(root, ctx) {
  root.innerHTML = `
    ${pageHead('设置', '市场开关、数据源、AI 渠道、界面偏好',
      `<span id="saveHint" class="fs-12 text-3">改动自动保存</span>`)}
    <div class="tabs" id="setTabs">
      <div class="tab active" data-tab="market">市场与数据源</div>
      <div class="tab" data-tab="ai">AI 渠道</div>
      <div class="tab" data-tab="ui">界面与默认值</div>
      <div class="tab" data-tab="about">关于</div>
    </div>
    <div id="setBody"><div class="loading"><div class="spinner"></div><span>加载设置…</span></div></div>`;

  let s;
  try {
    s = await api.get('/api/v2/settings');
    draft = s.settings || {};
  } catch (err) {
    root.querySelector('#setBody').innerHTML = alertBox('error', `读取设置失败：${esc(err.message)}`);
    return;
  }

  const tabs = root.querySelector('#setTabs');
  tabs.querySelectorAll('.tab').forEach(t => {
    t.addEventListener('click', () => {
      tabs.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
      t.classList.add('active');
      renderTab(root, ctx, t.dataset.tab);
    });
  });

  renderTab(root, ctx, 'market');
}

function renderTab(root, ctx, tab) {
  const body = root.querySelector('#setBody');
  if (tab === 'market') { body.innerHTML = marketHTML(); bindMarket(root, ctx); }
  else if (tab === 'ai') { body.innerHTML = '<div id="aiWrap"></div>'; renderAI(root, ctx); }
  else if (tab === 'ui') { body.innerHTML = uiHTML(); bindUI(root, ctx); }
  else body.innerHTML = aboutHTML();
}

// ============================================================
// 自动保存
// ============================================================
function makeSaver(root, ctx) {
  return debounce(async (patch) => {
    const hint = root.querySelector('#saveHint');
    if (hint) { hint.textContent = '保存中…'; hint.style.color = ''; }
    try {
      const r = await api.put('/api/v2/settings', { settings: patch });
      draft = r.settings || draft;
      Object.assign(state.settings, patch);
      if (patch.enabled_markets) {
        state.marketsOn = new Set(patch.enabled_markets);
        saveLocal('markets', patch.enabled_markets);
        document.dispatchEvent(new CustomEvent('markets-changed'));
      }
      if (patch.default_profile) state.profile = patch.default_profile;
      if (hint) {
        hint.textContent = '已保存';
        hint.style.color = 'var(--down)';
        setTimeout(() => { if (hint) { hint.textContent = '改动自动保存'; hint.style.color = ''; } }, 1600);
      }
    } catch (err) {
      if (hint) { hint.textContent = '保存失败'; hint.style.color = 'var(--up)'; }
      ctx.toast(`保存失败：${err.message}`, 'error');
    }
  }, 500);
}

// ============================================================
// 市场与数据源
// ============================================================
function marketHTML() {
  const enabled = new Set(draft.enabled_markets || ['SH', 'SZ', 'HK', 'US']);
  const bm = draft.benchmarks || {};
  const cache = draft.cache_hours || {};

  const rows = state.markets.map(m => {
    const on = enabled.has(m.code);
    const caps = [m.kline ? '行情' : null, m.snapshot ? '快照' : null,
      m.fundamentals ? '财务' : null].filter(Boolean).join(' / ') || '未支持';
    return `
    <div class="sym-row" style="padding:12px 16px">
      <input type="checkbox" class="cb mkt-cb" data-code="${m.code}" ${on ? 'checked' : ''}>
      <div style="flex:1;min-width:0">
        <div style="font-weight:600">${esc(m.label)}</div>
        <div class="fs-11 text-3">${esc(m.code)} · ${caps}</div>
      </div>
      <input class="input" style="width:180px;height:32px;font-size:12.5px"
             data-bm="${m.code}" value="${esc(bm[m.code] || '')}" placeholder="基准代码">
    </div>`;
  }).join('');

  const planned = [
    ['加密货币', 'CRYPTO', '接口已预留，暂未接入数据源'],
    ['日股 / 欧股', 'JP / EU', '计划中'],
  ].map(([label, code, note]) => `
    <div class="sym-row" style="padding:12px 16px;opacity:.5">
      <input type="checkbox" class="cb" disabled>
      <div style="flex:1">
        <div style="font-weight:600">${label}</div>
        <div class="fs-11 text-3">${code} · ${note}</div>
      </div>
      <span class="tag tag-neutral">未支持</span>
    </div>`).join('');

  return `
  <div class="card mb-16">
    <div class="card-head">
      <div>
        <div class="card-title">启用的市场</div>
        <div class="card-sub" style="margin-top:2px">
          勾选后，搜索和筛选只在这些市场里找。右侧可填各市场的基准指数（用于算 Beta / Alpha）
        </div>
      </div>
    </div>
    <div>${rows}${planned}</div>
  </div>

  <div class="card mb-16">
    <div class="card-head">
      <div>
        <div class="card-title">性能与缓存</div>
        <div class="card-sub" style="margin-top:2px">影响抓取速度和接口压力</div>
      </div>
    </div>
    <div class="card-pad">
      <div class="grid-3">
        <div class="field">
          <label>行情缓存（小时）</label>
          <input class="input" type="number" min="1" max="72" data-cache="quotes"
                 value="${cache.quotes ?? 6}">
        </div>
        <div class="field">
          <label>新闻缓存（小时）</label>
          <input class="input" type="number" min="1" max="72" data-cache="news"
                 value="${cache.news ?? 6}">
        </div>
        <div class="field">
          <label>筛选并发数</label>
          <input class="input" type="number" min="1" max="16" data-num="screen_concurrency"
                 value="${draft.screen_concurrency ?? 8}">
          <div class="hint">太高易触发限流，建议 6-10</div>
        </div>
      </div>
      <div class="grid-3">
        <div class="field">
          <label>AI 超时（秒）</label>
          <input class="input" type="number" min="30" max="600" data-num="ai_timeout"
                 value="${draft.ai_timeout ?? 120}">
        </div>
      </div>
    </div>
  </div>

  ${alertBox('info', `<b>为什么数据源优先级不让改：</b>A股/港股主用腾讯，美股主用 Yahoo，
    失败自动切备用源。这个是按各源的实际可用性调好的，手动打乱会让部分标的抓不到数据。`)}`;
}

function bindMarket(root, ctx) {
  const save = makeSaver(root, ctx);

  root.querySelectorAll('.mkt-cb').forEach(cb => {
    cb.addEventListener('change', () => {
      const on = [...root.querySelectorAll('.mkt-cb')]
        .filter(x => x.checked).map(x => x.dataset.code);
      save({ enabled_markets: on });
    });
  });

  root.querySelectorAll('[data-bm]').forEach(inp => {
    inp.addEventListener('input', () => {
      const map = { ...(draft.benchmarks || {}) };
      map[inp.dataset.bm] = inp.value.trim().toUpperCase();
      save({ benchmarks: map });
    });
  });

  root.querySelectorAll('[data-cache]').forEach(inp => {
    inp.addEventListener('input', () => {
      const c = { ...(draft.cache_hours || {}) };
      const v = Number(inp.value);
      if (v > 0) { c[inp.dataset.cache] = v; save({ cache_hours: c }); }
    });
  });

  root.querySelectorAll('[data-num]').forEach(inp => {
    inp.addEventListener('input', () => {
      const v = Number(inp.value);
      if (Number.isFinite(v) && v > 0) save({ [inp.dataset.num]: v });
    });
  });
}

// ============================================================
// 界面与默认值
// ============================================================
function uiHTML() {
  const lang = draft.library_lang || 'zh';
  const prof = draft.default_profile || 'balanced';

  return `
  <div class="card mb-16">
    <div class="card-head"><div class="card-title">界面</div></div>
    <div class="card-pad">
      <div class="grid-3">
        <div class="field">
          <label>知识库语言</label>
          <select class="select" data-set="library_lang">
            <option value="zh" ${lang === 'zh' ? 'selected' : ''}>仅中文</option>
            <option value="en" ${lang === 'en' ? 'selected' : ''}>仅英文</option>
            <option value="both" ${lang === 'both' ? 'selected' : ''}>中英对照</option>
          </select>
          <div class="hint">影响「市场百科」的显示语言</div>
        </div>
        <div class="field">
          <label>默认分析模式</label>
          <select class="select" data-set="default_profile">
            ${state.profiles.map(p => `
              <option value="${p.key}" ${p.key === prof ? 'selected' : ''}>${esc(p.label)}</option>
            `).join('')}
          </select>
        </div>
        <div class="field">
          <label>默认候选池</label>
          <select class="select" data-set="default_watchlist" id="selDefaultPool">
            <option value="">（内置全部板块）</option>
          </select>
          <div class="hint">筛选页默认用哪个池</div>
        </div>
      </div>
    </div>
  </div>

  ${alertBox('info', `<b>缓存怎么起作用：</b>行情默认缓存 6 小时 —— 同一天重复分析同一只股票几乎瞬时完成，
    跨天自动重抓。想强制刷新，把小时数调小。`)}`;
}

function bindUI(root, ctx) {
  const save = makeSaver(root, ctx);

  root.querySelectorAll('[data-set]').forEach(sel => {
    sel.addEventListener('change', () => save({ [sel.dataset.set]: sel.value }));
  });

  fillPoolOptions(root);
}

async function fillPoolOptions(root) {
  const sel = root.querySelector('#selDefaultPool');
  if (!sel) return;
  try {
    const d = await api.get('/api/v2/watchlists');
    const cur = String(draft.default_watchlist || '');
    sel.innerHTML = '<option value="">（内置全部板块）</option>' +
      (d.watchlists || []).map(p =>
        `<option value="${p.id}" ${String(p.id) === cur ? 'selected' : ''}>
          ${esc(p.name)}（${p.count}）
        </option>`).join('');
  } catch (_) { /* 忽略 */ }
}

// ============================================================
// 关于
// ============================================================
function aboutHTML() {
  return `
  <div class="card mb-16">
    <div class="card-head"><div class="card-title">关于</div></div>
    <div class="card-pad">
      <div class="kv-grid" style="grid-template-columns:repeat(3,1fr)">
        <div class="kv"><div class="k">版本</div><div class="v" style="font-size:14px">v2.0.0</div></div>
        <div class="kv"><div class="k">量化指标</div><div class="v" style="font-size:14px">76 项</div></div>
        <div class="kv"><div class="k">分析模式</div><div class="v" style="font-size:14px">8 套</div></div>
        <div class="kv"><div class="k">行情源</div><div class="v" style="font-size:14px">腾讯 / Yahoo</div></div>
        <div class="kv"><div class="k">财务源</div><div class="v" style="font-size:14px">东方财富</div></div>
        <div class="kv"><div class="k">本地存储</div><div class="v" style="font-size:14px">SQLite</div></div>
      </div>
    </div>
  </div>

  <div class="card mb-16">
    <div class="card-head"><div class="card-title">数据与隐私</div></div>
    <div class="card-pad fs-13 text-2" style="line-height:2">
      <div>· 数据、候选池、AI 渠道配置全部存在本机数据库</div>
      <div>· API 密钥用 Windows DPAPI 加密，换电脑无法解密</div>
      <div>· 不向任何第三方上传你的持仓或分析记录</div>
      <div>· 行情数据来自公开接口，仅供学习研究</div>
    </div>
  </div>

  ${alertBox('warn', `<b>免责声明：</b>本软件为个人科学研究工具。所有输出均为基于公开数据的量化计算结果
    与 AI 研究笔记，不构成任何投资建议。目标价、买入区间、止损位均为算法计算结果，不构成买卖指令。`)}`;
}

// ============================================================
// AI 渠道
// ============================================================
async function renderAI(root, ctx) {
  const wrap = root.querySelector('#aiWrap');
  if (!wrap) return;
  wrap.innerHTML = '<div class="loading"><div class="spinner"></div><span>读取渠道…</span></div>';

  let d;
  try {
    d = await api.get('/api/v2/channels');
  } catch (err) {
    wrap.innerHTML = alertBox('error', esc(err.message));
    return;
  }

  const chans = d.channels || [];

  const cards = chans.map(c => {
    const cap = c.capabilities || {};
    const ok = !cap.error;
    return `
    <div class="card mb-12">
      <div class="card-head">
        <div>
          <div class="card-title">
            ${esc(c.name)}
            ${c.is_default ? '<span class="tag tag-primary" style="margin-left:6px">默认</span>' : ''}
            ${ok ? '<span class="tag tag-down" style="margin-left:6px">可用</span>'
                 : '<span class="tag tag-up" style="margin-left:6px">异常</span>'}
          </div>
          <div class="card-sub" style="margin-top:3px">
            ${esc(c.protocol)} · ${esc(c.model)} · ${esc(c.base_url)}
          </div>
        </div>
        <div class="flex center gap-8">
          <button class="btn ghost sm" data-probe="${c.id}">探测</button>
          <button class="btn ghost sm" data-test="${c.id}">测试</button>
          <button class="btn danger sm" data-del="${c.id}">删除</button>
        </div>
      </div>
      <div class="card-pad" style="padding:14px 20px">
        <div class="fs-12 text-3" style="line-height:1.9">
          <div>密钥：<span class="num">${esc(c.key_masked || '未设置')}</span></div>
          ${cap.error
            ? `<div style="color:var(--up);white-space:pre-wrap;margin-top:5px">${esc(cap.error)}</div>`
            : `<div style="margin-top:5px">
                 模型列表 ${cap.models_ok ? '✓' : '—'} ·
                 JSON模式 ${cap.json_mode ? '✓' : '—'} ·
                 system角色 ${cap.system_role ? '✓' : '—'} ·
                 延迟 ${cap.latency_ms || 0}ms
                 ${cap.probed_at ? `· 探测于 ${esc(cap.probed_at)}` : ''}
               </div>`}
        </div>
      </div>
    </div>`;
  }).join('');

  wrap.innerHTML = `
    ${!chans.length ? alertBox('warn',
      '<b>还没有配置 AI 渠道。</b><br>不配置也能用 —— 所有量化指标、评分、价格锚点都由程序计算，不依赖 AI。') : ''}
    ${cards}
    <div class="card">
      <div class="card-head">
        <div class="card-title">添加渠道</div>
        <div class="card-sub">任意 OpenAI 兼容接口，或 Anthropic 原生协议</div>
      </div>
      <div class="card-pad">
        <div class="grid-3">
          <div class="field">
            <label>显示名</label>
            <input class="input" id="chName" placeholder="ds / claude / 本地">
          </div>
          <div class="field">
            <label>协议</label>
            <select class="select" id="chProtocol">
              <option value="openai">OpenAI 兼容（多数中转站）</option>
              <option value="anthropic">Anthropic 原生</option>
            </select>
          </div>
          <div class="field">
            <label>模型名</label>
            <input class="input" id="chModel" placeholder="deepseek-chat">
          </div>
        </div>
        <div class="field">
          <label>接口地址</label>
          <input class="input" id="chBaseUrl" placeholder="https://your-relay.com/v1">
          <div class="hint">注意结尾带不带 /v1，写错会报 404</div>
        </div>
        <div class="field">
          <label>API Key</label>
          <input class="input" id="chKey" type="password" placeholder="sk-...">
          <div class="hint">Windows DPAPI 加密存本地，不上传</div>
        </div>
        <button class="btn" id="btnAddChannel">添加渠道</button>
      </div>
    </div>`;

  wrap.querySelectorAll('[data-probe]').forEach(b => {
    b.addEventListener('click', async () => {
      b.disabled = true; b.textContent = '探测中…';
      try {
        const r = await api.post(`/api/v2/channels/${b.dataset.probe}/probe`, {});
        ctx.toast(r.message || (r.ok ? '探测完成' : '探测失败'), r.ok ? 'ok' : 'error');
      } catch (err) { ctx.toast(err.message, 'error'); }
      await renderAI(root, ctx);
    });
  });

  wrap.querySelectorAll('[data-test]').forEach(b => {
    b.addEventListener('click', async () => {
      b.disabled = true; b.textContent = '测试中…';
      try {
        const r = await api.post(`/api/v2/channels/${b.dataset.test}/test`, {});
        ctx.toast(r.message || (r.ok ? '连接成功' : '连接失败'), r.ok ? 'ok' : 'error');
      } catch (err) { ctx.toast(err.message, 'error'); }
      await renderAI(root, ctx);
    });
  });

  wrap.querySelectorAll('[data-del]').forEach(b => {
    b.addEventListener('click', async () => {
      if (!ctx.confirm('确定删除这个渠道？')) return;
      try {
        await api.del(`/api/v2/channels/${b.dataset.del}`);
        ctx.toast('已删除', 'ok');
        await renderAI(root, ctx);
      } catch (err) { ctx.toast(err.message, 'error'); }
    });
  });

  wrap.querySelector('#btnAddChannel')?.addEventListener('click', async () => {
    const name = wrap.querySelector('#chName').value.trim();
    const base_url = wrap.querySelector('#chBaseUrl').value.trim();
    const model = wrap.querySelector('#chModel').value.trim();
    const api_key = wrap.querySelector('#chKey').value.trim();
    const protocol = wrap.querySelector('#chProtocol').value;

    if (!name || !base_url || !model) { ctx.toast('名称、地址、模型都要填', 'warn'); return; }

    try {
      await api.post('/api/v2/channels', {
        name, base_url, model, api_key, protocol,
        temperature: 0.3, max_tokens: 2048, is_default: false,
      });
      ctx.toast('已添加', 'ok');
      await renderAI(root, ctx);
    } catch (err) {
      ctx.toast(err.message, 'error');
    }
  });
}