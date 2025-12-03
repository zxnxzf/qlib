# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""
LiveTopkStrategy - Small capital optimization strategy with two-round budget allocation
"""

import copy
import numpy as np
import pandas as pd
from qlib.backtest.decision import Order, OrderDir, TradeDecisionWO
from qlib.backtest.position import Position
from .signal_strategy import TopkDropoutStrategy


class LiveTopkStrategy(TopkDropoutStrategy):
    """
    LiveTopkStrategy 是针对小资金账户优化的策略，通过两轮预算分配最大化买入股票数量和资金利用率。

    核心逻辑：
    - Round 1: 假设等分预算，筛选出能买≥min_affordable_shares股的股票
    - Round 2: 用总预算重新等分给筛选出的股票，提高单股预算和资金使用率

    示例：
        总资金: 10万, topk=10, risk_degree=0.95
        Round 1: (10万*0.95)/10 = 0.95万/股 → 过滤3只贵股（<100股），保留7只
        Round 2: (10万*0.95)/7 = 1.36万/股 → 买入这7只，资金使用率95%

    参数:
    -------
    min_affordable_shares : int, optional (default=100)
        最小可负担股数（中国A股为100股即1手）
        只有在Round 1中能买到至少这么多股的股票才会被保留

    enable_affordability_filter : bool, optional (default=True)
        是否启用两轮分配逻辑
        设置为False时行为与父类TopkDropoutStrategy完全一致

    **kwargs : dict
        父类TopkDropoutStrategy的所有参数:
        - topk: 目标持仓数量
        - n_drop: 每期替换股票数
        - method_sell: 卖出方法 (bottom/random)
        - method_buy: 买入方法 (top/random)
        - hold_thresh: 最短持有天数（T+1限制）
        - only_tradable: 仅考虑可交易标的
        - forbid_all_trade_at_limit: 涨跌停时禁止交易
    """

    def __init__(
        self,
        *,
        min_affordable_shares: int = 100,
        enable_affordability_filter: bool = True,
        **kwargs,
    ):
        """
        初始化 LiveTopkStrategy

        Parameters
        ----------
        min_affordable_shares : int, default=100
            最小可负担股数阈值（1手=100股）
        enable_affordability_filter : bool, default=True
            启用两轮预算分配逻辑
        **kwargs : dict
            传递给父类TopkDropoutStrategy的参数
        """
        super().__init__(**kwargs)
        self.min_affordable_shares = min_affordable_shares
        self.enable_affordability_filter = enable_affordability_filter

    def generate_trade_decision(self, execute_result=None):
        """
        生成交易决策（覆盖父类方法，实现两轮预算分配）

        如果 enable_affordability_filter=False，则直接调用父类实现。
        否则，使用两轮分配逻辑：
        1. Round 1: 假设等分预算，筛选可负担股票
        2. Round 2: 重新等分预算给筛选出的股票

        Returns
        -------
        TradeDecisionWO
            包含卖单和买单的交易决策
        """
        # 功能开关：禁用时直接使用父类逻辑
        if not self.enable_affordability_filter:
            return super().generate_trade_decision(execute_result)

        # ========== 以下是完整复制父类逻辑，仅修改买入部分（273-301行）==========

        # 当前交易步及对应的交易/预测窗口
        trade_step = self.trade_calendar.get_trade_step()
        trade_start_time, trade_end_time = self.trade_calendar.get_step_time(trade_step)
        pred_start_time, pred_end_time = self.trade_calendar.get_step_time(trade_step, shift=1)

        if trade_step == 0:
            print(
                f"[LiveTopkStrategy] step={trade_step} "
                f"trade_window=[{trade_start_time}, {trade_end_time}] "
                f"pred_window=[{pred_start_time}, {pred_end_time}]"
            )

        pred_score = self.signal.get_signal(start_time=pred_start_time, end_time=pred_end_time)

        # NOTE: current version can't handle multi-signal DataFrame
        if isinstance(pred_score, pd.DataFrame):
            pred_score = pred_score.iloc[:, 0]

        if pred_score is None:
            return TradeDecisionWO([], self)

        # 定义辅助函数：根据only_tradable筛选
        if self.only_tradable:

            def get_first_n(li, n, reverse=False):
                cur_n = 0
                res = []
                for si in reversed(li) if reverse else li:
                    if self.trade_exchange.is_stock_tradable(
                        stock_id=si, start_time=trade_start_time, end_time=trade_end_time
                    ):
                        res.append(si)
                        cur_n += 1
                        if cur_n >= n:
                            break
                return res[::-1] if reverse else res

            def get_last_n(li, n):
                return get_first_n(li, n, reverse=True)

            def filter_stock(li):
                return [
                    si
                    for si in li
                    if self.trade_exchange.is_stock_tradable(
                        stock_id=si, start_time=trade_start_time, end_time=trade_end_time
                    )
                ]

        else:

            def get_first_n(li, n):
                return list(li)[:n]

            def get_last_n(li, n):
                return list(li)[-n:]

            def filter_stock(li):
                return li

        # 拷贝当前仓位状态
        current_temp: Position = copy.deepcopy(self.trade_position)
        sell_order_list = []
        buy_order_list = []

        cash = current_temp.get_cash()
        current_stock_list = current_temp.get_stock_list()

        # 现持仓按预测得分排序
        last = pred_score.reindex(current_stock_list).sort_values(ascending=False).index

        # 生成今日买入候选
        if self.method_buy == "top":
            today = get_first_n(
                pred_score[~pred_score.index.isin(last)].sort_values(ascending=False).index,
                self.n_drop + self.topk - len(last),
            )
        elif self.method_buy == "random":
            topk_candi = get_first_n(pred_score.sort_values(ascending=False).index, self.topk)
            candi = list(filter(lambda x: x not in last, topk_candi))
            n = self.n_drop + self.topk - len(last)
            try:
                today = np.random.choice(candi, n, replace=False)
            except ValueError:
                today = candi
        else:
            raise NotImplementedError(f"This type of input is not supported")

        # 合并当前+候选，按得分排序
        comb = pred_score.reindex(last.union(pd.Index(today))).sort_values(ascending=False).index

        # 依策略得到卖出列表
        if self.method_sell == "bottom":
            sell = last[last.isin(get_last_n(comb, self.n_drop))]
        elif self.method_sell == "random":
            candi = filter_stock(last)
            try:
                sell = pd.Index(np.random.choice(candi, self.n_drop, replace=False) if len(last) else [])
            except ValueError:
                sell = candi
        else:
            raise NotImplementedError(f"This type of input is not supported")

        # 最终买入候选列表
        buy = today[: len(sell) + self.topk - len(last)]

        # ========== 卖出逻辑（保持与父类一致）==========
        for code in current_stock_list:
            if not self.trade_exchange.is_stock_tradable(
                stock_id=code,
                start_time=trade_start_time,
                end_time=trade_end_time,
                direction=None if self.forbid_all_trade_at_limit else OrderDir.SELL,
            ):
                continue
            if code in sell:
                time_per_step = self.trade_calendar.get_freq()
                if current_temp.get_stock_count(code, bar=time_per_step) < self.hold_thresh:
                    continue
                sell_amount = current_temp.get_stock_amount(code=code)
                sell_order = Order(
                    stock_id=code,
                    amount=sell_amount,
                    start_time=trade_start_time,
                    end_time=trade_end_time,
                    direction=Order.SELL,
                )
                if self.trade_exchange.check_order(sell_order):
                    sell_order_list.append(sell_order)
                    trade_val, trade_cost, trade_price = self.trade_exchange.deal_order(
                        sell_order, position=current_temp
                    )
                    cash += trade_val - trade_cost

        # ========== 买入逻辑（两轮分配 - 核心修改部分）==========

        # Round 1: 可负担性筛选
        if len(buy) == 0:
            return TradeDecisionWO(sell_order_list, self)

        # 计算初始预算（假设等分）
        initial_budget_per_stock = cash * self.risk_degree / len(buy)

        if trade_step == 0:
            print(
                f"[LiveTopk] Round 1: 可负担性筛选"
                f"\n   现金: {cash:.2f}, 风险度: {self.risk_degree}, 候选数: {len(buy)}"
                f"\n   初始单股预算: {initial_budget_per_stock:.2f} 元"
            )

        # 筛选循环：保留能买到至少min_affordable_shares股的股票
        affordable_stocks = []
        for code in buy:
            # 检查可交易性
            if not self.trade_exchange.is_stock_tradable(
                stock_id=code,
                start_time=trade_start_time,
                end_time=trade_end_time,
                direction=None if self.forbid_all_trade_at_limit else OrderDir.BUY,
            ):
                continue

            # 获取成交价
            try:
                buy_price = self.trade_exchange.get_deal_price(
                    stock_id=code,
                    start_time=trade_start_time,
                    end_time=trade_end_time,
                    direction=OrderDir.BUY,
                )
            except Exception as e:
                print(f"[LiveTopk][跳过] {code}: 价格查询失败 ({e})")
                continue

            # 验证价格
            if pd.isna(buy_price) or buy_price <= 0:
                if trade_step == 0:
                    print(f"[LiveTopk][跳过] {code}: 无有效价格")
                continue

            # 计算可买股数
            potential_shares = initial_budget_per_stock / buy_price
            factor = self.trade_exchange.get_factor(
                stock_id=code, start_time=trade_start_time, end_time=trade_end_time
            )
            rounded_shares = self.trade_exchange.round_amount_by_trade_unit(potential_shares, factor)

            # 在回测中，rounded_shares 是“复权股数”，乘以 factor 后近似为真实股数
            raw_shares = rounded_shares
            if factor is not None and not pd.isna(factor):
                raw_shares = rounded_shares * factor

            # 筛选：保留能买到至少 min_affordable_shares 股（真实股数）的股票
            if raw_shares >= self.min_affordable_shares:
                affordable_stocks.append(code)
            else:
                if trade_step == 0:
                    print(
                        f"[LiveTopk][过滤] {code}: 价格={buy_price:.2f}, "
                        f"可买≈{raw_shares:.0f}股 < {self.min_affordable_shares}"
                    )

        # Round 2: 预算重新分配
        if len(affordable_stocks) == 0:
            print("[LiveTopk][警告] 所有股票都买不起，仅返回卖单")
            return TradeDecisionWO(sell_order_list, self)

        # 重新计算预算（用总预算除以可负担股票数）
        final_budget_per_stock = cash * self.risk_degree / len(affordable_stocks)

        if trade_step == 0:
            print(
                f"[LiveTopk] Round 2: 预算重新分配"
                f"\n   {len(buy)} 候选 → {len(affordable_stocks)} 可买入"
                f"\n   预算调整: {initial_budget_per_stock:.0f} → {final_budget_per_stock:.0f} 元/股"
            )

        # 生成买入订单
        for code in affordable_stocks:
            # 再次检查可交易性（保险起见）
            if not self.trade_exchange.is_stock_tradable(
                stock_id=code,
                start_time=trade_start_time,
                end_time=trade_end_time,
                direction=None if self.forbid_all_trade_at_limit else OrderDir.BUY,
            ):
                continue

            # 获取价格（应该成功，Round1已验证）
            buy_price = self.trade_exchange.get_deal_price(
                stock_id=code, start_time=trade_start_time, end_time=trade_end_time, direction=OrderDir.BUY
            )

            # 用新预算计算股数
            buy_amount = final_budget_per_stock / buy_price
            factor = self.trade_exchange.get_factor(stock_id=code, start_time=trade_start_time, end_time=trade_end_time)
            buy_amount = self.trade_exchange.round_amount_by_trade_unit(buy_amount, factor)

            # 创建订单
            buy_order = Order(
                stock_id=code,
                amount=buy_amount,
                start_time=trade_start_time,
                end_time=trade_end_time,
                direction=Order.BUY,
            )
            buy_order_list.append(buy_order)

        return TradeDecisionWO(sell_order_list + buy_order_list, self)
