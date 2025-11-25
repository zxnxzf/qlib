# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
This order generator is for strategies based on WeightStrategyBase
"""
from ...backtest.position import Position
from ...backtest.exchange import Exchange

import pandas as pd
import copy


class OrderGenerator:
    def generate_order_list_from_target_weight_position(
        self,
        current: Position,
        trade_exchange: Exchange,
        target_weight_position: dict,
        risk_degree: float,
        pred_start_time: pd.Timestamp,
        pred_end_time: pd.Timestamp,
        trade_start_time: pd.Timestamp,
        trade_end_time: pd.Timestamp,
    ) -> list:
        """generate_order_list_from_target_weight_position

        :param current: The current position
        :type current: Position
        :param trade_exchange:
        :type trade_exchange: Exchange
        :param target_weight_position: {stock_id : weight}
        :type target_weight_position: dict
        :param risk_degree:
        :type risk_degree: float
        :param pred_start_time:
        :type pred_start_time: pd.Timestamp
        :param pred_end_time:
        :type pred_end_time: pd.Timestamp
        :param trade_start_time:
        :type trade_start_time: pd.Timestamp
        :param trade_end_time:
        :type trade_end_time: pd.Timestamp

        :rtype: list
        """
        raise NotImplementedError()


class OrderGenWInteract(OrderGenerator):
    """Order Generator With Interact"""

    def generate_order_list_from_target_weight_position(
        self,
        current: Position,
        trade_exchange: Exchange,
        target_weight_position: dict,
        risk_degree: float,
        pred_start_time: pd.Timestamp,
        pred_end_time: pd.Timestamp,
        trade_start_time: pd.Timestamp,
        trade_end_time: pd.Timestamp,
    ) -> list:
        """generate_order_list_from_target_weight_position

        No adjustment for for the nontradable share.
        All the tadable value is assigned to the tadable stock according to the weight.
        if interact == True, will use the price at trade date to generate order list
        else, will only use the price before the trade date to generate order list

        :param current:
        :type current: Position
        :param trade_exchange:
        :type trade_exchange: Exchange
        :param target_weight_position:
        :type target_weight_position: dict
        :param risk_degree:
        :type risk_degree: float
        :param pred_start_time:
        :type pred_start_time: pd.Timestamp
        :param pred_end_time:
        :type pred_end_time: pd.Timestamp
        :param trade_start_time:
        :type trade_start_time: pd.Timestamp
        :param trade_end_time:
        :type trade_end_time: pd.Timestamp

        :rtype: list
        """
        if target_weight_position is None:
            return []

        # calculate current_tradable_value
        current_amount_dict = current.get_stock_amount_dict()

        current_total_value = trade_exchange.calculate_amount_position_value(
            amount_dict=current_amount_dict,
            start_time=trade_start_time,
            end_time=trade_end_time,
            only_tradable=False,
        )
        current_tradable_value = trade_exchange.calculate_amount_position_value(
            amount_dict=current_amount_dict,
            start_time=trade_start_time,
            end_time=trade_end_time,
            only_tradable=True,
        )
        # add cash
        current_tradable_value += current.get_cash()

        reserved_cash = (1.0 - risk_degree) * (current_total_value + current.get_cash())
        current_tradable_value -= reserved_cash

        if current_tradable_value < 0:
            # if you sell all the tradable stock can not meet the reserved
            # value. Then just sell all the stocks
            target_amount_dict = copy.deepcopy(current_amount_dict.copy())
            for stock_id in list(target_amount_dict.keys()):
                if trade_exchange.is_stock_tradable(stock_id, start_time=trade_start_time, end_time=trade_end_time):
                    del target_amount_dict[stock_id]
        else:
            # consider cost rate
            current_tradable_value /= 1 + max(trade_exchange.close_cost, trade_exchange.open_cost)

            # strategy 1 : generate amount_position by weight_position
            # Use API in Exchange()
            target_amount_dict = trade_exchange.generate_amount_position_from_weight_position(
                weight_position=target_weight_position,
                cash=current_tradable_value,
                start_time=trade_start_time,
                end_time=trade_end_time,
            )
        order_list = trade_exchange.generate_order_for_target_amount_position(
            target_position=target_amount_dict,
            current_position=current_amount_dict,
            start_time=trade_start_time,
            end_time=trade_end_time,
        )
        return order_list


class OrderGenWOInteract(OrderGenerator):
    """
    订单生成器 - 无交互版本（Without Interact）

    特点：
    - 不使用交易日当天的实时信息（价格、可交易性等）
    - 使用预测日（T-1）的收盘价或持仓中记录的价格来估算目标份额
    - 适用于回测场景，避免使用未来信息（look-ahead bias）
    """

    def generate_order_list_from_target_weight_position(
        self,
        current: Position,
        trade_exchange: Exchange,
        target_weight_position: dict,
        risk_degree: float,
        pred_start_time: pd.Timestamp,
        pred_end_time: pd.Timestamp,
        trade_start_time: pd.Timestamp,
        trade_end_time: pd.Timestamp,
    ) -> list:
        """
        根据目标权重生成订单列表（无交互版本）

        核心逻辑：
        1. 计算可用于交易的总价值 = 当前总资产 × 风险度
        2. 对每只目标股票：
           - 优先使用预测日的收盘价估算目标份额
           - 如果是持仓股票且预测日无价格，则使用持仓记录的价格
        3. 生成从当前持仓到目标持仓的买卖订单

        与 WInteract 版本的区别：
        - WOInteract: 使用预测日（T-1）的价格，不使用交易日（T）的信息
        - WInteract: 使用交易日（T）的价格和可交易性，信息更准确但存在前瞻偏差

        参数说明：
        :param current: 当前持仓状态（包含现金和持仓股票）
        :param trade_exchange: 交易所对象（提供价格查询、可交易性检查等）
        :param target_weight_position: 目标权重字典 {股票代码: 权重}，例如 {"SH600000": 0.05, "SZ000001": 0.03}
        :param risk_degree: 风险度（资金使用比例，0-1），例如 0.95 表示使用 95% 的资金
        :param pred_start_time: 预测开始时间（通常是 T-1 日）
        :param pred_end_time: 预测结束时间（通常是 T-1 日）
        :param trade_start_time: 交易开始时间（通常是 T 日）
        :param trade_end_time: 交易结束时间（通常是 T 日）

        返回：
        :return: Order 对象列表，每个 Order 包含股票代码、买卖方向、数量等信息
        """
        # ========== 第一步：边界检查 ==========

        # 如果目标权重为空，直接返回空订单列表
        if target_weight_position is None:
            return []

        # ========== 第二步：计算可用于交易的总价值 ==========

        # 计算风险总价值 = 当前总资产（持仓市值 + 现金）× 风险度
        # 例如：总资产 100000 元，风险度 0.95，则可用于交易的价值为 95000 元
        risk_total_value = risk_degree * current.calculate_value()

        # ========== 第三步：获取当前持仓股票列表 ==========

        # 获取当前持仓的所有股票代码（用于判断某只股票是否已持有）
        current_stock = current.get_stock_list()

        # ========== 第四步：计算目标持仓份额（amount_dict） ==========

        # 初始化目标份额字典 {股票代码: 目标份额}
        amount_dict = {}

        # 遍历每只目标股票，计算目标持仓份额
        for stock_id in target_weight_position:
            # ===== 情况 1：股票在预测日和交易日都可交易 =====
            # 这是最常见的情况：股票正常交易，没有涨跌停或停牌

            # 检查股票在交易日是否可交易（不涨跌停、不停牌）
            tradable_at_trade_date = trade_exchange.is_stock_tradable(
                stock_id=stock_id, start_time=trade_start_time, end_time=trade_end_time
            )
            # 检查股票在预测日是否可交易（用于获取收盘价）
            tradable_at_pred_date = trade_exchange.is_stock_tradable(
                stock_id=stock_id, start_time=pred_start_time, end_time=pred_end_time
            )

            if tradable_at_trade_date and tradable_at_pred_date:
                # 使用预测日的收盘价来估算目标份额
                # 公式：目标份额 = (总价值 × 目标权重) / 预测日收盘价
                # 例如：总价值 95000 元，目标权重 5%，预测日收盘价 10 元
                #      目标份额 = 95000 × 0.05 / 10 = 475 股
                pred_close_price = trade_exchange.get_close(
                    stock_id, start_time=pred_start_time, end_time=pred_end_time
                )
                amount_dict[stock_id] = (
                    risk_total_value * target_weight_position[stock_id] / pred_close_price
                )
                # TODO: Qlib 使用 None 表示停牌
                # 因此最后的收盘价可能无法作为准确的交易价格估算
                # 使用前向填充的收盘价可能是更好的解决方案

            # ===== 情况 2：股票已持有，但预测日无价格（可能停牌或刚上市） =====
            # 使用持仓中记录的价格来估算目标份额
            elif stock_id in current_stock:
                # 使用持仓中记录的价格（通常是最后一次交易的价格）
                # 公式：目标份额 = (总价值 × 目标权重) / 持仓记录价格
                current_price = current.get_stock_price(stock_id)
                amount_dict[stock_id] = (
                    risk_total_value * target_weight_position[stock_id] / current_price
                )

            # ===== 情况 3：股票不可交易且未持有 =====
            # 跳过此股票（不生成订单）
            else:
                continue

        # ========== 第五步：生成订单列表 ==========

        # 调用 Exchange 的订单生成方法，根据目标份额和当前份额生成买卖订单
        # 该方法会自动计算：
        # - 需要买入的股票和数量（目标份额 > 当前份额）
        # - 需要卖出的股票和数量（目标份额 < 当前份额）
        # - 需要平仓的股票（目标份额为 0，但当前有持仓）
        order_list = trade_exchange.generate_order_for_target_amount_position(
            target_position=amount_dict,           # 目标持仓份额字典
            current_position=current.get_stock_amount_dict(),  # 当前持仓份额字典
            start_time=trade_start_time,           # 交易开始时间
            end_time=trade_end_time,               # 交易结束时间
        )

        # ========== 第六步：返回订单列表 ==========

        # 返回生成的订单列表（Order 对象列表）
        # 每个 Order 包含：股票代码、方向（BUY/SELL）、数量、时间等信息
        return order_list
