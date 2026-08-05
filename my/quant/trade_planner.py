"""共享交易规划器的纯数据模型和卖出阶段。"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class SignalCandidate:
    code: str
    score: float
    rank: int
    reference_close: float


@dataclass(frozen=True)
class SignalPackage:
    batch_id: str
    signal_date: str
    exec_date: str
    gate_on: bool
    candidates: Tuple[SignalCandidate, ...]
    holding_scores: Dict[str, Optional[float]]
    params: Dict[str, float]


@dataclass(frozen=True)
class HoldingSnapshot:
    shares: int
    available_shares: int
    held_days: int


@dataclass(frozen=True)
class AccountSnapshot:
    cash: float
    holdings: Dict[str, HoldingSnapshot]


@dataclass(frozen=True)
class QuoteSnapshot:
    code: str
    timestamp: str
    bid1: float
    ask1: float
    last: float
    high_limit: float
    low_limit: float
    buyable: bool
    sellable: bool
    status: str
    risk_blocked: bool = False
    risk_reason: str = ""


@dataclass(frozen=True)
class MarketSnapshot:
    exec_date: str
    quotes: Dict[str, QuoteSnapshot]


@dataclass(frozen=True)
class PlannedOrder:
    code: str
    side: str
    shares: int
    limit_price: float
    reason: str


@dataclass(frozen=True)
class PlanSkip:
    code: str
    side: str
    reason: str


@dataclass(frozen=True)
class PlanResult:
    orders: Tuple[PlannedOrder, ...]
    skips: Tuple[PlanSkip, ...]


def _sell_skip_reason(holding: HoldingSnapshot, quote: Optional[QuoteSnapshot], hold_thresh: int) -> str:
    if holding.held_days < hold_thresh:
        return "hold_day_protection"
    if holding.available_shares <= 0:
        return "no_available_shares"
    if quote is None:
        return "no_quote"
    if quote.risk_blocked:
        return quote.risk_reason or "risk_blocked"
    if not quote.sellable:
        return quote.status or "not_sellable"
    if quote.bid1 <= 0:
        return "invalid_bid1"
    return ""


def plan_sells(package: SignalPackage, account: AccountSnapshot, market: MarketSnapshot) -> PlanResult:
    """按锁定信号规划卖单，不读取外部状态，也不修改账户。"""
    if package.exec_date != market.exec_date:
        raise ValueError(f"执行日不一致: signal={package.exec_date}, market={market.exec_date}")

    hold_thresh = int(package.params.get("hold_thresh", 1))
    limit = len(account.holdings) if not package.gate_on else int(package.params.get("n_drop", 2))
    if limit <= 0:
        return PlanResult(orders=(), skips=())

    if package.gate_on:
        ranked_codes = sorted(
            account.holdings,
            key=lambda code: (
                float("-inf")
                if package.holding_scores.get(code) is None
                else float(package.holding_scores[code]),
                code,
            ),
        )
        reason = "bottom_rank_dropout"
    else:
        ranked_codes = sorted(account.holdings)
        reason = "gate_off_liquidate"

    orders = []
    skips = []
    for code in ranked_codes:
        if len(orders) >= limit:
            break
        holding = account.holdings[code]
        skip_reason = _sell_skip_reason(holding, market.quotes.get(code), hold_thresh)
        if skip_reason:
            skips.append(PlanSkip(code=code, side="sell", reason=skip_reason))
            continue
        orders.append(
            PlannedOrder(
                code=code,
                side="sell",
                shares=min(holding.shares, holding.available_shares),
                limit_price=float(market.quotes[code].bid1),
                reason=reason,
            )
        )
    return PlanResult(orders=tuple(orders), skips=tuple(skips))
