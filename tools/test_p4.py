"""P4 验收：AI 定性分析全链路（真实行情 + 模拟AI）。"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai import channels as ch
from app.analysis import service
from app.core.db import init_db

MOCK = "http://127.0.0.1:8899"


def show(res: dict, title: str):
    print(f"\n{'='*74}\n  {title}\n{'='*74}")
    print(f"标的: {res['name']} ({res['symbol']})  {res['market_label']}")
    print(f"数据: {res['meta']['kline_rows']}条K线 来源={res['meta']['kline_source']} "
          f"基本面={'有' if res['meta']['has_fundamentals'] else '无'}")

    f = res["final"]
    print(f"\n【最终结论】")
    print(f"  综合分 {f['composite']} ({f['grade']})   "
          f"量化分 {f['quant_only']}  AI分 {f['ai_only']}")
    print(f"  评级 {f['rating']}  置信度 {f['confidence']}  周期 {f['horizon']}")
    print(f"  现价 {f['current_price']}  目标价 {f['target_price']} "
          f"({f['target_method']})")
    er = f["entry_range"] or {}
    print(f"  买入区间 {er.get('low')} ~ {er.get('high')}   止损 {f['stop_loss']}   "
          f"仓位 {f['position_pct']}%")
    print(f"  风险收益比 {f['risk_reward']}")
    print(f"  一句话: {f['summary']}")

    dims = " ".join(f"{k}={v}" for k, v in f["dimensions"].items() if v is not None)
    print(f"  维度: {dims}")

    ai = res.get("ai")
    if ai:
        print(f"\n【AI 分析】")
        for b in ai["bull_points"]:
            print(f"  ▲ {b}")
        for b in ai["bear_points"]:
            print(f"  ▼ {b}")
        if ai["catalysts"]:
            print(f"  催化: {'; '.join(ai['catalysts'])}")
        print(f"  风险: {'; '.join(ai['risks'])}")

    if res["meta"].get("ai_warnings"):
        print(f"\n  ⚠️ 校验告警:")
        for w in res["meta"]["ai_warnings"]:
            print(f"     - {w}")
    if res["meta"].get("ai_error"):
        print(f"\n  ❌ AI错误: {res['meta']['ai_error']}")


def main():
    init_db()

    # 清理并创建模拟渠道
    for c in ch.list_channels():
        if c.name.startswith("MOCK-"):
            ch.delete_channel(c.id)
    ch.add_channel("MOCK-正常", f"{MOCK}/ok", "mock-large", "sk-1", is_default=True)
    ch.add_channel("MOCK-幻觉", f"{MOCK}/hallucinate", "mock-large", "sk-2")

    # ---------- 1. 正常流程
    r = service.analyze("600519.SH", profile="balanced")
    show(r, "1. 贵州茅台 · 完整分析（AI正常）")

    # ---------- 2. 幻觉防护
    halluc_id = next(c.id for c in ch.list_channels() if c.name == "MOCK-幻觉")
    r2 = service.analyze("600519.SH", profile="balanced", channel_id=halluc_id)
    show(r2, "2. 同一标的 · AI编造目标价（测试防幻觉）")
    if r2["final"]["target_price"] == 999999.0:
        print("\n  ❌ 防幻觉失败：AI编造的目标价被采纳了！")
    else:
        print(f"\n  ✅ 防幻觉生效：目标价已回退到 {r2['final']['target_price']}")

    # ---------- 3. 不同模式
    print(f"\n{'='*74}\n  3. 同一标的在不同分析模式下的结论\n{'='*74}")
    print(f"{'模式':<12s} {'综合分':>7s} {'评级':>8s} {'目标价':>10s}")
    for p in ["balanced", "value", "growth", "quality", "momentum"]:
        rr = service.analyze("600519.SH", profile=p, use_ai=False)
        print(f"{p:<12s} {rr['final']['composite']:>7.1f} "
              f"{rr['final']['rating']:>8s} {str(rr['final']['target_price']):>10s}")

    # ---------- 4. 美股（无基本面）
    r3 = service.analyze("AAPL.US", profile="balanced")
    show(r3, "4. 苹果 · 无财务数据时的降级处理")

    # ---------- 5. 无 AI 也能用
    r4 = service.analyze("00700.HK", profile="balanced", use_ai=False)
    print(f"\n{'='*74}\n  5. 不启用AI（无key场景）\n{'='*74}")
    print(f"  {r4['name']} 综合分 {r4['final']['composite']} 评级 {r4['final']['rating']}")
    print(f"  目标价 {r4['final']['target_price']} 止损 {r4['final']['stop_loss']}")
    print(f"  ✅ 量化部分独立可用")

    # ---------- 6. 清理
    for c in ch.list_channels():
        if c.name.startswith("MOCK-"):
            ch.delete_channel(c.id)
    print("\n" + "=" * 74)
    print("  P4 验收完成")
    print("=" * 74)


if __name__ == "__main__":
    main()
