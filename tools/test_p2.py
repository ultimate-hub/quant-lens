"""P2 验收：指标引擎在真实数据上的表现。"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analytics import scoring
from app.analytics.fundamental import compute_fundamental_metrics
from app.analytics.price import compute_price_metrics
from app.markets import registry


def run(symbol: str, bench: str | None, profile: str = "balanced"):
    print(f"\n{'='*74}")
    print(f"  {symbol}   基准={bench or '无'}   模式={profile}")
    print("=" * 74)

    df, src = registry.get_kline(symbol, beg="20190101")
    bdf = None
    if bench:
        try:
            bdf, _ = registry.get_kline(bench, beg="20190101")
        except Exception as e:  # noqa: BLE001
            print(f"  基准获取失败: {str(e)[:60]}")

    pm = compute_price_metrics(df, bdf)

    # 基本面（美股/港股会失败，属预期）
    fm = {}
    try:
        f, _ = registry.get_fundamentals(symbol)
        snap, _ = registry.get_snapshot(symbol)
        fm = compute_fundamental_metrics(f, snap)
    except Exception:  # noqa: BLE001
        try:
            snap, _ = registry.get_snapshot(symbol)
            fm = compute_fundamental_metrics(None, snap)
        except Exception:  # noqa: BLE001
            fm = compute_fundamental_metrics(None, None)

    print(f"  数据源={src}  K线={len(df)}条")
    print(f"  年化={pm['annual_return']*100:6.2f}%  波动={pm['annual_volatility']*100:5.2f}%  "
          f"回撤={pm['max_drawdown']*100:7.2f}%  夏普={pm['sharpe']:5.2f}")
    print(f"  动量: 1月={pm['mom_1m']*100:6.2f}%  3月={pm['mom_3m']*100:6.2f}%  "
          f"12月={pm['mom_12m']*100:6.2f}%  RSI={pm['rsi_14']:.1f}  MACD={pm['macd']['signal']}")
    if pm.get("beta") is not None:
        print(f"  Beta={pm['beta']:.2f}  Alpha={pm['alpha']*100:.2f}%  "
              f"信息比率={pm['information_ratio']:.2f}  相对强弱={pm['relative_strength_60d']*100:.2f}%")
    if fm.get("has_fundamentals"):
        print(f"  基本面({fm['report_type']} {fm['report_date']}): ROE={fm['roe']} "
              f"毛利率={fm['gross_margin']:.1f}% 负债率={fm['debt_ratio']:.1f}% "
              f"营收增速={fm['revenue_yoy']:.1f}%")
    print(f"  PE={fm.get('pe_ttm')}  PB={fm.get('pb')}  PEG={fm.get('peg')}")

    res = scoring.score_all(pm, fm, profile=profile)
    print(f"\n  综合分: {res['composite']}  ({res['grade']})   覆盖{res['coverage']}/6维度")
    for k, v in res["dimensions"].items():
        label = scoring.DIMENSION_LABELS[k]
        w = res["weights_used"].get(k)
        bar = "█" * int((v or 0) / 5) if v is not None else "（无数据）"
        print(f"    {label:6s} {('%5.1f' % v) if v is not None else '   — '}  "
              f"权重{('%4.1f' % w) if w else '  - '}  {bar}")
    if res["missing"]:
        print(f"    缺失维度: {[scoring.DIMENSION_LABELS[m] for m in res['missing']]}")
    return res


print("\n" + "#" * 74)
print("#  真实数据验收")
print("#" * 74)

run("600519.SH", "000300.SH")
run("00700.HK", None)
run("AAPL.US", "SPY.US")

print(f"\n{'='*74}\n  同一标的不同模式对比（600519.SH）\n{'='*74}")
df, _ = registry.get_kline("600519.SH", beg="20190101")
bdf, _ = registry.get_kline("000300.SH", beg="20190101")
pm = compute_price_metrics(df, bdf)
f, _ = registry.get_fundamentals("600519.SH")
snap, _ = registry.get_snapshot("600519.SH")
fm = compute_fundamental_metrics(f, snap)

print(f"{'模式':<10s} {'综合分':>7s} {'等级':>6s}   维度分")
for p in scoring.list_profiles():
    r = scoring.score_all(pm, fm, profile=p["key"])
    dims = " ".join(f"{scoring.DIMENSION_LABELS[k][:2]}{v:.0f}"
                    for k, v in r["dimensions"].items() if v is not None)
    print(f"{p['label']:<10s} {r['composite']:>7.1f} {r['grade']:>6s}   {dims}")
