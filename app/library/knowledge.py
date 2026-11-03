"""
中国金融市场知识库。

设计要点：
    1. 每条都带「对量化分析的影响」—— 这是最有价值的一栏，
       因为很多回测失真就是因为没处理 T+1、涨跌停、停牌
    2. 中英双语，按用户设置显示（zh / en / both）
    3. 纯静态数据，不依赖任何外部接口，永远可用
    4. 只写能确认的规则，不确定的标注「以交易所最新规则为准」

数据结构：
    {
      "id":       唯一标识
      "category": 分类 key（对应 CATEGORIES）
      "title":    {"zh", "en"}
      "summary":  {"zh", "en"}   一句话说清是什么
      "impact":   {"zh", "en"}   对分析/回测的具体影响  ← 核心
      "tags":     [搜索关键词]
    }
"""

from __future__ import annotations

CATEGORIES = [
    {"key": "trading", "label": {"zh": "交易规则", "en": "Trading Rules"}, "icon": "⚙"},
    {"key": "business", "label": {"zh": "业务机制", "en": "Market Mechanisms"}, "icon": "⇄"},
    {"key": "product", "label": {"zh": "产品类型", "en": "Product Types"}, "icon": "◆"},
    {"key": "metric", "label": {"zh": "数据与指标", "en": "Data & Metrics"}, "icon": "▤"},
]


ENTRIES: list[dict] = [

    # ============================================================
    # 交易规则
    # ============================================================
    {
        "id": "t-plus-1",
        "category": "trading",
        "title": {"zh": "T+1 交收制度", "en": "T+1 Settlement"},
        "summary": {
            "zh": "股票当日买入，次一交易日才能卖出。卖出所得资金当日可用于买入，但提现需 T+1。",
            "en": "Shares bought today can only be sold from the next trading day. Sale proceeds "
                  "can be reused for buying the same day, but withdrawal takes T+1.",
        },
        "impact": {
            "zh": "回测若允许「当日买、当日卖」，收益会被系统性高估。做日内策略在 A 股股票上不成立；"
                  "可转债、部分 ETF 是 T+0，可以日内交易。",
            "en": "Backtests that allow same-day buy-and-sell systematically overstate returns. "
                  "Intraday strategies are invalid for A-share stocks; convertible bonds and some "
                  "ETFs are T+0 and can be traded intraday.",
        },
        "tags": ["T+1", "交收", "结算", "settlement", "日内"],
    },
    {
        "id": "price-limit",
        "category": "trading",
        "title": {"zh": "涨跌停限制", "en": "Daily Price Limits"},
        "summary": {
            "zh": "主板 ±10%，ST/*ST ±5%；创业板、科创板 ±20%；北交所 ±30%。"
                  "注册制新股上市前 5 个交易日不设涨跌幅。",
            "en": "Main board ±10%, ST stocks ±5%; ChiNext and STAR Market ±20%; "
                  "Beijing Stock Exchange ±30%. No limit for the first 5 days after a "
                  "registration-based IPO.",
        },
        "impact": {
            "zh": "涨停买不进、跌停卖不出。回测必须模拟：涨停日按「无法成交」处理，"
                  "否则会算出根本拿不到的收益。动量策略尤其容易踩这个坑。",
            "en": "You cannot buy at limit-up or sell at limit-down. Backtests must model this "
                  "as an unfilled order, otherwise the returns are unreachable. Momentum "
                  "strategies are hit hardest.",
        },
        "tags": ["涨跌停", "涨停", "跌停", "limit", "ST"],
    },
    {
        "id": "lot-size",
        "category": "trading",
        "title": {"zh": "最小交易单位", "en": "Minimum Lot Size"},
        "summary": {
            "zh": "买入以 100 股（1 手）为整数倍；科创板 200 股起、可按 1 股递增。"
                  "卖出可以卖零股（不足 100 股的部分必须一次卖出）。",
            "en": "Purchases must be in multiples of 100 shares (1 lot); STAR Market starts at "
                  "200 shares and allows 1-share increments. Odd lots may be sold, but the "
                  "remaining fraction must be sold in one order.",
        },
        "impact": {
            "zh": "资金量小时无法精确按目标权重建仓。例如 5 万元买 1480 元的茅台，"
                  "只能买 33 手 = 48840 元，实际权重会被动偏离目标。",
            "en": "Small accounts cannot hit target weights precisely. With ¥50,000 and a "
                  "¥1,480 stock you can only buy 33 lots (¥48,840), so actual weights drift "
                  "from the intended allocation.",
        },
        "tags": ["最小单位", "手", "整手", "lot", "零股"],
    },
    {
        "id": "trading-cost",
        "category": "trading",
        "title": {"zh": "交易费用构成", "en": "Trading Costs"},
        "summary": {
            "zh": "佣金（约万 2.5，最低 5 元，双向）+ 印花税（卖出 0.05%）"
                  "+ 过户费（0.001%，双向）。",
            "en": "Commission (~0.025%, min ¥5, both sides) + stamp duty (0.05% on sell only) "
                  "+ transfer fee (0.001%, both sides).",
        },
        "impact": {
            "zh": "一次完整买卖往返约 0.11% 成本。年换手 10 次的策略，光成本就吃掉 1.1% 收益；"
                  "高频策略在 A 股很容易被费用吃光 —— 回测必须扣除，否则结论失真。",
            "en": "A full round trip costs about 0.11%. A strategy turning over 10 times a year "
                  "loses 1.1% to costs alone. High-frequency strategies are easily wiped out by "
                  "fees in A-shares — backtests must include them.",
        },
        "tags": ["佣金", "印花税", "过户费", "费用", "commission", "stamp duty"],
    },
    {
        "id": "trading-hours",
        "category": "trading",
        "title": {"zh": "交易时间与集合竞价", "en": "Trading Hours & Call Auction"},
        "summary": {
            "zh": "9:15–9:25 开盘集合竞价，9:30–11:30 / 13:00–15:00 连续竞价。"
                  "深市、创业板、科创板另有 14:57–15:00 收盘集合竞价。",
            "en": "Opening call auction 9:15–9:25; continuous trading 9:30–11:30 and 13:00–15:00. "
                  "Shenzhen, ChiNext and STAR Market add a closing call auction 14:57–15:00.",
        },
        "impact": {
            "zh": "日线回测默认以收盘价成交，但真实交易在 14:57 后是集合竞价，"
                  "大单会冲击价格。用收盘价成交的假设对流动性差的股票偏乐观。",
            "en": "Daily backtests assume fills at the close, but the real close is a call auction "
                  "after 14:57 where large orders move the price. Close-price fills are optimistic "
                  "for illiquid names.",
        },
        "tags": ["交易时间", "集合竞价", "开盘", "收盘", "auction"],
    },
    {
        "id": "suspension",
        "category": "trading",
        "title": {"zh": "停牌与复牌", "en": "Trading Suspension"},
        "summary": {
            "zh": "重大资产重组、重大事项、异常波动等情形下停牌。停牌期间无法交易，"
                  "复牌首日可能大幅跳空。",
            "en": "Trading may be suspended for major restructurings, material events or abnormal "
                  "volatility. No trading occurs while suspended, and the resumption day often "
                  "gaps sharply.",
        },
        "impact": {
            "zh": "停牌期间净值应按停牌前价格计，而不是缺失。复牌跳空会让「止损位」形同虚设 —— "
                  "回测里止损单可能以远低于止损价成交。",
            "en": "NAV should hold at the pre-suspension price rather than going missing. The "
                  "resumption gap can blow through stop levels, so a stop order may fill far "
                  "below its trigger price.",
        },
        "tags": ["停牌", "复牌", "suspension", "跳空"],
    },
    {
        "id": "ex-rights",
        "category": "trading",
        "title": {"zh": "除权除息", "en": "Ex-Rights & Ex-Dividend"},
        "summary": {
            "zh": "分红、送股、转增、配股后，股价按比例下调，称为除权除息。"
                  "前复权把历史价格调整为可比口径。",
            "en": "After dividends, bonus shares, capitalisation or rights issues, the price is "
                  "adjusted downward. Forward-adjusted (qfq) prices make history comparable.",
        },
        "impact": {
            "zh": "必须用复权价算收益，否则除权日会出现虚假暴跌（例如 10 送 10 会让价格腰斩，"
                  "但你没亏钱）。本软件的行情默认为前复权。",
            "en": "Always compute returns on adjusted prices, otherwise ex-rights days show fake "
                  "crashes (a 2-for-1 bonus halves the price without any loss). This app uses "
                  "forward-adjusted prices by default.",
        },
        "tags": ["除权", "除息", "复权", "分红", "adjust", "dividend"],
    },
    {
        "id": "st-rules",
        "category": "trading",
        "title": {"zh": "ST / *ST 特别处理", "en": "ST / *ST Special Treatment"},
        "summary": {
            "zh": "财务或经营异常的公司被实施特别处理，简称前加 ST；退市风险警示为 *ST。"
                  "涨跌停幅度降为 ±5%。",
            "en": "Companies with financial or operational problems get an ST prefix; "
                  "*ST marks delisting risk. The daily limit narrows to ±5%.",
        },
        "impact": {
            "zh": "ST 股涨跌停只有 ±5%，流动性和波动特征与正常股完全不同。"
                  "多数价值/质量策略应把 ST 直接剔除，否则评分会被极端值污染。",
            "en": "ST stocks have ±5% limits and very different liquidity/volatility. Most "
                  "value and quality strategies should exclude them outright, or scores get "
                  "polluted by extremes.",
        },
        "tags": ["ST", "特别处理", "退市", "delisting"],
    },

    # ============================================================
    # 业务机制
    # ============================================================
    {
        "id": "margin-trading",
        "category": "business",
        "title": {"zh": "融资融券", "en": "Margin Trading & Short Selling"},
        "summary": {
            "zh": "向券商借钱买股（融资）或借股卖出（融券）。仅限标的池内的股票，"
                  "需支付利息，维持担保比例低于 130% 会被强制平仓。",
            "en": "Borrow cash to buy (margin) or borrow shares to sell short. Only eligible "
                  "securities qualify, interest applies, and a maintenance ratio below 130% "
                  "triggers forced liquidation.",
        },
        "impact": {
            "zh": "融资余额上升常被视为看多信号，但它是滞后指标。做空策略要确认标的在融券池内，"
                  "否则逻辑不成立；强制平仓会放大下跌（踩踏）。",
            "en": "Rising margin balances are read as bullish but are lagging. Short strategies "
                  "must confirm the stock is borrowable; forced liquidations amplify sell-offs.",
        },
        "tags": ["融资", "融券", "两融", "margin", "short", "平仓"],
    },
    {
        "id": "stock-connect",
        "category": "business",
        "title": {"zh": "沪深港通（港股通/陆股通）", "en": "Stock Connect"},
        "summary": {
            "zh": "内地与香港投资者互相买卖对方市场股票。有每日额度限制，交易日历取两地交集，"
                  "结算用人民币、存在汇兑成本。",
            "en": "Mainland and Hong Kong investors trade each other's markets. Daily quotas apply, "
                  "the calendar is the intersection of both markets, settlement is in RMB with "
                  "FX costs.",
        },
        "impact": {
            "zh": "港股通标的与港股本地的交易日不同（A 股休市时可能无法交易港股通标的），"
                  "回测跨市场组合必须对齐两地交易日历，否则会出现「凭空多出的交易日」。",
            "en": "Stock Connect names do not trade on all HK days (mainland holidays block them). "
                  "Cross-market backtests must align both calendars, or phantom trading days appear.",
        },
        "tags": ["港股通", "陆股通", "北向", "南向", "connect", "沪深港通"],
    },
    {
        "id": "ipo-subscription",
        "category": "business",
        "title": {"zh": "打新（新股申购）", "en": "IPO Subscription"},
        "summary": {
            "zh": "按持仓市值配售额度，沪市/深市各需 1 万元市值起。中签后缴款，"
                  "科创板、创业板需单独开通权限。",
            "en": "Allocation is based on held market value, starting from ¥10,000 in each of "
                  "Shanghai and Shenzhen. Payment is due after winning the ballot; STAR Market "
                  "and ChiNext require separate permissions.",
        },
        "impact": {
            "zh": "打新收益是独立的低风险收益来源，会显著抬高小账户的实际收益率。"
                  "把打新收益混进选股策略的业绩里，会高估策略本身的能力。",
            "en": "IPO flipping is a separate low-risk return stream that can materially lift a "
                  "small account's realised return. Mixing it into a stock-picking strategy "
                  "overstates the strategy's own skill.",
        },
        "tags": ["打新", "新股", "申购", "中签", "IPO"],
    },
    {
        "id": "convertible-bond",
        "category": "business",
        "title": {"zh": "可转债申购与交易", "en": "Convertible Bonds"},
        "summary": {
            "zh": "信用申购（无需市值、无需预缴款），中签后缴款。上市后 T+0 交易、无涨跌停"
                  "（但有 20%/30% 临时停牌机制）。",
            "en": "Credit-based subscription (no holdings or prepayment needed), payment after "
                  "winning. Trades T+0 with no daily limit, but 20%/30% temporary halts apply.",
        },
        "impact": {
            "zh": "可转债是 A 股少数能 T+0 的品种，且涨跌幅机制不同。"
                  "把可转债当股票回测（套用 ±10% 涨跌停和 T+1）会得出完全错误的结论。",
            "en": "Convertible bonds are among the few T+0 instruments in A-shares with different "
                  "limit mechanics. Backtesting them with stock rules (±10% limits, T+1) gives "
                  "completely wrong results.",
        },
        "tags": ["可转债", "转债", "T+0", "convertible"],
    },
    {
        "id": "block-trade",
        "category": "business",
        "title": {"zh": "大宗交易", "en": "Block Trades"},
        "summary": {
            "zh": "单笔规模达到门槛（通常 30 万股或 200 万元）可通过大宗交易平台撮合，"
                  "价格通常较市价折价，买入后 T+1 才能卖出。",
            "en": "Orders above the threshold (typically 300k shares or ¥2m) can be matched on the "
                  "block platform, usually at a discount, with a lock-up until the next day.",
        },
        "impact": {
            "zh": "频繁的大宗折价成交常被解读为大股东/机构减持意愿。"
                  "折价率异常放大是风险信号，可作为情绪因子使用。",
            "en": "Frequent discounted blocks often signal institutional or major-holder selling. "
                  "Abnormally wide discounts are a risk signal usable as a sentiment factor.",
        },
        "tags": ["大宗交易", "折价", "block trade"],
    },
    {
        "id": "share-pledge",
        "category": "business",
        "title": {"zh": "股票质押", "en": "Share Pledging"},
        "summary": {
            "zh": "股东把股票质押给金融机构融资。股价跌到平仓线会被强制卖出，形成抛压。",
            "en": "Shareholders pledge shares as collateral. If the price falls to the liquidation "
                  "line, forced selling adds downward pressure.",
        },
        "impact": {
            "zh": "高质押比例 + 股价接近平仓线 = 潜在闪崩风险。"
                  "这类标的的回撤分布有明显厚尾，用正态假设的风险模型会低估风险。",
            "en": "High pledge ratios near liquidation lines carry flash-crash risk. Such names "
                  "have fat-tailed drawdowns, so normal-distribution risk models understate risk.",
        },
        "tags": ["质押", "平仓线", "pledge", "风险"],
    },
    {
        "id": "lockup",
        "category": "business",
        "title": {"zh": "限售解禁", "en": "Lock-up Expiry"},
        "summary": {
            "zh": "IPO 前股东、定增参与方的股份有锁定期（通常 12–36 个月），到期后可上市流通。",
            "en": "Pre-IPO holders and private placement participants face lock-ups (usually "
                  "12–36 months) before their shares become tradable.",
        },
        "impact": {
            "zh": "大额解禁前后常出现超额下跌。解禁规模占流通盘比例可作为事件驱动因子的输入，"
                  "但需要提前获取解禁日历。",
            "en": "Large lock-up expiries often precede underperformance. The unlocked ratio to "
                  "free float works as an event factor, but needs a forward unlock calendar.",
        },
        "tags": ["解禁", "限售", "lockup", "事件"],
    },
    {
        "id": "dividend",
        "category": "business",
        "title": {"zh": "分红送转", "en": "Dividends & Bonus Shares"},
        "summary": {
            "zh": "现金分红（派息）、送股（送红股）、转增（资本公积转增股本）。"
                  "实施日股价按比例调整。",
            "en": "Cash dividends, bonus shares and capital-reserve capitalisation. The price is "
                  "adjusted proportionally on the ex-date.",
        },
        "impact": {
            "zh": "红利策略的收益必须包含分红再投资，否则会低估。"
                  "股息率 = 每股分红 / 股价，高股息不等于高回报（可能是股价跌出来的）。",
            "en": "Dividend strategies must reinvest dividends or returns are understated. "
                  "A high yield does not imply high return — it may just reflect a fallen price.",
        },
        "tags": ["分红", "送股", "转增", "股息", "dividend"],
    },
    {
        "id": "northbound",
        "category": "business",
        "title": {"zh": "北向资金（陆股通）", "en": "Northbound Capital"},
        "summary": {
            "zh": "境外资金通过陆股通买入 A 股的净流入。2024 年 8 月起停止盘中实时披露，"
                  "改为每日收盘后公布成交额。",
            "en": "Net inflows from overseas via Stock Connect. Real-time intraday disclosure "
                  "ended in August 2024; turnover is now published after the close.",
        },
        "impact": {
            "zh": "实时北向数据已不可得，任何声称「盘中跟随北向资金」的策略现在都无法复现。"
                  "只能用日度数据做滞后因子，预测力已大幅下降。",
            "en": "Intraday northbound data is no longer available, so strategies that claim to "
                  "follow it intraday cannot be reproduced. Only lagged daily data remains, with "
                  "much weaker predictive power.",
        },
        "tags": ["北向", "陆股通", "外资", "northbound"],
    },

    # ============================================================
    # 产品类型
    # ============================================================
    {
        "id": "board-main",
        "category": "product",
        "title": {"zh": "主板（沪市 60 / 深市 00）", "en": "Main Board"},
        "summary": {
            "zh": "规模较大、经营稳定的成熟企业。沪市主板代码 60 开头，深市主板 00 开头。"
                  "涨跌停 ±10%。",
            "en": "Mature, larger-cap companies. Shanghai codes start with 60, Shenzhen with 00. "
                  "Daily limit ±10%.",
        },
        "impact": {
            "zh": "主板流动性最好、数据最全，适合做长期因子研究。"
                  "但大盘股因子（如小市值效应）在这里基本失效。",
            "en": "Best liquidity and data coverage, ideal for long-horizon factor research. "
                  "However size factors such as the small-cap effect largely fail here.",
        },
        "tags": ["主板", "沪市", "深市", "main board"],
    },
    {
        "id": "board-chinext",
        "category": "product",
        "title": {"zh": "创业板（300 开头）", "en": "ChiNext"},
        "summary": {
            "zh": "成长型创新创业企业，代码 300 开头，涨跌停 ±20%，需开通交易权限。",
            "en": "Growth and innovative companies, codes starting with 300, ±20% daily limit, "
                  "trading permission required.",
        },
        "impact": {
            "zh": "波动率显著高于主板，同样的止损比例在这里更容易被触发（被噪声打掉）。"
                  "跨板块比较时必须做波动率标准化。",
            "en": "Much higher volatility than the main board, so the same stop percentage gets "
                  "triggered by noise more often. Cross-board comparisons need volatility "
                  "normalisation.",
        },
        "tags": ["创业板", "300", "ChiNext"],
    },
    {
        "id": "board-star",
        "category": "product",
        "title": {"zh": "科创板（688 开头）", "en": "STAR Market"},
        "summary": {
            "zh": "硬科技企业，代码 688 开头，涨跌停 ±20%，最少买 200 股、可按 1 股递增，"
                  "需 50 万元资产 + 2 年经验开通。",
            "en": "Hard-tech companies, codes starting with 688, ±20% limit, minimum 200 shares "
                  "with 1-share increments, requires ¥500k assets and 2 years' experience.",
        },
        "impact": {
            "zh": "允许未盈利企业上市，PE 常为负或无意义。用 PE 做估值筛选会把整个科创板排除，"
                  "这类标的应改用 PS、研发投入占比等指标。",
            "en": "Unprofitable companies may list, so PE is often negative or meaningless. "
                  "PE-based screening excludes the whole board; use PS or R&D intensity instead.",
        },
        "tags": ["科创板", "688", "STAR"],
    },
    {
        "id": "board-bse",
        "category": "product",
        "title": {"zh": "北交所（8 / 4 开头）", "en": "Beijing Stock Exchange"},
        "summary": {
            "zh": "服务创新型中小企业，涨跌停 ±30%，流动性明显弱于沪深。",
            "en": "Serves innovative SMEs, ±30% daily limit, notably weaker liquidity than "
                  "Shanghai and Shenzhen.",
        },
        "impact": {
            "zh": "流动性差导致滑点极大，回测按收盘价成交会严重高估可实现收益。"
                  "建议对北交所标的额外扣除 1–2% 的滑点假设。",
            "en": "Poor liquidity means large slippage; close-price fills badly overstate "
                  "achievable returns. Assume an extra 1–2% slippage for BSE names.",
        },
        "tags": ["北交所", "BSE", "新三板"],
    },
    {
        "id": "etf",
        "category": "product",
        "title": {"zh": "ETF（交易型开放式指数基金）", "en": "ETFs"},
        "summary": {
            "zh": "在交易所像股票一样买卖的指数基金。股票型 ETF 为 T+1；"
                  "跨境 ETF、债券 ETF、黄金 ETF 多为 T+0。",
            "en": "Index funds traded on-exchange like stocks. Equity ETFs are T+1; cross-border, "
                  "bond and gold ETFs are mostly T+0.",
        },
        "impact": {
            "zh": "ETF 是构建基准和做行业配置的首选（费率低、无个股风险）。"
                  "注意跨境 ETF 常有较大溢价，溢价买入会额外承担回归损失。",
            "en": "ETFs are the natural choice for benchmarks and sector allocation (low fees, no "
                  "single-stock risk). Cross-border ETFs often trade at premiums that later "
                  "mean-revert, costing the buyer.",
        },
        "tags": ["ETF", "指数基金", "T+0", "跨境"],
    },
    {
        "id": "lof",
        "category": "product",
        "title": {"zh": "LOF（上市开放式基金）", "en": "LOFs"},
        "summary": {
            "zh": "既可在场内买卖，也可在场外申购赎回的基金。场内价格与净值可能偏离。",
            "en": "Funds tradable on-exchange and also subscribable/redeemable off-exchange. "
                  "The exchange price can diverge from NAV.",
        },
        "impact": {
            "zh": "折溢价套利是 LOF 的主要玩法，但申赎有 T+2 延迟，套利窗口常已关闭。"
                  "用 LOF 做长期持有需注意流动性远不如 ETF。",
            "en": "Premium/discount arbitrage is the main use, but subscription/redemption takes "
                  "T+2, by which time the window often closes. Long-term holders face much worse "
                  "liquidity than ETFs.",
        },
        "tags": ["LOF", "折溢价", "套利"],
    },
    {
        "id": "reits",
        "category": "product",
        "title": {"zh": "REITs（基础设施公募 REITs）", "en": "Public REITs"},
        "summary": {
            "zh": "投向仓储物流、产业园区、高速公路等基础设施，强制高比例分红。"
                  "涨跌幅 ±10%，T+1。",
            "en": "Invest in infrastructure such as logistics, industrial parks and highways, with "
                  "mandatory high payout. ±10% limit, T+1.",
        },
        "impact": {
            "zh": "REITs 的估值逻辑是「现金流折现 + 分红率」，不适用 PE/PB 那套。"
                  "用股票因子模型评估 REITs 会得出误导性结论。",
            "en": "REITs are valued on discounted cash flow and distribution yield, not PE/PB. "
                  "Equity factor models give misleading results on them.",
        },
        "tags": ["REITs", "基础设施", "分红"],
    },
    {
        "id": "otc-fund",
        "category": "product",
        "title": {"zh": "场外基金（申购赎回）", "en": "Off-exchange Mutual Funds"},
        "summary": {
            "zh": "通过银行、第三方平台按净值申购赎回。确认通常 T+1，赎回到账 T+2 至 T+7。",
            "en": "Subscribed and redeemed at NAV via banks or platforms. Confirmation is usually "
                  "T+1; redemption proceeds arrive T+2 to T+7.",
        },
        "impact": {
            "zh": "场外基金不能用日线价格做择时回测 —— 申赎有确认延迟，且当天 15:00 前才算当日净值。"
                  "本软件的基金模块用的是净值曲线，不做日内假设。",
            "en": "Off-exchange funds cannot be backtested with daily prices for timing: "
                  "confirmation lags, and only orders before 15:00 get that day's NAV. "
                  "This app's fund module uses NAV curves, not intraday assumptions.",
        },
        "tags": ["场外基金", "申购", "赎回", "净值", "mutual fund"],
    },

    # ============================================================
    # 数据与指标
    # ============================================================
    {
        "id": "adjustment",
        "category": "metric",
        "title": {"zh": "复权方式（前复权 / 后复权）", "en": "Price Adjustment (qfq / hfq)"},
        "summary": {
            "zh": "前复权：把历史价格按分红送股调整到与当前可比（最新价不变）。"
                  "后复权：把当前价格调整到与历史可比（最早价不变）。",
            "en": "Forward adjustment (qfq) rescales history to be comparable with today's price "
                  "(latest price unchanged). Backward adjustment (hfq) keeps the earliest price "
                  "fixed instead.",
        },
        "impact": {
            "zh": "算收益率必须用复权价。前复权适合看图形和近期涨跌，"
                  "后复权适合算长期累计收益 —— 两者算出的历史收益不同，别混用。",
            "en": "Returns must use adjusted prices. Forward adjustment suits charts and recent "
                  "moves; backward adjustment suits long-run cumulative returns. Mixing them "
                  "produces inconsistent numbers.",
        },
        "tags": ["复权", "前复权", "后复权", "adjustment", "qfq"],
    },
    {
        "id": "turnover-rate",
        "category": "metric",
        "title": {"zh": "换手率", "en": "Turnover Rate"},
        "summary": {
            "zh": "成交量 / 流通股本。衡量筹码交换活跃度，通常与股价波动正相关。",
            "en": "Volume divided by free float. Measures how actively shares change hands and "
                  "usually correlates positively with volatility.",
        },
        "impact": {
            "zh": "换手率骤增往往先于趋势变化，但也是「见顶」信号之一。"
                  "低换手股的回测必须加滑点，否则实盘无法按回测价成交。",
            "en": "Turnover spikes often precede trend changes and are also a topping signal. "
                  "Low-turnover names need slippage added to backtests, or live fills will "
                  "differ from tested prices.",
        },
        "tags": ["换手率", "成交量", "turnover", "流动性"],
    },
    {
        "id": "pe-ttm",
        "category": "metric",
        "title": {"zh": "市盈率 PE（TTM / 静态 / 动态）", "en": "P/E Ratio (TTM / Static / Forward)"},
        "summary": {
            "zh": "股价 / 每股收益。TTM 用最近 12 个月滚动利润，静态用上一年度，"
                  "动态用预测利润。",
            "en": "Price divided by earnings per share. TTM uses trailing 12-month earnings, "
                  "static uses last fiscal year, forward uses estimates.",
        },
        "impact": {
            "zh": "亏损公司 PE 为负或无意义，直接按「PE < 20」筛会把亏损股和周期股底部全部排除。"
                  "跨行业比 PE 也没意义（银行 5 倍和半导体 50 倍都合理）。",
            "en": "PE is negative or meaningless for loss-makers, so a simple 'PE < 20' filter "
                  "excludes all unprofitable firms and cyclical troughs. Cross-industry PE "
                  "comparison is also invalid (5x for banks and 50x for semis are both normal).",
        },
        "tags": ["市盈率", "PE", "TTM", "估值"],
    },
    {
        "id": "pb-ratio",
        "category": "metric",
        "title": {"zh": "市净率 PB", "en": "Price-to-Book (P/B)"},
        "summary": {
            "zh": "股价 / 每股净资产。重资产行业（银行、地产、钢铁）比 PE 更稳定可靠。",
            "en": "Price divided by book value per share. More reliable than PE for "
                  "asset-heavy sectors such as banks, property and steel.",
        },
        "impact": {
            "zh": "PB 低说明便宜，但也可能是资产质量差（坏账、减值）。"
                  "轻资产公司（软件、消费）的高 PB 是正常的，别一刀切。",
            "en": "A low PB can mean cheap or poor asset quality (bad loans, impairments). High PB "
                  "is normal for asset-light businesses such as software and consumer — "
                  "do not apply one threshold everywhere.",
        },
        "tags": ["市净率", "PB", "估值", "破净"],
    },
    {
        "id": "roe",
        "category": "metric",
        "title": {"zh": "ROE（净资产收益率）", "en": "Return on Equity"},
        "summary": {
            "zh": "净利润 / 净资产。衡量股东资金的赚钱效率，巴菲特最看重的单一指标。",
            "en": "Net profit divided by equity. Measures how efficiently shareholder capital is "
                  "used — the single metric Buffett watches most.",
        },
        "impact": {
            "zh": "高 ROE 若靠高杠杆撑起来（如地产、银行），风险远大于靠高利润率撑起来的"
                  "（如白酒）。必须结合负债率一起看，否则会把「危险的高 ROE」当成优质。",
            "en": "High ROE driven by leverage (property, banks) is far riskier than high ROE "
                  "driven by margins (liquor). Always read it together with the debt ratio, or "
                  "dangerous leverage looks like quality.",
        },
        "tags": ["ROE", "净资产收益率", "盈利", "杜邦"],
    },
    {
        "id": "dragon-tiger",
        "category": "metric",
        "title": {"zh": "龙虎榜", "en": "Dragon-Tiger List"},
        "summary": {
            "zh": "交易所每日公布异动股票的买卖前五营业部/机构席位，用于观察资金动向。",
            "en": "Exchanges publish the top five buying and selling seats for stocks with abnormal "
                  "moves, revealing who is trading.",
        },
        "impact": {
            "zh": "游资席位（知名营业部）上榜常预示短期继续炒作，但持续性差（1–3 天）。"
                  "机构席位对倒信息噪音大，需要结合成交占比过滤。",
            "en": "Well-known speculative seats often signal a short continuation (1–3 days). "
                  "Institutional seats are noisy and should be filtered by the share of total "
                  "turnover.",
        },
        "tags": ["龙虎榜", "游资", "席位", "dragon tiger"],
    },
    {
        "id": "index-method",
        "category": "metric",
        "title": {"zh": "指数编制方法", "en": "Index Construction"},
        "summary": {
            "zh": "主流宽基多为市值加权（如沪深 300），也有等权、因子加权。"
                  "成分股定期调整（通常每半年）。",
            "en": "Most broad indices are cap-weighted (e.g. CSI 300), with equal-weight and "
                  "factor-weighted variants. Constituents are rebalanced periodically "
                  "(usually semi-annually).",
        },
        "impact": {
            "zh": "用指数做基准时要注意：市值加权指数天然偏向大盘股，"
                  "小盘策略跑赢它是正常的，不代表策略优秀（要看信息比率和风格暴露）。",
            "en": "Cap-weighted benchmarks are structurally large-cap biased, so a small-cap "
                  "strategy beating one is expected and does not prove skill — check the "
                  "information ratio and style exposure instead.",
        },
        "tags": ["指数", "编制", "沪深300", "基准", "index"],
    },
]


def all_entries() -> list[dict]:
    return ENTRIES


def by_category(cat: str) -> list[dict]:
    return [e for e in ENTRIES if e["category"] == cat]


def search(q: str) -> list[dict]:
    """在标题/摘要/影响/标签里做不区分大小写的模糊匹配。"""
    q = (q or "").strip().lower()
    if not q:
        return []
    out = []
    for e in ENTRIES:
        hay = " ".join([
            e["title"]["zh"], e["title"]["en"],
            e["summary"]["zh"], e["summary"]["en"],
            e["impact"]["zh"], e["impact"]["en"],
            " ".join(e.get("tags", [])),
        ]).lower()
        if q in hay:
            out.append(e)
    return out


def stats() -> dict:
    return {
        "total": len(ENTRIES),
        "categories": len(CATEGORIES),
        "by_category": {c["key"]: len(by_category(c["key"])) for c in CATEGORIES},
    }
