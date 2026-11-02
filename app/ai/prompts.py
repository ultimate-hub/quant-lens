"""
Prompt 模板：给 AI 的指令。

设计原则：
    1. 数据由程序提供，AI 只负责判断和解释 —— 禁止 AI 自己回忆数字
    2. 强制 JSON 输出，字段固定，便于程序解析
    3. 明确告诉 AI "目标价只能从候选里选"，防止编造
"""

from __future__ import annotations

SYSTEM_ANALYST = """你是一位严谨的证券投资分析师，正在为一位专业投资者撰写研究笔记。

铁律（必须遵守）：
1. 你只能使用用户提供的【数据事实】进行分析，绝对不许自己回忆或编造任何财务数字、股价、新闻。
2. 目标价、买入区间、止损位只能从用户给出的【候选价位】中选择，或明确说明"候选价位均不合理"并给出理由。
3. 必须同时列出看多和看空的理由，不允许只说好话。
4. 所有结论都要能追溯到具体数据，禁止空泛表述（如"前景广阔"）。
5. 只输出 JSON，不要任何解释性文字、不要 markdown 代码块。
"""

OUTPUT_SCHEMA = """请严格按以下 JSON 结构输出（字段名不能改，类型不能变）：

{
  "ai_score": 0到100的整数,          // 综合定性评分，50为中性
  "rating": "强烈关注"|"关注"|"中性"|"回避"|"强烈回避",
  "confidence": 0到1的小数,           // 你对自己判断的置信度
  "horizon": "1个月内"|"1-3个月"|"3-12个月"|"1年以上",
  "bull_points": ["看多理由1", "看多理由2", "看多理由3"],   // 2-4条，每条必须引用具体数据
  "bear_points": ["看空理由1", "看空理由2"],                // 2-4条
  "catalysts": ["潜在催化剂1", "催化剂2"],                  // 1-3条
  "risks": ["主要风险1", "风险2"],                          // 2-3条
  "chosen_target": {
    "price": 数字,                    // 必须来自候选价位
    "method": "你选择的方法名称",
    "reason": "为什么选这个价位，30字以内"
  },
  "entry_range": {"low": 数字, "high": 数字},
  "stop_loss": 数字,
  "position_pct": 数字,               // 建议仓位百分比 0-30
  "summary": "一句话总结，40字以内"
}"""


def build_analysis_prompt(facts: dict) -> list[dict]:
    """
    组装完整的分析请求。

    facts 包含：
        symbol, name, market, price_metrics, fundamental_metrics,
        anchors_text, news_text(可选), quant_score(可选)
    """
    parts = [
        f"# 分析标的\n"
        f"{facts.get('name', '')}（{facts.get('symbol', '')}，{facts.get('market_label', '')}）",

        f"# 数据事实：价格与风险（由程序从历史行情计算，可直接引用）\n"
        f"{facts.get('price_text', '')}",

        f"# 数据事实：基本面（由程序从财务报表提取，可直接引用）\n"
        f"{facts.get('fundamental_text', '')}",

        f"# 数据事实：量化评分（程序按多维度加权计算）\n"
        f"{facts.get('score_text', '')}",

        f"# 候选价位（目标价只能从这里选）\n"
        f"{facts.get('anchors_text', '')}",
    ]

    if facts.get("news_text"):
        parts.append(f"# 近期新闻（已由程序检索，可作为催化剂/风险的依据）\n"
                     f"{facts['news_text']}")

    parts.append(f"# 输出要求\n{OUTPUT_SCHEMA}")

    return [
        {"role": "system", "content": SYSTEM_ANALYST},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def format_price_facts(m: dict) -> str:
    """把价格指标转成人类可读的事实清单。"""
    def pct(v, d=2):
        return f"{v*100:.{d}f}%" if isinstance(v, (int, float)) else "无数据"

    def num(v, d=2):
        return f"{v:.{d}f}" if isinstance(v, (int, float)) else "无数据"

    lines = [
        f"区间：{m.get('start_date')} 至 {m.get('end_date')}（{m.get('days')}个交易日）",
        f"最新收盘：{num(m.get('latest_close'))}",
        f"区间总收益：{pct(m.get('total_return'))}",
        f"年化收益：{pct(m.get('annual_return'))}",
        f"年化波动率：{pct(m.get('annual_volatility'))}",
        f"最大回撤：{pct(m.get('max_drawdown'))}（最长持续{m.get('max_dd_duration')}个交易日）",
        f"夏普比率：{num(m.get('sharpe'))}　索提诺：{num(m.get('sortino'))}　卡玛：{num(m.get('calmar'))}",
        f"日胜率：{pct(m.get('win_rate'), 1)}　盈亏比：{num(m.get('profit_loss_ratio'))}",
        f"VaR(95%)：{pct(m.get('var_95'))}　CVaR：{pct(m.get('cvar_95'))}",
        f"偏度：{num(m.get('skew'))}　峰度：{num(m.get('kurtosis'))}",
        f"动量：1月{pct(m.get('mom_1m'))}　3月{pct(m.get('mom_3m'))}　"
        f"6月{pct(m.get('mom_6m'))}　12月{pct(m.get('mom_12m'))}",
        f"RSI(14)：{num(m.get('rsi_14'), 1)}　52周位置：{num(m.get('position_52w'))}",
    ]
    macd = m.get("macd") or {}
    if macd:
        lines.append(f"MACD：DIF={macd.get('dif')} DEA={macd.get('dea')} "
                     f"柱={macd.get('hist')}（{macd.get('signal')}）")
    if m.get("beta") is not None:
        lines.append(f"Beta：{num(m.get('beta'))}　Alpha：{pct(m.get('alpha'))}　"
                     f"信息比率：{num(m.get('information_ratio'))}")
    return "\n".join(lines)


def format_fundamental_facts(m: dict) -> str:
    """把基本面指标转成事实清单；没有数据要明确说明。"""
    if not m or not m.get("has_fundamentals"):
        pe = m.get("pe_ttm") if m else None
        pb = m.get("pb") if m else None
        return (f"该标的暂无完整财务数据（非A股市场）。\n"
                f"仅有估值：PE={pe if pe else '无'}　PB={pb if pb else '无'}\n"
                f"请勿在分析中提及 ROE、毛利率、负债率等财务指标，因为你没有这些数据。")

    def num(v, d=2, suffix=""):
        return f"{v:.{d}f}{suffix}" if isinstance(v, (int, float)) else "无数据"

    return "\n".join([
        f"报告期：{m.get('report_type')} {m.get('report_date')}",
        f"估值：PE(TTM)={num(m.get('pe_ttm'))}　PB={num(m.get('pb'))}　"
        f"股息率={num(m.get('dividend_yield'), 2, '%')}　PEG={num(m.get('peg'))}",
        f"盈利能力：ROE={num(m.get('roe'), 2, '%')}　ROIC={num(m.get('roic'), 2, '%')}",
        f"利润率：毛利率={num(m.get('gross_margin'), 2, '%')}　"
        f"净利率={num(m.get('net_margin'), 2, '%')}",
        f"成长性：营收同比={num(m.get('revenue_yoy'), 2, '%')}　"
        f"净利同比={num(m.get('profit_yoy'), 2, '%')}",
        f"财务健康：资产负债率={num(m.get('debt_ratio'), 2, '%')}　"
        f"流动比率={num(m.get('current_ratio'))}",
        f"现金流质量：经营现金流/净利润={num(m.get('ocf_to_profit'))}",
        f"每股：EPS={num(m.get('eps'))}　BPS={num(m.get('bps'))}",
    ])


def format_score_facts(score: dict) -> str:
    """把量化评分转成事实清单。"""
    labels = {"valuation": "估值", "growth": "成长", "quality": "质量",
              "momentum": "动量", "risk": "风险", "sentiment": "情绪"}
    lines = [f"综合量化分：{score.get('composite')}（{score.get('grade')}）",
             f"覆盖维度：{score.get('coverage')}/6"]
    for k, v in (score.get("dimensions") or {}).items():
        if v is not None:
            w = (score.get("weights_used") or {}).get(k, 0)
            lines.append(f"  {labels.get(k, k)}：{v} 分（权重{w}%）")
    missing = score.get("missing") or []
    if missing:
        lines.append(f"  无数据维度：{', '.join(labels.get(x, x) for x in missing)}")
    return "\n".join(lines)
