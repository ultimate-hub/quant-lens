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
    """
    全部概念名（行业 + 概念 + 用户自定义）。

    历史名字保留为 /sectors，实际数据源已经是概念库。
    """
    from app.concepts import store as concept_store
    return {"sectors": concept_store.all_names()}


# ==================================================================
# 概念库（行业 / 概念 / 用户自定义）
# ==================================================================
class ConceptIn(BaseModel):
    name: str = Field(..., description="概念名称")
    kind: str = Field("concept", description="industry 或 concept")
    note: str = ""
    aliases: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)


class ConceptPatch(BaseModel):
    name: str | None = None
    kind: str | None = None
    note: str | None = None


class SymListIn(BaseModel):
    symbols: list[str] = Field(default_factory=list)


class AliasIn(BaseModel):
    aliases: list[str] = Field(default_factory=list)


@router.get("/concepts")
def list_concepts(kind: str | None = Query(None, description="industry / concept"),
                  q: str | None = Query(None, description="按名称或别名搜")):
    """列出全部概念（含用户自定义）。"""
    from app.concepts import store as concept_store
    return {
        "concepts": concept_store.list_concepts(kind=kind, q=q),
        "stats": concept_store.stats(),
    }


@router.get("/concepts/{cid}")
def get_concept(cid: int):
    from app.concepts import store as concept_store
    c = concept_store.get_concept(cid)
    if not c:
        raise HTTPException(status_code=404, detail="概念不存在")
    return {"concept": c}


@router.post("/concepts")
def create_concept(body: ConceptIn):
    from app.concepts import store as concept_store
    try:
        cid = concept_store.create_concept(
            body.name, kind=body.kind, note=body.note,
            aliases=body.aliases, symbols=body.symbols)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": cid, "ok": True}


@router.put("/concepts/{cid}")
def update_concept(cid: int, body: ConceptPatch):
    from app.concepts import store as concept_store
    try:
        concept_store.update_concept(cid, name=body.name, note=body.note, kind=body.kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.delete("/concepts/{cid}")
def delete_concept(cid: int):
    from app.concepts import store as concept_store
    try:
        concept_store.delete_concept(cid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/concepts/{cid}/items")
def add_concept_items(cid: int, body: SymListIn):
    from app.concepts import store as concept_store
    n = concept_store.add_items(cid, body.symbols)
    return {"added": n}


@router.delete("/concepts/{cid}/items")
def remove_concept_items(cid: int, symbols: str = Query(..., description="逗号分隔")):
    from app.concepts import store as concept_store
    lst = [s.strip() for s in symbols.split(",") if s.strip()]
    return {"removed": concept_store.remove_items(cid, lst)}


@router.post("/concepts/{cid}/aliases")
def add_concept_aliases(cid: int, body: AliasIn):
    from app.concepts import store as concept_store
    n = concept_store.add_aliases(cid, body.aliases)
    return {"added": n}


@router.delete("/concepts/{cid}/aliases")
def remove_concept_alias(cid: int, alias: str = Query(...)):
    from app.concepts import store as concept_store
    concept_store.remove_alias(cid, alias)
    return {"ok": True}


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
# 市场全景（热力图）
# ==================================================================
@router.get("/market/heatmap")
def market_heatmap(force: bool = Query(False, description="强制重新抓取")):
    """
    市场全景热力图数据。

    默认用当天缓存 —— 保证「今天的封面」一整天不变，
    也避免反复请求东财批量接口被限流。
    """
    from app import market_overview

    try:
        return market_overview.build_heatmap(force=force)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)[:300]) from exc


# ==================================================================
# 持仓日志
# ==================================================================
class PositionIn(BaseModel):
    symbol: str = Field(..., description="标准代码 600519.SH")
    name: str = ""
    buy_date: str = Field(..., description="YYYY-MM-DD")
    buy_price: float = Field(..., gt=0)
    shares: float = Field(..., gt=0)
    target_ratio: float | None = Field(None, description="目标占比 %")
    note: str = ""


class PositionPatch(BaseModel):
    symbol: str | None = None
    name: str | None = None
    buy_date: str | None = None
    buy_price: float | None = None
    shares: float | None = None
    target_ratio: float | None = None
    note: str | None = None


@router.get("/positions")
def list_positions():
    """持仓明细 + 汇总（含当前价、浮盈、占比）。"""
    from app import portfolio

    try:
        return portfolio.list_positions()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)[:300]) from exc


@router.post("/positions")
def add_position(body: PositionIn):
    from app import portfolio

    try:
        pid = portfolio.add(
            body.symbol, body.buy_date, body.buy_price, body.shares,
            name=body.name, target_ratio=body.target_ratio, note=body.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": pid, "ok": True}


@router.put("/positions/{pid}")
def update_position(pid: int, body: PositionPatch):
    from app import portfolio

    try:
        portfolio.update(pid, **{k: v for k, v in body.model_dump().items()
                                 if v is not None})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.delete("/positions/{pid}")
def delete_position(pid: int):
    from app import portfolio
    portfolio.remove(pid)
    return {"ok": True}


@router.delete("/positions")
def clear_positions():
    from app import portfolio
    portfolio.clear_all()
    return {"ok": True}


# ==================================================================
# 走势推演 + 配仓建议
# ==================================================================
@router.get("/outlook/{symbol}")
def outlook(symbol: str,
            days: int = Query(20, ge=5, le=60, description="推演交易日数"),
            sims: int = Query(1000, ge=200, le=5000, description="模拟路径数"),
            profile: str = Query("balanced")):
    """
    走势推演 + 配仓建议。

    注意：这里**不调用 AI**。
        - 推演是统计学模拟（历史日收益自举）
        - 建议是规则引擎（可追溯到具体数字）
    这样即使没有配 AI 渠道，这两个功能也完整可用。
    """
    from app.analytics import advice as advice_mod
    from app.analytics import forecast as forecast_mod

    try:
        bundle = service.analyze(symbol, profile=profile, use_ai=False)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)[:300]) from exc

    # final 是合并后的锚点（含 target_price / entry_range / stop_loss / position_pct），
    # anchors 是原始计算结构，字段名不同 —— 用错会导致目标价取到 None
    anchors = bundle.get("final") or {}
    price_m = bundle.get("price_metrics") or {}
    fund_m = bundle.get("fundamental_metrics") or {}
    scoring = bundle.get("final") or {}
    series = bundle.get("price_series") or []

    if not series:
        raise HTTPException(status_code=400, detail="没有足够的历史价格数据")

    import pandas as pd

    closes = pd.Series(
        [p["close"] for p in series],
        index=pd.to_datetime([p["date"] for p in series]))

    cur = anchors.get("current_price") or float(closes.iloc[-1])

    # ---- 模拟 ----
    try:
        fc = forecast_mod.simulate(
            symbol, closes, days=days, sims=sims,
            target_price=anchors.get("target_price"),
            stop_price=anchors.get("stop_loss"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ---- 建议 ----
    adv = advice_mod.build_advice(
        symbol=symbol,
        name=bundle.get("name") or symbol,
        current_price=cur,
        anchors=anchors,
        scoring=scoring,
        price_metrics=price_m,
        fundamental_metrics=fund_m,
        forecast=fc,
    )

    # ---- 时序模型对比（不影响上面的蒙特卡洛，是另一条路径的参考）----
    ts = None
    try:
        from app.analytics import ts_models as ts_mod

        ts_paths = ts_mod.forecast_all(closes.to_numpy(), days=days)
        ts_bt = ts_mod.backtest(closes.to_numpy(), days=days)
        ts = {
            "paths": ts_paths,
            "backtest": ts_bt,
            "labels": {k: v[0] for k, v in ts_mod.MODELS.items()},
        }
    except Exception as exc:  # noqa: BLE001
        ts = {"error": str(exc)[:200]}

    return {
        "symbol": symbol,
        "name": bundle.get("name"),
        "forecast": fc,
        "scenarios": forecast_mod.scenarios(fc),
        "advice": adv,
        "ts": ts,
    }


@router.get("/forecast/{symbol}/calibration")
def forecast_calibration(
    symbol: str,
    days: int = Query(20, ge=5, le=60),
    sims: int = Query(300, ge=100, le=1000),
    tests: int = Query(12, ge=3, le=30),
):
    """
    走势推演的校准检验。

    回答的问题是：**这个模拟到底罩不罩得住真实值**。

    做法是走查回测：在历史上取若干个截止点，只用截止点之前的数据模拟，
    再看真实价格落没落在 50% / 90% 区间里，统计命中率。

    如果 50% 区间只罩住 30%，说明模型过度自信，置信带要放宽；
    如果罩住 75%，说明画太宽了，不够锐利。
    """
    from app.analytics import forecast as forecast_mod

    try:
        bundle = service.analyze(symbol, profile="balanced", use_ai=False)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)[:300]) from exc

    series = bundle.get("price_series") or []
    if not series:
        raise HTTPException(status_code=400, detail="没有历史价格数据")

    import pandas as pd

    closes = pd.Series(
        [p["close"] for p in series],
        index=pd.to_datetime([p["date"] for p in series]))

    out = forecast_mod.calibrate(symbol, closes, days=days, sims=sims, n_tests=tests)
    out["symbol"] = symbol
    out["name"] = bundle.get("name")
    return out


@router.get("/advice/{symbol}")
def advice_only(symbol: str, profile: str = Query("balanced")):
    """只要配仓建议（不跑模拟，快）。"""
    from app.analytics import advice as advice_mod

    try:
        bundle = service.analyze(symbol, profile=profile, use_ai=False)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)[:300]) from exc

    anchors = bundle.get("final") or {}
    return {
        "advice": advice_mod.build_advice(
            symbol=symbol,
            name=bundle.get("name") or symbol,
            current_price=anchors.get("current_price") or 0,
            anchors=anchors,
            scoring=bundle.get("final") or {},
            price_metrics=bundle.get("price_metrics") or {},
            fundamental_metrics=bundle.get("fundamental_metrics") or {},
        )
    }


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
