import unittest

from strategies.fixed_ratio_rebalancer import PricePoint, backtest_rebalancing


def series(prices):
    return [PricePoint(f"2024-01-{i:02d}", float(p)) for i, p in enumerate(prices, 1)]


class FixedRatioRebalancerTests(unittest.TestCase):
    def test_no_lookahead_uses_prior_weights_for_daily_return(self):
        spy = series([100, 110, 110])
        gld = series([100, 100, 110])
        result = backtest_rebalancing(spy, gld, initial_capital=100.0, cost_bps=0.0)

        # Day 2 return is earned on the initial 50/50 weights: +5%.
        self.assertAlmostEqual(result["daily"][1]["equity"], 105.0, places=8)
        # Day 3 return is also earned on 50/50 weights after the day-2 rebalance: +5%.
        self.assertAlmostEqual(result["daily"][2]["equity"], 110.25, places=8)
        self.assertAlmostEqual(result["daily"][1]["spy_weight_after_close"], 0.5)
        self.assertAlmostEqual(result["daily"][1]["gld_weight_after_close"], 0.5)

    def test_costs_reduce_equity_and_are_logged(self):
        spy = series([100, 120, 120])
        gld = series([100, 100, 100])
        free = backtest_rebalancing(spy, gld, initial_capital=1000, cost_bps=0)
        charged = backtest_rebalancing(spy, gld, initial_capital=1000, cost_bps=10)

        self.assertLess(charged["metrics"]["final_equity"], free["metrics"]["final_equity"])
        self.assertGreater(charged["total_costs_dollars"], 0)
        self.assertTrue(any(row["action"] == "REBALANCE" for row in charged["trades"]))

    def test_buy_and_hold_benchmarks_are_returned(self):
        result = backtest_rebalancing(series([100, 110, 121]),
                                      series([100, 90, 99]),
                                      initial_capital=1000, cost_bps=0)
        self.assertIn("SPY_buy_and_hold", result["benchmarks"])
        self.assertIn("GLD_buy_and_hold", result["benchmarks"])
        self.assertIn("static_50_50_buy_and_hold", result["benchmarks"])
        self.assertAlmostEqual(result["benchmarks"]["SPY_buy_and_hold"]["final_equity"], 1210.0, places=5)
        self.assertAlmostEqual(result["benchmarks"]["GLD_buy_and_hold"]["final_equity"], 990.0, places=5)

    def test_requires_matching_dates(self):
        with self.assertRaises(ValueError):
            backtest_rebalancing(
                [PricePoint("2024-01-01", 100), PricePoint("2024-01-02", 101)],
                [PricePoint("2024-02-01", 100), PricePoint("2024-02-02", 101)],
            )

    def test_rejects_invalid_costs_and_weights(self):
        spy, gld = series([100, 101]), series([100, 99])
        with self.assertRaises(ValueError):
            backtest_rebalancing(spy, gld, cost_bps=-1)
        with self.assertRaises(ValueError):
            backtest_rebalancing(spy, gld, target_spy=0.7, target_gld=0.5)

    def test_duplicate_dates_are_rejected(self):
        dup = [PricePoint("2024-01-01", 100), PricePoint("2024-01-01", 101)]
        gld = series([100, 101])
        with self.assertRaises(ValueError):
            backtest_rebalancing(dup, gld)


if __name__ == "__main__":
    unittest.main()
