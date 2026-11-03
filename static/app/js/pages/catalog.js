/* ============================================================
   产品中心
   ------------------------------------------------------------
   回答「有哪些品种、能不能分析」，和「市场百科」（规则是什么）互补。
   状态：full 可完整分析 / partial 部分支持 / none 未支持
   语言跟随设置：zh / en / both
   ============================================================ */

import { esc, api, state, navigate, pageHead, alertBox } from '../shared.js';

export async function render(root, ctx) {
  root.innerHTML = `
    ${pageHead('产品中心', '中国金融市场的品种目录，以及本软件对每个品种的支持程度',
      `<span class="fs-12 text-3" id="catStats"></span>`)}
    <div id="catBody"><div class="loading"><div class="spinner"></div><span>加载目录…</span></div></div>`;

  let d;
  try {
    d = await api.get('/api/v2/catalog');
  } catch (err) {
    root.querySelector('#catBody').innerHTML = alertBox('error', `加载失败：${esc(err.message)}`);
    return;
  }

  renderCatalog(root, ctx, d);
}

function lang() {
  return state.settings.library_lang || 'zh';
}

function T(obj) {
  if (!obj) return '';
  if (typeof obj === 'string') return obj;
  if (lang() === 'en') return obj.en || obj.zh || '';
  return obj.zh || '';
}

function Tsub(obj) {
  if (lang() !== 'both' || !obj || typeof obj === 'string') return '';
  return obj.en || '';
}

function renderCatalog(root, ctx, d) {
  const groups = d.groups || [];
  const st = d.stats || {};

  const statEl = root.querySelector('#catStats');
  if (statEl) {
    statEl.textContent =
      `${st.items} 个品种 · 可完整分析 ${st.full} · 部分支持 ${st.partial} · 未支持 ${st.none}`;
  }

  const statusMeta = {
    full: { cls: 'tag-down', label: '可完整分析', dot: '●' },
    partial: { cls: 'tag-warn', label: '部分支持', dot: '◐' },
    none: { cls: 'tag-neutral', label: '未支持', dot: '○' },
  };

  const body = root.querySelector('#catBody');
  body.innerHTML = `
    <div class="grid-3 mb-16">
      <div class="card"><div class="card-pad" style="text-align:center">
        <div class="v num" style="font-size:26px;font-weight:800;color:var(--down)">${st.full ?? 0}</div>
        <div class="fs-12 text-2 mt-8">可完整分析</div>
        <div class="fs-11 text-3">行情 + 指标 + 评分 + 锚点</div>
      </div></div>
      <div class="card"><div class="card-pad" style="text-align:center">
        <div class="v num" style="font-size:26px;font-weight:800;color:var(--warn)">${st.partial ?? 0}</div>
        <div class="fs-12 text-2 mt-8">部分支持</div>
        <div class="fs-11 text-3">仅行情 / 快照，无完整财务</div>
      </div></div>
      <div class="card"><div class="card-pad" style="text-align:center">
        <div class="v num" style="font-size:26px;font-weight:800;color:var(--text-3)">${st.none ?? 0}</div>
        <div class="fs-12 text-2 mt-8">未支持</div>
        <div class="fs-11 text-3">数据源未接入</div>
      </div></div>
    </div>

    ${groups.map(g => `
      <div class="card mb-16">
        <div class="card-head">
          <div class="flex center gap-8">
            <span style="color:var(--primary);font-size:15px">${esc(g.icon || '')}</span>
            <div class="card-title">${esc(T(g.label))}</div>
          </div>
          <div class="card-sub">${g.items.length} 个品种</div>
        </div>
        <div>
          ${g.items.map(it => {
            const meta = statusMeta[it.status] || statusMeta.none;
            const clickable = it.go && it.status !== 'none';
            return `
            <div class="sym-row cat-row" data-item='${esc(JSON.stringify({
              go: it.go || null, status: it.status,
            }))}' style="padding:13px 18px;${clickable ? 'cursor:pointer' : 'opacity:.72'}">
              <div style="flex:1;min-width:0">
                <div class="flex center gap-8" style="flex-wrap:wrap">
                  <span style="font-weight:600">${esc(T(it.name))}</span>
                  ${Tsub(it.name)
                    ? `<span class="fs-11 text-3">${esc(Tsub(it.name))}</span>` : ''}
                  <span class="tag ${meta.cls}">${meta.dot} ${meta.label}</span>
                </div>
                <div class="fs-12 text-2 mt-8" style="line-height:1.6">${esc(T(it.desc))}</div>
                ${it.note ? `<div class="fs-11 text-3 mt-8">↳ ${esc(T(it.note))}</div>` : ''}
              </div>
              ${clickable
                ? `<svg width="15" height="15" viewBox="0 0 24 24" fill="none"
                     stroke="var(--text-3)" stroke-width="2"><path d="M9 6l6 6-6 6"/></svg>`
                : ''}
            </div>`;
          }).join('')}
        </div>
      </div>`).join('')}

    ${alertBox('info', `<b>怎么用这个页面：</b>它能分析的品种点进去会跳到对应的筛选或分析页；
      标「未支持」的不是不能做，而是数据源还没接 —— 软件不会给你编数据。`)}`;

  // 点击跳转
  body.querySelectorAll('.cat-row').forEach(row => {
    row.addEventListener('click', () => {
      let meta;
      try { meta = JSON.parse(row.dataset.item); } catch (_) { return; }
      if (!meta.go) return;

      if (meta.go.page === 'analysis' && meta.go.symbol) {
        navigate('analysis', { symbol: meta.go.symbol });
      } else if (meta.go.page === 'screen') {
        navigate('screen');
      } else if (meta.go.page === 'external' && meta.go.href) {
        window.open(meta.go.href, '_blank');
      }
    });
  });
}