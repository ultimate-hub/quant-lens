"""
指标计算模块的单元测试。

什么是单元测试？
  给每个函数喂"已知答案"的输入，检查输出对不对。
  比如：净值从 1.0 涨到 1.1，总收益必须是 10%。
  一旦以后改代码改坏了，测试会立刻报红，避免把错误带进产品。

运行方式（在 quant-platform 目录下）：
    python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from app.services import indicators


def make_nav(values, start="2024-01-01"):
    """辅助函数：用一串数字造出带日期索引的净值序列。"""
    idx = pd.bdate_range(start=start, periods=len(values))
    return pd.Series(values, index=idx, dtype="float64")


class TestBasicMetrics(unittest.TestCase):

    def test_total_return(self):
        """1.0 -> 1.1 应该正好是 10%"""
        nav = make_nav([1.0, 1.1])
        self.assertAlmostEqual(indicators.total_return(nav), 0.1, places=6)

    def test_total_return_loss(self):
        """1.0 -> 0.8 是 -20%"""
        nav = make_nav([1.0, 0.8])
        self.assertAlmostEqual(indicators.total_return(nav), -0.2, places=6)

    def test_max_drawdown_simple(self):
        """
        1.0 -> 1.2 -> 0.9
        最高点 1.2，最低点 0.9，回撤 = 0.9/1.2 - 1 = -25%
        """
        nav = make_nav([1.0, 1.2, 0.9])
        self.assertAlmostEqual(indicators.max_drawdown(nav), -0.25, places=6)

    def test_max_drawdown_monotonic_up(self):
        """一路上涨的净值，最大回撤应该是 0"""
        nav = make_nav([1.0, 1.1, 1.2, 1.3])
        self.assertAlmostEqual(indicators.max_drawdown(nav), 0.0, places=6)

    def test_daily_returns(self):
        """涨跌幅计算：(1.1/1.0-1)=10%, (1.21/1.1-1)=10%"""
        nav = make_nav([1.0, 1.1, 1.21])
        rets = indicators.daily_returns(nav)
        self.assertEqual(len(rets), 2)
        self.assertAlmostEqual(rets.iloc[0], 0.1, places=6)
        self.assertAlmostEqual(rets.iloc[1], 0.1, places=6)

    def test_win_rate(self):
        """3天里2天涨1天跌，胜率 2/3"""
        nav = make_nav([1.0, 1.1, 1.05, 1.15])
        self.assertAlmostEqual(indicators.win_rate(nav), 2 / 3, places=4)

    def test_annualized_return_one_year(self):
        """
        252 个交易日正好是"一年"。
        净值从 1.0 涨到 1.2，年化收益应该正好 20%。
        """
        values = list(np.linspace(1.0, 1.2, 253))
        nav = make_nav(values)
        self.assertAlmostEqual(indicators.annualized_return(nav), 0.2, places=2)

    def test_volatility_zero_for_flat(self):
        """净值完全不动 -> 波动率为 0（不会有除零错误）"""
        nav = make_nav([1.0] * 10)
        self.assertAlmostEqual(indicators.annualized_volatility(nav), 0.0, places=6)

    def test_sharpe_no_crash_when_zero_vol(self):
        """波动率为 0 时夏普应该返回 0 而不是报错或无穷大"""
        nav = make_nav([1.0] * 10)
        self.assertEqual(indicators.sharpe_ratio(nav), 0.0)


class TestComputeAll(unittest.TestCase):

    def test_compute_all_keys(self):
        """确保输出包含前端需要的所有字段"""
        nav = make_nav(list(np.linspace(1.0, 1.5, 100)))
        m = indicators.compute_all_metrics(nav)
        for key in ["annual_return", "max_drawdown", "sharpe", "sortino",
                    "calmar", "win_rate", "total_return", "days"]:
            self.assertIn(key, m)

    def test_compute_all_too_short(self):
        """数据不足 2 条应该抛异常，而不是返回垃圾结果"""
        nav = make_nav([1.0])
        with self.assertRaises(ValueError):
            indicators.compute_all_metrics(nav)


class TestScore(unittest.TestCase):

    def test_score_range(self):
        """评分必须在 0-100 之间"""
        nav = make_nav(list(np.linspace(1.0, 2.0, 500)))
        m = indicators.compute_all_metrics(nav)
        s = indicators.score_fund(m)
        self.assertGreaterEqual(s["total_score"], 0)
        self.assertLessEqual(s["total_score"], 100)
        self.assertIn(s["grade"], ["优秀", "良好", "一般", "较差"])

    def test_good_fund_beats_bad_fund(self):
        """稳健上涨的基金，评分必须高于暴涨暴跌的基金"""
        good = make_nav(list(np.linspace(1.0, 1.5, 500)))
        # 先暴涨后腰斩
        bad_values = list(np.linspace(1.0, 2.0, 250)) + list(np.linspace(2.0, 1.0, 250))
        bad = make_nav(bad_values)

        score_good = indicators.score_fund(indicators.compute_all_metrics(good))["total_score"]
        score_bad = indicators.score_fund(indicators.compute_all_metrics(bad))["total_score"]
        self.assertGreater(score_good, score_bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)
