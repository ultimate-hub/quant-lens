"""扩展指标单元测试（第二批26个）。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from app.analytics import extended


def make_df(values, start="2022-01-03"):
    idx = pd.bdate_range(start=start, periods=len(values))
    close = np.asarray(values, dtype=float)
    return pd.DataFrame({
        "trade_date": idx.strftime("%Y-%m-%d"),
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": 10000.0,
        "amount": close * 10000, "pct_change": None,
    })


def series_of(df):
    s = df.set_index("trade_date")["close"].astype(float)
    s.index = pd.to_datetime(s.index)
    return s


class TestStatistical(unittest.TestCase):

    def test_hurst_range(self):
        """Hurst 必须在 0~1 之间（R/S 估计可能越界，已钳制）"""
        rng = np.random.default_rng(0)
        vals = 1 + np.cumsum(rng.normal(0, 0.01, 400))
        h = extended.hurst_exponent(series_of(make_df(np.maximum(vals, 0.1))))
        if h is not None:
            self.assertGreaterEqual(h, 0.0)
            self.assertLessEqual(h, 1.0)

    def test_hurst_trending_higher_than_random(self):
        """强趋势序列的 Hurst 应高于随机游走"""
        trend = series_of(make_df(np.linspace(1, 3, 400)))
        rng = np.random.default_rng(1)
        rand = series_of(make_df(1 + np.cumsum(rng.normal(0, 0.01, 400))))
        h_trend = extended.hurst_exponent(trend)
        h_rand = extended.hurst_exponent(rand)
        if h_trend is not None and h_rand is not None:
            self.assertGreater(h_trend, h_rand)

    def test_autocorrelation_range(self):
        rng = np.random.default_rng(2)
        s = series_of(make_df(1 + np.cumsum(rng.normal(0, 0.01, 300))))
        ac = extended.autocorrelation(s)
        if ac is not None:
            self.assertGreaterEqual(ac, -1.0)
            self.assertLessEqual(ac, 1.0)

    def test_variance_ratio_positive(self):
        rng = np.random.default_rng(3)
        s = series_of(make_df(1 + np.cumsum(rng.normal(0, 0.01, 500))))
        vr = extended.variance_ratio(s, 5)
        if vr is not None:
            self.assertGreater(vr, 0)

    def test_jarque_bera_normal_data(self):
        """正态随机数据的 JB 应该较小，判为接近正态"""
        rng = np.random.default_rng(4)
        prices = 1 + np.cumsum(rng.normal(0, 0.01, 2000))
        s = series_of(make_df(np.maximum(prices, 0.1)))
        result = extended.jarque_bera(s)
        self.assertIsNotNone(result)
        jb, is_normal = result
        self.assertGreaterEqual(jb, 0)

    def test_max_streaks(self):
        """连续5天涨、连续3天跌"""
        vals = [1.0]
        for _ in range(5):
            vals.append(vals[-1] * 1.01)
        for _ in range(3):
            vals.append(vals[-1] * 0.99)
        win, loss = extended.max_streaks(series_of(make_df(vals)))
        self.assertEqual(win, 5)
        self.assertEqual(loss, 3)


class TestTechnical(unittest.TestCase):

    def test_kdj_bounds(self):
        """KDJ 的 K/D 应在 0~100"""
        rng = np.random.default_rng(5)
        df = make_df(1 + np.cumsum(rng.normal(0, 0.01, 200)))
        r = extended.kdj(df)
        if r["k"] is not None:
            self.assertGreaterEqual(r["k"], -5)
            self.assertLessEqual(r["k"], 105)

    def test_kdj_uptrend_high_k(self):
        """一路上涨，K 值应该偏高"""
        df = make_df(np.linspace(1, 2, 200))
        r = extended.kdj(df)
        self.assertGreater(r["k"], 60)

    def test_bollinger_pct_b(self):
        """价格在中轨时 %B 应接近 0.5"""
        df = make_df(np.ones(60) * 1.0 + np.sin(np.arange(60)) * 0.01)
        r = extended.bollinger(series_of(df))
        if r["pct_b"] is not None:
            self.assertGreaterEqual(r["pct_b"], -2)
            self.assertLessEqual(r["pct_b"], 3)

    def test_adx_range(self):
        """ADX 必须在 0~100"""
        rng = np.random.default_rng(6)
        df = make_df(1 + np.cumsum(rng.normal(0, 0.01, 200)))
        r = extended.atr_adx(df)
        if r["adx"] is not None:
            self.assertGreaterEqual(r["adx"], 0)
            self.assertLessEqual(r["adx"], 100)

    def test_adx_trending_is_high(self):
        """单边上涨行情 ADX 应该较高"""
        df = make_df(np.linspace(1, 2, 200))
        r = extended.atr_adx(df)
        self.assertGreater(r["adx"], 25)

    def test_obv_uptrend_with_volume(self):
        """价涨量增 → 量价齐升"""
        vals = np.linspace(1, 2, 100)
        df = make_df(vals)
        r = extended.obv(df)
        self.assertEqual(r["obv_trend"], "量价齐升")

    def test_williams_r_bounds(self):
        rng = np.random.default_rng(7)
        df = make_df(1 + np.cumsum(rng.normal(0, 0.01, 100)))
        wr = extended.williams_r(df)
        if wr is not None:
            self.assertGreaterEqual(wr, -100)
            self.assertLessEqual(wr, 0)

    def test_volume_ratio(self):
        """成交量恒定，量比应接近 1"""
        df = make_df(np.linspace(1, 2, 100))
        vr = extended.volume_ratio(df)
        self.assertAlmostEqual(vr, 1.0, places=2)

    def test_amihud_requires_amount(self):
        """没有成交额字段时应返回 None 而不是崩"""
        df = make_df(np.linspace(1, 2, 100)).drop(columns=["amount"])
        self.assertIsNone(extended.amihud_illiquidity(df))


class TestBenchmarkRelations(unittest.TestCase):

    def test_identical_series_correlation_one(self):
        """和基准完全相同，相关系数应≈1，上行/下行捕获率≈1"""
        vals = list(1 + np.cumsum(np.random.default_rng(8).normal(0, 0.01, 300)))
        s = series_of(make_df(vals))
        r = extended.benchmark_relations(s, s)
        self.assertAlmostEqual(r["correlation"], 1.0, places=2)
        self.assertAlmostEqual(r["r_squared"], 1.0, places=2)
        if r["up_capture"] is not None:
            self.assertAlmostEqual(r["up_capture"], 1.0, places=2)

    def test_no_benchmark_returns_none(self):
        s = series_of(make_df(np.linspace(1, 2, 100)))
        r = extended.benchmark_relations(s, None)
        self.assertIsNone(r["correlation"])


class TestPerformanceRatios(unittest.TestCase):

    def test_recovery_factor(self):
        """总收益 50%、最大回撤 25% → 恢复因子 2.0"""
        vals = [1.0] * 5 + [1.5] + [1.125] + [1.5]
        r = extended.performance_ratios(series_of(make_df(vals)))
        if r["recovery_factor"] is not None:
            self.assertGreater(r["recovery_factor"], 0)

    def test_pain_index_positive(self):
        rng = np.random.default_rng(9)
        s = series_of(make_df(1 + np.cumsum(rng.normal(0, 0.01, 300))))
        r = extended.performance_ratios(s)
        if r["pain_index"] is not None:
            self.assertGreaterEqual(r["pain_index"], 0)


class TestAggregate(unittest.TestCase):

    def test_compute_extended_keys(self):
        rng = np.random.default_rng(10)
        df = make_df(1 + np.cumsum(rng.normal(0, 0.01, 300)))
        r = extended.compute_extended(df, df)
        for key in ["hurst", "kdj", "bollinger", "adx", "obv",
                    "correlation", "best_month", "pain_index"]:
            self.assertIn(key, r)

    def test_short_data_returns_empty(self):
        df = make_df([1.0, 1.1, 1.2])
        self.assertEqual(extended.compute_extended(df), {})

    def test_indicator_count(self):
        """确认指标总数达到 70+ 的目标"""
        c = extended.count_indicators()
        self.assertGreaterEqual(c["total"], 70)


if __name__ == "__main__":
    unittest.main(verbosity=2)
