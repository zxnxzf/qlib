"""
LiveExchange 提供实时报价定价，用于实盘份额换算。
继承 Exchange，仅覆写 get_deal_price，优先使用外部注入的 quotes_live。
"""

from typing import Dict, Any

import pandas as pd

from .exchange import Exchange


class LiveExchange(Exchange):
    """使用实时报价的 Exchange 派生类。"""

    def __init__(self, quotes_live: Dict[str, Dict[str, float]], **kwargs: Any):
        # 保存实时报价字典：{code: {last,bid1,ask1,high_limit,low_limit}}
        self.quotes_live = quotes_live or {}
        # 调用父类初始化，保持原有行为
        super().__init__(**kwargs)

    def _price_from_quotes(self, stock_id: str, is_buy: bool) -> float:
        # 读取单只标的的报价
        q = self.quotes_live.get(stock_id, {})
        last = q.get("last", float("nan"))
        bid1 = q.get("bid1", float("nan"))
        ask1 = q.get("ask1", float("nan"))
        hi = q.get("high_limit", float("nan"))
        lo = q.get("low_limit", float("nan"))
        # 买单优先吃 ask1，其次 last，并做涨停保护
        if is_buy:
            price = ask1 if pd.notna(ask1) and ask1 > 0 else last
            if pd.notna(hi) and hi > 0:
                price = min(price, hi) if pd.notna(price) and price > 0 else hi
        # 卖单优先出 bid1，其次 last，并做跌停保护
        else:
            price = bid1 if pd.notna(bid1) and bid1 > 0 else last
            if pd.notna(lo) and lo > 0:
                price = max(price, lo) if pd.notna(price) and price > 0 else lo
        # 返回可用价格，否则 NaN
        return float(price) if pd.notna(price) and price > 0 else float("nan")

    def get_deal_price(self, stock_id, start_time=None, end_time=None, direction=None, **kwargs):
        # 优先用实时报价定价
        from .decision import OrderDir  # 延迟导入避免循环

        is_buy = direction == OrderDir.BUY
        price = self._price_from_quotes(stock_id, is_buy=is_buy)
        # 如果有有效实时价，直接返回
        if pd.notna(price) and price > 0:
            return price
        # 否则回退父类逻辑（历史价）
        return super().get_deal_price(stock_id, start_time=start_time, end_time=end_time, direction=direction, **kwargs)
