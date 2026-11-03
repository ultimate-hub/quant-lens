"""
中国金融产品目录。

和 knowledge.py 的分工：
    knowledge.py  解释规则（T+1、涨跌停…），回答「为什么」
    catalog.py    列出品种（股票、基金、债券…），回答「有什么、能不能分析」

每个品种标注三种状态，让用户一眼看出边界：
    full     可以完整分析（行情 + 指标 + 评分 + 锚点）
    partial  只能看行情/快照，没有财务数据
    none     本软件暂不支持
"""

from __future__ import annotations

GROUPS: list[dict] = [
    {
        "key": "stock",
        "label": {"zh": "股票", "en": "Stocks"},
        "icon": "▤",
        "items": [
            {
                "key": "sh_main", "name": {"zh": "沪市主板", "en": "SSE Main Board"},
                "desc": {"zh": "60 开头，市值加权为主", "en": "Codes 60xxxx"},
                "status": "full", "market": "SH", "go": {"page": "screen"},
            },
            {
                "key": "sz_main", "name": {"zh": "深市主板", "en": "SZSE Main Board"},
                "desc": {"zh": "00 开头", "en": "Codes 00xxxx"},
                "status": "full", "market": "SZ", "go": {"page": "screen"},
            },
            {
                "key": "chinext", "name": {"zh": "创业板", "en": "ChiNext"},
                "desc": {"zh": "300 开头，涨跌停 ±20%", "en": "Codes 300xxx, ±20% limit"},
                "status": "full", "market": "SZ", "go": {"page": "screen"},
            },
            {
                "key": "star", "name": {"zh": "科创板", "en": "STAR Market"},
                "desc": {"zh": "688 开头，允许未盈利上市", "en": "Codes 688xxx"},
                "status": "full", "market": "SH", "go": {"page": "screen"},
            },
            {
                "key": "bse", "name": {"zh": "北交所", "en": "Beijing Stock Exchange"},
                "desc": {"zh": "涨跌停 ±30%，流动性较弱", "en": "±30% limit, thinner liquidity"},
                "status": "full", "market": "BJ", "go": {"page": "screen"},
            },
            {
                "key": "hk", "name": {"zh": "港股", "en": "Hong Kong"},
                "desc": {"zh": "有行情和 PE/PB，无完整财务", "en": "Quotes and PE/PB, no financials"},
                "status": "partial", "market": "HK", "go": {"page": "screen"},
            },
            {
                "key": "us", "name": {"zh": "美股", "en": "US Equities"},
                "desc": {"zh": "有行情和 PB/市值，无完整财务", "en": "Quotes, PB and market cap only"},
                "status": "partial", "market": "US", "go": {"page": "screen"},
            },
            {
                "key": "st", "name": {"zh": "ST / *ST 股", "en": "ST Stocks"},
                "desc": {"zh": "涨跌停 ±5%，建议多数策略剔除", "en": "±5% limit, usually excluded"},
                "status": "full", "market": "SH", "go": {"page": "screen"},
                "note": {"zh": "本软件不自动剔除，请自行在筛选条件里处理",
                         "en": "Not auto-excluded; filter manually"},
            },
        ],
    },
    {
        "key": "fund",
        "label": {"zh": "基金", "en": "Funds"},
        "icon": "◇",
        "items": [
            {
                "key": "etf", "name": {"zh": "场内 ETF", "en": "On-exchange ETF"},
                "desc": {"zh": "可作基准，也可当个股分析", "en": "Usable as benchmark or analyzed like a stock"},
                "status": "full", "market": "SH", "go": {"page": "analysis", "symbol": "510300.SH"},
            },
            {
                "key": "lof", "name": {"zh": "LOF", "en": "LOF"},
                "desc": {"zh": "场内买卖 + 场外申赎", "en": "Exchange-traded and OTC"},
                "status": "partial", "market": "SZ", "go": {"page": "analysis", "symbol": "161725.SZ"},
            },
            {
                "key": "otc_fund", "name": {"zh": "场外基金", "en": "Mutual Funds"},
                "desc": {"zh": "旧版基金诊断模块（净值曲线）", "en": "Legacy fund module (NAV based)"},
                "status": "partial", "go": {"page": "external", "href": "/v1"},
                "note": {"zh": "在旧版界面，点开会跳转", "en": "Opens the legacy interface"},
            },
            {
                "key": "reits", "name": {"zh": "公募 REITs", "en": "Public REITs"},
                "desc": {"zh": "基础设施类，强制高分红", "en": "Infrastructure, mandatory payout"},
                "status": "partial", "market": "SH",
                "note": {"zh": "能看行情，但估值逻辑与股票不同", "en": "Quotes only; different valuation logic"},
            },
        ],
    },
    {
        "key": "bond",
        "label": {"zh": "债券", "en": "Bonds"},
        "icon": "≡",
        "items": [
            {
                "key": "convertible", "name": {"zh": "可转债", "en": "Convertible Bonds"},
                "desc": {"zh": "T+0，无涨跌停但有临停", "en": "T+0, no daily limit but temporary halts"},
                "status": "none",
                "note": {"zh": "接口未接入", "en": "Data source not integrated"},
            },
            {
                "key": "repo", "name": {"zh": "国债逆回购", "en": "Reverse Repo"},
                "desc": {"zh": "短期现金管理工具", "en": "Short-term cash management"},
                "status": "none",
                "note": {"zh": "接口未接入", "en": "Data source not integrated"},
            },
            {
                "key": "gov_bond", "name": {"zh": "国债 / 地方债", "en": "Government Bonds"},
                "desc": {"zh": "利率债，与股市相关性低", "en": "Rate products, low equity correlation"},
                "status": "none",
                "note": {"zh": "接口未接入", "en": "Data source not integrated"},
            },
        ],
    },
    {
        "key": "index",
        "label": {"zh": "指数", "en": "Indices"},
        "icon": "◎",
        "items": [
            {
                "key": "csi300", "name": {"zh": "沪深 300", "en": "CSI 300"},
                "desc": {"zh": "默认 A 股基准（用 510300.SH 代替）", "en": "Default A-share benchmark (via 510300.SH)"},
                "status": "partial", "go": {"page": "analysis", "symbol": "510300.SH"},
                "note": {"zh": "指数本身无前复权，用 ETF 代替更准",
                         "en": "Index lacks adjusted prices; the ETF is more accurate"},
            },
            {
                "key": "csi500", "name": {"zh": "中证 500", "en": "CSI 500"},
                "desc": {"zh": "中盘代表", "en": "Mid-cap proxy"},
                "status": "partial", "go": {"page": "analysis", "symbol": "510500.SH"},
            },
            {
                "key": "star50", "name": {"zh": "科创 50", "en": "STAR 50"},
                "desc": {"zh": "科创板龙头", "en": "STAR Market leaders"},
                "status": "partial", "go": {"page": "analysis", "symbol": "588000.SH"},
            },
            {
                "key": "hsi", "name": {"zh": "恒生指数", "en": "Hang Seng"},
                "desc": {"zh": "港股基准（用 2800.HK 代替）", "en": "HK benchmark (via 2800.HK)"},
                "status": "partial", "go": {"page": "analysis", "symbol": "2800.HK"},
            },
            {
                "key": "spx", "name": {"zh": "标普 500", "en": "S&P 500"},
                "desc": {"zh": "美股基准（用 SPY.US 代替）", "en": "US benchmark (via SPY.US)"},
                "status": "partial", "go": {"page": "analysis", "symbol": "SPY.US"},
            },
        ],
    },
    {
        "key": "derivative",
        "label": {"zh": "衍生品与其他", "en": "Derivatives & Others"},
        "icon": "△",
        "items": [
            {
                "key": "futures", "name": {"zh": "股指期货 / 商品期货", "en": "Index & Commodity Futures"},
                "desc": {"zh": "保证金交易，杠杆风险", "en": "Margin traded, leveraged"},
                "status": "none",
                "note": {"zh": "接口未接入", "en": "Data source not integrated"},
            },
            {
                "key": "options", "name": {"zh": "ETF 期权 / 股指期权", "en": "Options"},
                "desc": {"zh": "波动率交易工具", "en": "Volatility instruments"},
                "status": "none",
                "note": {"zh": "接口未接入", "en": "Data source not integrated"},
            },
            {
                "key": "crypto", "name": {"zh": "加密货币", "en": "Crypto"},
                "desc": {"zh": "接口已预留（CRYPTO），未接入数据源", "en": "Code reserved, no data source"},
                "status": "none",
            },
        ],
    },
]

STATUS_META = {
    "full": {"zh": "可完整分析", "en": "Fully supported", "tone": "ok"},
    "partial": {"zh": "部分支持", "en": "Partial", "tone": "warn"},
    "none": {"zh": "未支持", "en": "Not supported", "tone": "off"},
}


def all_groups() -> list[dict]:
    return GROUPS


def stats() -> dict:
    items = [it for g in GROUPS for it in g["items"]]
    counts = {"full": 0, "partial": 0, "none": 0}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    return {"groups": len(GROUPS), "items": len(items), **counts}