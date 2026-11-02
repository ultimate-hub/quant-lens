"""
指标引擎单元测试。

原则：所有测试用合成数据，不依赖网络，结果必须确定。
运行：python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from app.analytics import price, scoring
from app.analytics.fundamental import compute_fundamental_metrics
from app.markets.base import Fundamentals, Snapshot


def make_df(values, start="2023-01-02"):
    """用收盘价造一份最小K线。"""
    idx = pd.bdate_range(start=start, periods=len(values))
    close = np.asarray(values, dtype=float)
    return pd.DataFrame({
        "trade_date": idx.strftime("%Y-%m-%d"),
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": 1000.0, "amount": None, "pct_change": None,
    })


class TestPriceMetrics(unittest.TestCase):

    def test_total_and_annual_return(self):
        df = make_df([1.0, 1.1])
        s = price.to_series(df)
        self.assertAlmostEqual(price.total_return(s), 0.1, places=6)

    def test_max_drawdown(self):
        """1.0 -> 1.2 -> 0.9 应得 -25%"""
        s = price.to_series(make_df([1.0, 1.2, 0.9]))
        self.assertAlmostEqual(price.max_drawdown(s), -0.25, places=6)

    def test_drawdown_stats_recovery(self):
        """涨到1.2跌到0.9再涨回1.25：应记录恢复天数"""
        s = price.to_series(make_df([1.0, 1.2, 0.9, 1.0, 1.25]))
        st = price.drawdown_stats(s)
        self.assertGreaterEqual(st["max_duration"], 2)
        self.assertIsNotNone(st["recovery_days"])

    def test_volatility_zero(self):
        s = price.to_series(make_df([1.0] * 30))
        self.assertAlmostEqual(price.annual_volatility(s), 0.0, places=6)

    def test_sharpe_no_div_zero(self):
        s = price.to_series(make_df([1.0] * 30))
        self.assertEqual(price.sharpe(s), 0.0)

    def test_var_cvar(self):
        """VaR 应比 CVaR 温和（CVaR 是尾部平均，更极端）"""
        rng = np.random.default_rng(42)
        values = 1 + np.cumsum(rng.normal(0, 0.01, 500))
        s = price.to_series(make_df(np.maximum(values, 0.1)))
        var, cvar = price.var_cvar(s, 0.95)
        self.assertLess(var, 0)
        self.assertLessEqual(cvar, var)

    def test_beta_of_identical_series(self):
        """和基准完全一样的序列，Beta 应该≈1，Alpha≈0"""
        vals = list(1 + np.cumsum(np.random.default_rng(7).normal(0, 0.01, 300)))
        s = price.to_series(make_df(vals))
        beta, alpha = price.beta_alpha(s, s)
        self.assertAlmostEqual(beta, 1.0, places=2)
        self.assertAlmostEqual(alpha, 0.0, places=2)

    def test_beta_no_bench(self):
        """没有基准时不能崩，返回 0"""
        s = price.to_series(make_df([1.0, 1.1, 1.2]))
        self.assertEqual(price.beta_alpha(s, pd.Series(dtype=float)), (0.0, 0.0))

    def test_momentum(self):
        """线性上涨：252天前的价格到现在的涨幅"""
        s = price.to_series(make_df(np.linspace(1.0, 2.0, 300)))
        m = price.momentum(s, 21)
        self.assertGreater(m, 0)

    def test_rsi_bounds(self):
        """RSI 必须在 0-100 之间"""
        rng = np.random.default_rng(1)
        s = price.to_series(make_df(1 + np.cumsum(rng.normal(0, 0.01, 200))))
        r = price.rsi(s)
        self.assertGreaterEqual(r, 0)
        self.assertLessEqual(r, 100)

    def test_rsi_all_up_is_high(self):
        """一路上涨 RSI 应接近 100"""
        s = price.to_series(make_df(np.linspace(1.0, 2.0, 100)))
        self.assertGreater(price.rsi(s), 90)

    def test_position_52w(self):
        """涨到区间最高点，位置应≈1"""
        s = price.to_series(make_df(np.linspace(1.0, 2.0, 260)))
        self.assertGreater(price.position_52w(s), 0.95)

    def test_macd_structure(self):
        s = price.to_series(make_df(np.linspace(1.0, 2.0, 200)))
        m = price.macd(s)
        self.assertIn("dif", m)
        self.assertIn(m["signal"], ("金叉偏多", "死叉偏空"))

    def test_compute_all_keys(self):
        df = make_df(np.linspace(1.0, 1.5, 300))
        m = price.compute_price_metrics(df)
        for k in ["annual_return", "max_drawdown", "sharpe", "sortino",
                  "calmar", "rsi_14", "mom_3m", "var_95", "ulcer_index"]:
            self.assertIn(k, m)
        # 没有基准时相对指标应为 None 而不是 0（0 会误导）
        self.assertIsNone(m["beta"])

    def test_compute_too_short(self):
        with self.assertRaises(ValueError):
            price.compute_price_metrics(make_df([1.0]))


class TestScoring(unittest.TestCase):

    def test_scale_and_band(self):
        self.assertAlmostEqual(scoring.scale(5, 0, 10), 50.0)
        self.assertAlmostEqual(scoring.scale(-5, 0, 10), 0.0)     # 截断
        self.assertIsNone(scoring.scale(None, 0, 10))
        self.assertEqual(scoring.band(8, [(10, 90), (20, 70)]), 90.0)
        self.assertEqual(scoring.band(15, [(10, 90), (20, 70)]), 70.0)

    def test_profiles_weights_sum_100(self):
        """每套模式的权重必须合计 100，否则评分会失真"""
        for key, p in scoring.PROFILES.items():
            total = sum(p["weights"].values())
            self.assertEqual(total, 100, f"{key} 权重合计={total}")

    def test_renormalize_when_dimensions_missing(self):
        """
        关键测试：美股只有价格数据时，权重必须重新归一化，
        而不是让缺失维度记 0 分导致总分被拉低。
        """
        price_m = {"annual_volatility": 0.25, "max_drawdown": -0.3,
                   "sharpe": 1.0, "calmar": 0.8, "ulcer_index": 8,
                   "mom_3m": 0.1, "mom_6m": 0.15, "mom_12m": 0.2,
                   "rsi_14": 55, "position_52w": 0.7}
        fund_m = compute_fundamental_metrics(None, None)   # 无基本面

        res = scoring.score_all(price_m, fund_m)
        self.assertIn("growth", res["missing"])
        self.assertIn("quality", res["missing"])
        # 有数据维度的权重之和应接近 100
        self.assertAlmostEqual(sum(res["weights_used"].values()), 100.0, delta=1.0)
        self.assertGreater(res["composite"], 0)

    def test_good_stock_beats_bad_stock(self):
        """优质股（高ROE低负债）综合分必须高于垃圾股"""
        good_price = {"annual_volatility": 0.18, "max_drawdown": -0.2,
                      "sharpe": 1.5, "calmar": 1.2, "ulcer_index": 5,
                      "mom_3m": 0.1, "mom_6m": 0.15, "mom_12m": 0.2,
                      "rsi_14": 55, "position_52w": 0.7}
        good_fund = {"pe_ttm": 12, "pb": 1.5, "dividend_yield": 3.0, "peg": 0.8,
                     "roe": 22, "roic": 18, "gross_margin": 50, "net_margin": 25,
                     "debt_ratio": 25, "ocf_to_profit": 1.2,
                     "revenue_yoy": 20, "profit_yoy": 25}

        bad_price = {"annual_volatility": 0.55, "max_drawdown": -0.7,
                     "sharpe": -0.5, "calmar": -0.2, "ulcer_index": 25,
                     "mom_3m": -0.3, "mom_6m": -0.4, "mom_12m": -0.5,
                     "rsi_14": 25, "position_52w": 0.1}
        bad_fund = {"pe_ttm": 80, "pb": 10, "dividend_yield": 0.2, "peg": 4.0,
                    "roe": 2, "roic": 1, "gross_margin": 12, "net_margin": 2,
                    "debt_ratio": 85, "ocf_to_profit": 0.1,
                    "revenue_yoy": -15, "profit_yoy": -30}

        g = scoring.score_all(good_price, good_fund)
        b = scoring.score_all(bad_price, bad_fund)
        self.assertGreater(g["composite"], b["composite"] + 20,
                           f"优质{g['composite']} 应显著高于垃圾{b['composite']}")

    def test_ai_score_enters_sentiment(self):
        price_m = {"annual_volatility": 0.2, "max_drawdown": -0.2, "sharpe": 1,
                   "calmar": 1, "ulcer_index": 5, "mom_3m": 0.05,
                   "mom_6m": 0.05, "mom_12m": 0.05, "rsi_14": 50, "position_52w": 0.5}
        fund_m = compute_fundamental_metrics(None, None)
        no_ai = scoring.score_all(price_m, fund_m)
        with_ai = scoring.score_all(price_m, fund_m, ai_score=95)
        self.assertIn("sentiment", no_ai["missing"])
        self.assertEqual(with_ai["dimensions"]["sentiment"], 95.0)
        self.assertGreaterEqual(with_ai["composite"], no_ai["composite"])

    def test_profile_changes_result(self):
        """不同模式必须给出不同的综合分"""
        price_m = {"annual_volatility": 0.2, "max_drawdown": -0.25, "sharpe": 0.8,
                   "calmar": 0.6, "ulcer_index": 8, "mom_3m": 0.2,
                   "mom_6m": 0.3, "mom_12m": 0.4, "rsi_14": 65, "position_52w": 0.9}
        fund_m = {"pe_ttm": 40, "pb": 6, "dividend_yield": 0.5, "peg": 2.0,
                  "roe": 18, "roic": 15, "gross_margin": 45, "net_margin": 20,
                  "debt_ratio": 40, "ocf_to_profit": 1.0,
                  "revenue_yoy": 25, "profit_yoy": 30}
        value_s = scoring.score_all(price_m, fund_m, profile="value")["composite"]
        mom_s = scoring.score_all(price_m, fund_m, profile="momentum")["composite"]
        self.assertNotAlmostEqual(value_s, mom_s, places=1)


class TestFundamental(unittest.TestCase):

    def test_peg_calculation(self):
        f = Fundamentals(symbol="X", profit_yoy=20.0)
        snap = Snapshot(pe_ttm=20.0)
        m = compute_fundamental_metrics(f, snap)
        self.assertAlmostEqual(m["peg"], 1.0, places=4)

    def test_peg_none_when_negative_growth(self):
        f = Fundamentals(symbol="X", profit_yoy=-10.0)
        snap = Snapshot(pe_ttm=20.0)
        m = compute_fundamental_metrics(f, snap)
        self.assertIsNone(m["peg"])

    def test_graceful_without_fundamentals(self):
        m = compute_fundamental_metrics(None, Snapshot(pe_ttm=15, pb=2))
        self.assertFalse(m["has_fundamentals"])
        self.assertEqual(m["pe_ttm"], 15)
        self.assertIsNone(m["roe"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
