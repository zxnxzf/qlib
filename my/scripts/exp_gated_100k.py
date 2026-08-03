#!/usr/bin/env python3
"""实验：10万×门控 引擎级回测（gated_100k）——"上钱前最终画像"

门控规则（沙盘定版）：中证全指收盘连续3天低于其MA20 → 次日清仓持币；
否则正常运行。清仓/重建由策略在引擎内真实下单（含全部成本与可成交约束）。
对照：capital_100k 裸奔版（净+10.52%，MDD约-35%）。
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/Users/bytedance/code/qlib")
sys.path.insert(0, "/Users/bytedance/code/qlib/my/scripts")

import pandas as pd
import qlib
from qlib.data import D
from qlib.contrib.evaluate import backtest_daily
from qlib.contrib.strategy import TopkDropoutStrategy
from qlib.backtest.decision import Order, OrderDir, TradeDecisionWO

ART = Path("/Users/bytedance/code/qlib/my/artifacts")
NAME = "gated_100k"
ANN = 238


class GatedTopkDropout(TopkDropoutStrategy):
    """门控包装：gate=False 的交易日清仓持币，gate=True 正常 TopkDropout。"""

    def __init__(self, gate: pd.Series, **kwargs):
        super().__init__(**kwargs)
        self._gate = gate

    def generate_trade_decision(self, execute_result=None):
        trade_step = self.trade_calendar.get_trade_step()
        trade_start, trade_end = self.trade_calendar.get_step_time(trade_step)
        d = pd.Timestamp(trade_start).normalize()
        g = bool(self._gate.get(d, True))
        if g:
            return super().generate_trade_decision(execute_result)
        orders = []
        for code in self.trade_position.get_stock_list():
            if self.trade_exchange.is_stock_tradable(
                stock_id=code, start_time=trade_start, end_time=trade_end, direction=OrderDir.SELL
            ):
                amount = self.trade_position.get_stock_amount(code)
                order = Order(stock_id=code, amount=amount, start_time=trade_start,
                              end_time=trade_end, direction=OrderDir.SELL)
                if self.trade_exchange.check_order(order):
                    orders.append(order)
        return TradeDecisionWO(orders, self)


def main():
    qlib.init(provider_uri="/Users/bytedance/code/qlib/my/data/cn_data", region="cn", kernels=4)
    pred = pd.read_pickle(ART / "candidate1_pred.pkl")

    idx = D.features(["SH000985"], ["$close"], start_time="2022-10-01", end_time="2026-07-28").droplevel(0)["$close"]
    above = idx > idx.rolling(20).mean()
    off = (~above).rolling(3).sum() >= 3          # 连续3天线下 → 关
    gate = (~off).shift(1)                        # 昨日状态定今日，防前视
    gate.index = pd.to_datetime(gate.index)
    gate = gate.fillna(True)

    strategy = GatedTopkDropout(
        gate=gate, signal=pred, topk=50, n_drop=2, hold_thresh=1, only_tradable=True
    )
    report, _ = backtest_daily(
        start_time="2023-01-01", end_time="2026-07-28", strategy=strategy,
        account=100_000, benchmark="SH000300",
        exchange_kwargs={
            "deal_price": "open",
            "limit_threshold": ("$open/Ref($close,1)-1 > 0.095", "$open/Ref($close,1)-1 < -0.095"),
            "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5, "impact_cost": 0.001,
        },
    )
    report.to_pickle(ART / f"exp_{NAME}_report.pkl")
    net = report["return"] - report["cost"]
    cum = (1 + net).cumprod()
    mdd = (cum / cum.cummax() - 1).min()
    ex = net - report["bench"]
    print(
        f"门控引擎版(10万): 绝对年化={cum.iloc[-1]**(ANN/len(net))-1:+.1%} 最大回撤={mdd:.1%} "
        f"净超额={ex.mean()*ANN:+.2%} IR={ex.mean()/ex.std()*ANN**0.5:+.2f} 终值={report['account'].iloc[-1]:,.0f}",
        flush=True,
    )
    for y, g in net.groupby(net.index.year):
        c = (1 + g).prod() - 1
        print(f"  {y} 绝对: {c:+.1%}", flush=True)
    for k in (1.0, 0.8, 0.7):
        print(f"  仓位系数{k:.0%}: 预期回撤≈{mdd*k:.1%} 绝对年化≈{(cum.iloc[-1]**(ANN/len(net))-1)*k:+.1%}", flush=True)

    subprocess.run(
        f"/Users/bytedance/code/qlib/.venv/bin/python /Users/bytedance/code/qlib/my/scripts/package_dashboard.py "
        f"{NAME} {ART / 'candidate1_pred.pkl'} {ART / f'exp_{NAME}_report.pkl'}",
        shell=True, check=False,
    )

    from exp_mlflow_log import log_experiment

    rid = log_experiment(
        NAME,
        params={"account": 100000, "gate": "SH000985 MA20 3day-confirm", "base": "candidate ndrop2 open-exec", "min_cost": 5},
        metrics={"net_excess_ann": ex.mean() * ANN, "abs_mdd": float(mdd),
                 "abs_ann": float(cum.iloc[-1] ** (ANN / len(net)) - 1)},
        dashboard=str(ART / "faux_recorders" / NAME / "recorder_dashboard.html"),
    )
    print(f"mlflow run: {rid}", flush=True)


if __name__ == "__main__":
    main()
