"""
时序模型测试。

为什么这些测试重要：
    模型算错了不会抛异常，只会给出一条看起来正常的预测线。
    没有测试兜底，这种错误永远发现不了。
"""

import unittest

import numpy as np

from app.analytics import ts_models


def make_series(n=300, seed=7, drift=0.0003, vol=0.02, start=100.0):
    """造一条带漂移的随机价格序列（固定种子，结果可复现）。"""
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    return start * np.exp(np.cumsum(rets))


class TestForecastAll(unittest.TestCase):
    def setUp(self):
        self.y = make_series()

    def test_returns_every_model(self):
        out = ts_models.forecast_all(self.y, days=20)
        self.assertEqual(set(out), set(ts_models.MODELS))

    def test_path_length_matches_days(self):
        for days in (5, 20, 40):
            out = ts_models.forecast_all(self.y, days=days)
            for key, path in out.items():
                self.assertEqual(len(path), days, f"{key} 长度不对")

    def test_all_positive(self):
        # 价格不能是 0 或负数 —— 对数模型外推时最容易出这种事
        out = ts_models.forecast_all(self.y, days=20)
        for key, path in out.items():
            self.assertTrue(all(v > 0 for v in path), f"{key} 出现非正价格")

    def test_clamped_to_40_percent(self):
        # 极端趋势样本会让直线外推跑飞，这里验证保险是否生效
        # 注意容差用 1e-3：forecast_all 会把结果四舍五入到 4 位小数，
        # 边界值会因此偏移一点点，按 1e-6 比会误报。
        spike = make_series(n=200) * 1.0
        spike[:150] *= 4.0        # 人为制造一段暴跌，逼模型外推
        out = ts_models.forecast_all(spike, days=30)
        last = float(spike[-1])
        for key, path in out.items():
            lo = min(path)
            hi = max(path)
            self.assertGreaterEqual(lo, last * 0.6 - 1e-3, f"{key} 下界没夹住")
            self.assertLessEqual(hi, last * 1.4 + 1e-3, f"{key} 上界没夹住")

    def test_naive_is_flat(self):
        out = ts_models.forecast_all(self.y, days=10)
        last = self.y[-1]
        # 同样用 1e-3 容差（4 位小数取整）
        self.assertTrue(all(abs(v - last) < 1e-3 for v in out["naive"]))

    def test_too_short_raises(self):
        with self.assertRaises(ValueError):
            ts_models.forecast_all([100.0] * 10, days=20)

    def test_handles_nan_and_zeros(self):
        dirty = np.array([100.0, np.nan, 102.0, 0.0, 101.0] + list(make_series(60)))
        out = ts_models.forecast_all(dirty, days=5)
        for key, path in out.items():
            self.assertTrue(all(np.isfinite(v) and v > 0 for v in path), key)


class TestBacktest(unittest.TestCase):
    def test_ok_with_enough_data(self):
        y = make_series(n=400)
        bt = ts_models.backtest(y, days=20, holdout=20)
        self.assertTrue(bt["ok"])
        self.assertGreaterEqual(bt["windows"], 3)
        self.assertEqual(len(bt["models"]), len(ts_models.MODELS))

    def test_naive_always_present_as_baseline(self):
        bt = ts_models.backtest(make_series(400), days=20)
        names = [m["key"] for m in bt["models"]]
        self.assertIn("naive", names)
        base = next(m for m in bt["models"] if m["key"] == "naive")
        self.assertAlmostEqual(base["vs_naive"], 0.0)

    def test_naive_direction_is_none(self):
        # 随机游走永远预测持平，方向命中率没有定义，必须是 None 而不是 0
        bt = ts_models.backtest(make_series(400), days=20)
        base = next(m for m in bt["models"] if m["key"] == "naive")
        self.assertIsNone(base["dir_hit"])

    def test_models_sorted_by_mae(self):
        bt = ts_models.backtest(make_series(400), days=20)
        maes = [m["mae"] for m in bt["models"]]
        self.assertEqual(maes, sorted(maes))

    def test_too_short_reports_not_ok(self):
        bt = ts_models.backtest(make_series(60), days=20)
        self.assertFalse(bt["ok"])
        self.assertIn("reason", bt)

    def test_conclusion_mentions_baseline_when_losing(self):
        # 纯噪声序列上所有模型都赢不了随机游走，结论必须如实说出来
        rng = np.random.default_rng(11)
        pure_noise = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 500)))
        bt = ts_models.backtest(pure_noise, days=20)
        if bt["ok"] and not bt["best_usable"]:
            self.assertIn("随机游走", bt["conclusion"])


class TestClamp(unittest.TestCase):
    def test_clamp_bounds(self):
        path = np.array([50.0, 100.0, 200.0])
        out = ts_models._clamp_path(path, 100.0, max_move=0.4)
        self.assertAlmostEqual(float(out[0]), 60.0)
        self.assertAlmostEqual(float(out[1]), 100.0)
        self.assertAlmostEqual(float(out[2]), 140.0)


if __name__ == "__main__":
    unittest.main()