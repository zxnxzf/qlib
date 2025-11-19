# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Set, Tuple, TYPE_CHECKING, Union

import numpy as np

from qlib.utils.time import epsilon_change

if TYPE_CHECKING:
    from qlib.backtest.decision import BaseTradeDecision

import warnings

import pandas as pd

from ..data.data import Cal


class TradeCalendarManager:
    """
    交易日历管理器，供策略（BaseStrategy）与执行器（BaseExecutor）共享。
    """

    def __init__(
        self,
        freq: str,
        start_time: Union[str, pd.Timestamp] = None,
        end_time: Union[str, pd.Timestamp] = None,
        level_infra: LevelInfrastructure | None = None,
    ) -> None:
        """
        参数
        ----------
        freq : str
            交易日历频率，同时决定每个 trade_step 的步长（如 "day"、"1min"）。
        start_time : Union[str, pd.Timestamp], optional
            交易日历的闭区间起始时间，默认为 None（需在交易前 reset）。
        end_time : Union[str, pd.Timestamp], optional
            交易日历的闭区间结束时间，默认为 None（需在交易前 reset）。
        """
        self.level_infra = level_infra
        self.reset(freq=freq, start_time=start_time, end_time=end_time)

    def reset(
        self,
        freq: str,
        start_time: Union[str, pd.Timestamp] = None,
        end_time: Union[str, pd.Timestamp] = None,
    ) -> None:
        """
        重置交易日历（参数同 __init__）

        - self.trade_len : 该日历内总共的交易步数
        - self.trade_step : 已完成的交易步数，从 0 开始，取值范围 [0, 1, ..., self.trade_len - 1]
        """
        self.freq = freq
        self.start_time = pd.Timestamp(start_time) if start_time else None
        self.end_time = pd.Timestamp(end_time) if end_time else None

        _calendar = Cal.calendar(freq=freq, future=True)
        assert isinstance(_calendar, np.ndarray)
        self._calendar = _calendar
        _, _, _start_index, _end_index = Cal.locate_index(start_time, end_time, freq=freq, future=True)
        self.start_index = _start_index  # 在全局日历中的开始索引
        self.end_index = _end_index  # 在全局日历中的结束索引
        self.trade_len = _end_index - _start_index + 1  # 本次交易区间总步数
        self.trade_step = 0  # 已完成步数，重置为 0

    def finished(self) -> bool:
        """
        判断是否已走完整个日历
        - 调用 strategy.generate_decisions / executor.execute 前应检查
        - self.trade_step >= self.trade_len 视为已结束；否则表示已完成的步数即 self.trade_step
        """
        return self.trade_step >= self.trade_len

    def step(self) -> None:
        if self.finished():
            raise RuntimeError(f"The calendar is finished, please reset it if you want to call it!")
        self.trade_step += 1

    def get_freq(self) -> str:
        return self.freq

    def get_trade_len(self) -> int:
        """返回总步数 trade_len"""
        return self.trade_len

    def get_trade_step(self) -> int:
        return self.trade_step

    def get_step_time(self, trade_step: int | None = None, shift: int = 0) -> Tuple[pd.Timestamp, pd.Timestamp]:
        """
        取得某个 trade_step 的左/右端点时间区间（闭区间定义）。

        端点说明：
            - Qlib 按闭区间选取时间序列，等价于 pandas.Series.loc 的行为。
            - 支持分钟级决策，1 秒的偏移小于任一交易间隔（内部通过 epsilon_change 处理右端点）。

        参数
        ----------
        trade_step : int, optional
            指定要取的步数；None 表示当前 self.trade_step。
        shift : int, optional
            时间窗口的偏移量，默认 0；>0 表示向前取更早的窗口，<0 表示向后。

        返回
        -------
        Tuple[pd.Timestamp, pd.Timestamp]
            该窗口的 [start, end] 时间戳。
        """
        if trade_step is None:
            trade_step = self.get_trade_step()
        calendar_index = self.start_index + trade_step - shift
        return self._calendar[calendar_index], epsilon_change(self._calendar[calendar_index + 1])

    def get_data_cal_range(self, rtype: str = "full") -> Tuple[int, int]:
        """
        计算数据日历区间（返回索引范围），假设：
        1) common_infra 中的交易所频率与数据日历一致
        2) 用户需要按 **天** 的索引偏移（例如 1 天=240 分钟）

        参数
        ----------
        rtype: str
            - "full": 返回整天内的索引范围
            - "step": 返回当前交易步所在区间的索引范围

        返回
        -------
        Tuple[int, int]
            (相对当日开始的左、右索引偏移)
        """
        # 可能存在性能开销，需要在调用方控制使用频率
        assert self.level_infra is not None

        day_start = pd.Timestamp(self.start_time.date())  # 当天 00:00 左闭
        day_end = epsilon_change(day_start + pd.Timedelta(days=1))  # 当天 24:00 右闭（经 epsilon 调整）
        freq = self.level_infra.get("common_infra").get("trade_exchange").freq  # 交易所频率
        _, _, day_start_idx, _ = Cal.locate_index(day_start, day_end, freq=freq)  # 当天在全局日历的起始索引

        if rtype == "full":
            _, _, start_idx, end_index = Cal.locate_index(self.start_time, self.end_time, freq=freq)
        elif rtype == "step":
            _, _, start_idx, end_index = Cal.locate_index(*self.get_step_time(), freq=freq)
        else:
            raise ValueError(f"This type of input {rtype} is not supported")

        return start_idx - day_start_idx, end_index - day_start_idx

    def get_all_time(self) -> Tuple[pd.Timestamp, pd.Timestamp]:
        """返回交易使用的 (start_time, end_time)"""
        return self.start_time, self.end_time

    # 辅助函数
    def get_range_idx(self, start_time: pd.Timestamp, end_time: pd.Timestamp) -> Tuple[int, int]:
        """
        获取闭区间 [start_time, end_time] 在当前日历中的索引范围。

        参数
        ----------
        start_time : pd.Timestamp
        end_time : pd.Timestamp

        返回
        -------
        Tuple[int, int]
            左右端点索引（闭区间），会按 [0, trade_len-1] 进行裁剪
        """
        left = int(np.searchsorted(self._calendar, start_time, side="right") - 1)
        right = int(np.searchsorted(self._calendar, end_time, side="right") - 1)
        left -= self.start_index
        right -= self.start_index

        def clip(idx: int) -> int:
            return min(max(0, idx), self.trade_len - 1)

        return clip(left), clip(right)

    def __repr__(self) -> str:
        return (
            f"class: {self.__class__.__name__}; "
            f"{self.start_time}[{self.start_index}]~{self.end_time}[{self.end_index}]: "
            f"[{self.trade_step}/{self.trade_len}]"
        )


class BaseInfrastructure:
    def __init__(self, **kwargs: Any) -> None:
        self.reset_infra(**kwargs)

    @abstractmethod
    def get_support_infra(self) -> Set[str]:
        raise NotImplementedError("`get_support_infra` is not implemented!")

    def reset_infra(self, **kwargs: Any) -> None:
        support_infra = self.get_support_infra()
        for k, v in kwargs.items():
            if k in support_infra:
                setattr(self, k, v)
            else:
                warnings.warn(f"{k} is ignored in `reset_infra`!")

    def get(self, infra_name: str) -> Any:
        if hasattr(self, infra_name):
            return getattr(self, infra_name)
        else:
            warnings.warn(f"infra {infra_name} is not found!")

    def has(self, infra_name: str) -> bool:
        return infra_name in self.get_support_infra() and hasattr(self, infra_name)

    def update(self, other: BaseInfrastructure) -> None:
        support_infra = other.get_support_infra()
        infra_dict = {_infra: getattr(other, _infra) for _infra in support_infra if hasattr(other, _infra)}
        self.reset_infra(**infra_dict)


class CommonInfrastructure(BaseInfrastructure):
    def get_support_infra(self) -> Set[str]:
        return {"trade_account", "trade_exchange"}


class LevelInfrastructure(BaseInfrastructure):
    """由执行器创建的层级基础设施，同层策略共享"""

    def get_support_infra(self) -> Set[str]:
        """
        说明当前层支持的基础设施名称

        sub_level_infra:
        - **注意**：仅在 `_init_sub_trading` 之后有效
        """
        return {"trade_calendar", "sub_level_infra", "common_infra", "executor"}

    def reset_cal(
        self,
        freq: str,
        start_time: Union[str, pd.Timestamp, None],
        end_time: Union[str, pd.Timestamp, None],
    ) -> None:
        """重置（或创建）交易日历管理器"""
        if self.has("trade_calendar"):
            self.get("trade_calendar").reset(freq, start_time=start_time, end_time=end_time)
        else:
            self.reset_infra(
                trade_calendar=TradeCalendarManager(freq, start_time=start_time, end_time=end_time, level_infra=self),
            )

    def set_sub_level_infra(self, sub_level_infra: LevelInfrastructure) -> None:
        """设置子层级 infra，便于多层执行器之间访问日历"""
        self.reset_infra(sub_level_infra=sub_level_infra)


def get_start_end_idx(trade_calendar: TradeCalendarManager, outer_trade_decision: BaseTradeDecision) -> Tuple[int, int]:
    """
    获取内层策略可用的决策级时间索引范围（基于外层决策给的限制）。
    - 注意：不适用于单笔订单级别。

    参数
    ----------
    trade_calendar : TradeCalendarManager
    outer_trade_decision : BaseTradeDecision
        外层策略生成的交易决策

    返回
    -------
    Tuple[int, int]
        (开始索引, 结束索引)
    """
    try:
        return outer_trade_decision.get_range_limit(inner_calendar=trade_calendar)
    except NotImplementedError:
        return 0, trade_calendar.get_trade_len() - 1
