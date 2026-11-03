"""
v2 API 路由：把 P0-P5 的能力暴露成网页接口。

设计原则：
    1. 每个接口都能独立降级（AI 挂了不影响量化结果）
    2. 长耗时操作（分析）返回结构化结果，前端直接渲染
    3. 敏感信息（密钥）永远不返回明文
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.ai import channels as ch_store
from app.ai import gateway
from app.analysis import service
from app.analytics import scoring
from app.markets import registry
from app.markets.symbols import MARKET_LABELS, SUPPORTED_MARKETS
from app.news import aggregator, ai_summary, sector_map

router = APIRouter(prefix="/api/v2", tags=["v2"])


# ==================================================================
# 基础信息
# ==================================================================
@router.get("/ping")
def ping():
    """
    内部就绪探测专用（不触发心跳）。

    桌面启动器用它判断服务是否起来。
    不能用 /api/health —— 那个会记录心跳，
    导致"前端还没连上"被误判成"前端已连上"。
    """
    return {"pong": True}


@router.get("/profiles")
def list_profiles():
    """分析模式列表（像选模型一样选权重）。"""
    return {
        "profiles": scoring.list_profiles(),
        "dimension_labels": scoring.DIMENSION_LABELS,
    }


@router.get("/markets")
def list_markets():
    """支持的市场与数据能力矩阵。"""
    coverage = registry.market_coverage()
    return {
        "markets": [
            {"code": m, "label": MARKET_LABELS.get(m, m), **coverage.get(m, {})}
            for m in SUPPORTED_MARKETS if m in coverage
        ]
    }


@router.get("/search")
def search(q: str = Query(..., description="关键词或代码"),
           markets: str | None = Query(None, description="逗号分隔的市场，如 SH,SZ,HK,US"),
           limit: int = 15):
    """跨市场搜索标的。"""
    mkt_list = [m.strip().upper() for m in markets.split(",")] if markets else None
    results = registry.search(q, markets=mkt_list, limit=limit)
    return {"keyword": q, "count": len(results), "results": results}


# ==================================================================
# 核心：分析
# ==================================================================
@router.get("/analyze/{symbol:path}")
def analyze(symbol: str,
            profile: str = Query("balanced", description="分析模式"),
            use_ai: bool = Query(True, description="是否启用AI"),
            channel_id: int | None = Query(None, description="指定AI渠道")):
    """
    完整分析一个标的：量化指标 + 价格锚点 + AI定性 + 最终推荐。
    """
    try:
        return service.analyze(symbol, profile=profile,
                               channel_id=channel_id, use_ai=use_ai)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)[:400]) from exc


# ==================================================================
# 新闻
# ==================================================================
@router.get("/news")
def news(q: str = Query(..., description="关键词"),
         days: int = 7, limit: int = 20):
    """只取新闻（不调 AI，快）。"""
    return aggregator.search_news(q, days=days, limit=limit)


@router.get("/news/summary")
def news_summary(q: str = Query(...), days: int = 7, limit: int = 20,
                 use_ai: bool = True, channel_id: int | None = None):
    """新闻 + AI 归纳。"""
    bundle = aggregator.search_news(q, days=days, limit=limit)
    summary = ai_summary.summarize(q, bundle["items"], bundle["sectors"],
                                   channel_id=channel_id, use_ai=use_ai)
    return {**bundle, "summary": summary}


@router.get("/sectors")
def list_sectors():
    """内置板块列表。"""
    return {"sectors": sector_map.all_sectors()}


@router.get("/sectors/{name}")
def sector_detail(name: str):
    """某个板块的代表个股。"""
    stocks = sector_map.sector_stocks(name)
    if not stocks:
        raise HTTPException(status_code=404, detail=f"没有板块 {name}")
    return {"sector": name,
            "stocks": [{"symbol": c, "name": n} for c, n in stocks]}


# ==================================================================
# 选股筛选
# ==================================================================
class ScreenIn(BaseModel):
    symbols: list[str] = Field(default_factory=list, description="待筛选的标的列表")
    profile: str = "balanced"
    conditions: dict = Field(default_factory=dict, description="筛选条件")


@router.get("/universe")
def universe(sector: str | None = Query(None, description="板块名；不填返回全部")):
    """
    候选池：从内置板块映射表取代表个股。

    没有 sector 参数时返回所有板块的全部个股（去重，约100只）。
    """
    if sector:
        stocks = sector_map.sector_stocks(sector)
        if not stocks:
            raise HTTPException(status_code=404, detail=f"没有板块 {sector}")
        return {"sector": sector, "count": len(stocks),
                "symbols": [{"symbol": c, "name": n} for c, n in stocks]}

    seen: dict[str, str] = {}
    for name in sector_map.all_sectors():
        for code, stock_name in sector_map.sector_stocks(name):
            seen.setdefault(code, stock_name)
    return {"sector": None, "count": len(seen),
            "symbols": [{"symbol": c, "name": n} for c, n in seen.items()]}


@router.post("/screen")
def screen_stocks(body: ScreenIn):
    """
    批量筛选。

    注意：这个接口不调 AI（否则 20 只股票要等几分钟）。
    它只做量化计算 + 条件过滤 + 排序。
    """
    from app.analysis import screener

    symbols = body.symbols
    if not symbols:
        raise HTTPException(status_code=400, detail="symbols 不能为空")

    try:
        return screener.screen(symbols, profile=body.profile,
                               conditions=body.conditions)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)[:400]) from exc


# ==================================================================
# 用户设置
# ==================================================================
@router.get("/settings")
def get_settings():
    """读取全部设置（未设置过的用默认值）。"""
    from app.userdata import store
    return {"settings": store.get_settings(), "defaults": store.DEFAULTS}


class SettingsIn(BaseModel):
    settings: dict = Field(default_factory=dict)


@router.put("/settings")
def update_settings(body: SettingsIn):
    """局部更新设置。"""
    from app.userdata import store
    try:
        merged = store.set_settings(body.settings)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"settings": merged}


# ==================================================================
# 候选池
# ==================================================================
class WatchlistIn(BaseModel):
    name: str = Field(..., description="池名称")
    note: str = ""


class ItemsIn(BaseModel):
    symbols: list[str] = Field(default_factory=list, description="标准代码列表")
    raw_text: str = Field("", description="用户粘贴的原始文本（自动解析）")
    industry: str = ""


class ParseIn(BaseModel):
    text: str = ""


@router.get("/watchlists")
def list_watchlists():
    """列出所有候选池（含内置池）。"""
    from app.userdata import store
    store.ensure_builtin_watchlist()
    return {"watchlists": store.list_watchlists()}


@router.post("/watchlists")
def create_watchlist(body: WatchlistIn):
    from app.userdata import store
    try:
        wid = store.create_watchlist(body.name, body.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": wid, "ok": True}


@router.delete("/watchlists/{wid}")
def delete_watchlist(wid: int):
    from app.userdata import store
    try:
        store.delete_watchlist(wid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/watchlists/{wid}/items")
def watchlist_items(wid: int):
    from app.userdata import store
    items = store.get_watchlist_items(wid)

    grouped: dict[str, list] = {}
    for it in items:
        grouped.setdefault(it.get("industry") or "未分类", []).append(it)

    return {"id": wid, "count": len(items), "items": items,
            "groups": [{"industry": k, "items": v} for k, v in grouped.items()]}


@router.post("/watchlists/{wid}/items")
def add_watchlist_items(wid: int, body: ItemsIn):
    """
    往候选池加标的。两种方式：
        1. symbols: 直接给标准代码
        2. raw_text: 粘一坨文本自动解析（中文名会走搜索转代码）
    """
    from app.markets import registry
    from app.userdata import store

    added: list[dict] = []
    unresolved: list[dict] = []

    for s in body.symbols:
        s = (s or "").strip()
        if s:
            added.append({"symbol": s.upper(), "name": "", "industry": body.industry})

    if body.raw_text:
        parsed = store.parse_symbols(body.raw_text)
        for it in parsed["ok"]:
            added.append({"symbol": it["symbol"], "name": "", "industry": body.industry})

        for it in parsed["unknown"]:
            if it.get("reason") != "name":
                unresolved.append(it)
                continue
            try:
                hits = registry.search(it["name"], limit=1)
                if hits:
                    added.append({"symbol": hits[0]["symbol"],
                                  "name": hits[0]["name"],
                                  "industry": body.industry})
                else:
                    unresolved.append({"raw": it["raw"], "reason": "没搜到这个名称"})
            except Exception:  # noqa: BLE001
                unresolved.append({"raw": it["raw"], "reason": "搜索失败"})

    for it in added:
        if it["name"]:
            continue
        try:
            snap, _ = registry.get_snapshot(it["symbol"])
            it["name"] = snap.name or ""
        except Exception:  # noqa: BLE001
            pass

    n = store.add_items(wid, added)
    return {"added": n, "items": added, "unresolved": unresolved}


@router.delete("/watchlists/{wid}/items")
def remove_watchlist_items(wid: int, symbols: str = Query(..., description="逗号分隔")):
    from app.userdata import store
    lst = [s.strip() for s in symbols.split(",") if s.strip()]
    return {"removed": store.remove_items(wid, lst)}


@router.post("/watchlists/parse")
def parse_symbols_preview(body: ParseIn):
    """预览解析结果（不写库），让用户先看识别得对不对。"""
    from app.userdata import store
    return store.parse_symbols(body.text)


# ==================================================================
# 市场百科（知识库）
# ==================================================================
@router.get("/library")
def library(category: str | None = Query(None, description="分类 key"),
            q: str | None = Query(None, description="搜索关键词")):
    """
    中国金融市场知识库。

    纯静态内容，不依赖任何外部接口 —— 断网也能查。
    每条都带「对量化分析的影响」，这是最实用的一栏。
    """
    from app.library import knowledge

    if q:
        entries = knowledge.search(q)
    elif category:
        entries = knowledge.by_category(category)
    else:
        entries = knowledge.all_entries()

    return {
        "categories": knowledge.CATEGORIES,
        "entries": entries,
        "stats": knowledge.stats(),
        "query": {"category": category, "q": q},
    }


@router.get("/catalog")
def catalog():
    """
    中国金融产品目录。

    回答「有哪些品种、能不能分析」，和 /library（回答「规则是什么」）互补。
    每个品种标 full / partial / none，让用户一眼看到能力边界。
    """
    from app.library import catalog as cat

    return {
        "groups": cat.all_groups(),
        "stats": cat.stats(),
        "status_meta": cat.STATUS_META,
    }


@router.get("/library/{entry_id}")
def library_entry(entry_id: str):
    from app.library import knowledge
    for e in knowledge.all_entries():
        if e["id"] == entry_id:
            return {"entry": e}
    raise HTTPException(status_code=404, detail="没有这条知识条目")


# ==================================================================
# AI 渠道管理
# ==================================================================
class ChannelIn(BaseModel):
    name: str = Field(..., description="显示名")
    base_url: str = Field(..., description="中转站地址")
    model: str = Field(..., description="模型名")
    api_key: str = Field("", description="密钥（不返回给前端）")
    protocol: str = Field("openai", description="openai 或 anthropic")
    temperature: float = 0.3
    max_tokens: int = 2048
    is_default: bool = False


@router.get("/channels")
def list_channels():
    """列出渠道（密钥脱敏）。"""
    out = []
    for c in ch_store.list_channels():
        out.append({
            "id": c.id, "name": c.name, "protocol": c.protocol,
            "base_url": c.base_url, "model": c.model,
            "key_masked": c.key_masked, "enabled": c.enabled,
            "is_default": c.is_default, "capabilities": c.capabilities,
            "has_key_error": bool(c.capabilities.get("key_error")),
        })
    return {"channels": out}


@router.get("/channels/presets")
def channel_presets():
    """常见服务商预设。"""
    return {"presets": ch_store.PRESETS,
            "protocols": ch_store.PROTOCOL_LABELS}


@router.post("/channels")
def create_channel(body: ChannelIn):
    cid = ch_store.add_channel(
        name=body.name, base_url=body.base_url, model=body.model,
        api_key=body.api_key, protocol=body.protocol,
        temperature=body.temperature, max_tokens=body.max_tokens,
        is_default=body.is_default)
    return {"id": cid, "ok": True}


@router.delete("/channels/{cid}")
def delete_channel(cid: int):
    ch_store.delete_channel(cid)
    return {"ok": True}


@router.post("/channels/{cid}/probe")
def probe_channel(cid: int):
    """能力探测：测出这个中转站支持什么。"""
    ch = ch_store.get_channel(cid)
    if not ch:
        raise HTTPException(status_code=404, detail="渠道不存在")
    return {"capabilities": gateway.probe(ch)}


@router.post("/channels/{cid}/test")
def test_channel(cid: int):
    """测试连接（设置页的按钮）。"""
    ch = ch_store.get_channel(cid)
    if not ch:
        raise HTTPException(status_code=404, detail="渠道不存在")
    ok, msg = gateway.test_connection(ch)
    return {"ok": ok, "message": msg}


@router.get("/usage")
def usage(days: int = 7):
    """AI 用量统计。"""
    return {"days": days, "usage": ch_store.usage_summary(days)}
