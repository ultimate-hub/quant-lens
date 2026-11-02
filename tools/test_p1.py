"""P1 验收测试：多市场适配器端到端验证。"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.markets import registry
from app.markets.symbols import parse, to_em_secid, to_tencent, to_yahoo


def sec(title):
    print(f"\n{'='*72}\n  {title}\n{'='*72}")


sec("1. 代码体系转换")
for raw in ["600519", "600519.SH", "000001.SZ", "00700.HK", "AAPL.US"]:
    s = parse(raw)
    print(f"{raw:12s} -> {s.raw:12s} 腾讯={to_tencent(s):12s} "
          f"Yahoo={to_yahoo(s):12s} 东财={to_em_secid(s)}")

sec("2. 三市场 K线")
for raw, beg in [("600519.SH", "20200101"), ("00700.HK", "20200101"), ("AAPL.US", "20180101")]:
    try:
        df, src = registry.get_kline(raw, beg=beg)
        name = df.attrs.get("name", "")
        print(f"{raw:12s} [{src:10s}] {len(df):5d}条  "
              f"{df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[-1]}  "
              f"最新收盘={df['close'].iloc[-1]:.2f}  {name}")
    except Exception as e:
        print(f"{raw:12s} 失败: {type(e).__name__}: {str(e)[:90]}")

sec("3. 三市场 快照")
for raw in ["600519.SH", "00700.HK", "AAPL.US"]:
    try:
        snap, src = registry.get_snapshot(raw)
        print(f"{raw:12s} [{src:10s}] {snap.name:10s} 价={snap.price} "
              f"PE={snap.pe_ttm} PB={snap.pb} 市值={snap.market_cap}")
    except Exception as e:
        print(f"{raw:12s} 失败: {type(e).__name__}: {str(e)[:90]}")

sec("4. A股基本面（F10）")
try:
    f, src = registry.get_fundamentals("600519.SH")
    print(f"[{src}] {f.report_date} {f.report_type}")
    print(f"  营收={f.revenue/1e8:.2f}亿  净利={f.net_profit/1e8:.2f}亿")
    print(f"  营收同比={f.revenue_yoy:.2f}%  净利同比={f.profit_yoy:.2f}%")
    print(f"  ROE={f.roe}  ROIC={f.roic}  毛利率={f.gross_margin}  净利率={f.net_margin}")
    print(f"  资产负债率={f.debt_ratio}  流动比率={f.current_ratio}  EPS={f.eps}  BPS={f.bps}")
except Exception as e:
    print("失败:", type(e).__name__, str(e)[:120])

print("\n美股基本面（预期失败，应给出友好提示）：")
try:
    registry.get_fundamentals("AAPL.US")
    print("  ⚠️ 意外成功")
except Exception as e:
    print(f"  ✅ 正确拒绝: {str(e)[:80]}")

sec("5. 跨市场搜索")
for kw, mkts in [("茅台", None), ("腾讯", None), ("apple", None), ("半导体", ["SH", "SZ"])]:
    res = registry.search(kw, markets=mkts, limit=5)
    print(f"\n关键词 '{kw}' 市场={mkts or '全部'} -> {len(res)}条")
    for r in res:
        print(f"   {r['symbol']:12s} {r['name'][:22]:22s} {r['market']:4s} [{r['source']}]")

sec("6. 数据能力矩阵")
for m, cap in registry.market_coverage().items():
    print(f"  {m:4s} K线={cap['kline']} 快照={cap['snapshot']} 基本面={cap['fundamentals']}")
