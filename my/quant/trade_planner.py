"""共享交易规划器的纯数据模型和卖出阶段。"""

from dataclasses import dataclass, field
from typing import Dict, Optional, Sequence, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .execution import Receipt


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
    provenance: Dict[str, str] = field(default_factory=dict)


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
    price_floor: float = 0.0
    price_ceiling: float = 0.0
    candidate_rank: Optional[int] = None
    batch_id: str = ""


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
        quote = market.quotes[code]
        floor, ceiling = price_band(
            quote.bid1,
            "sell",
            quote.high_limit,
            quote.low_limit,
            float(package.params.get("max_slippage", 0.003)),
        )
        orders.append(
            PlannedOrder(
                code=code,
                side="sell",
                shares=min(holding.shares, holding.available_shares),
                limit_price=float(quote.bid1),
                reason=reason,
                price_floor=floor,
                price_ceiling=ceiling,
                batch_id=package.batch_id,
            )
        )
    return PlanResult(orders=tuple(orders), skips=tuple(skips))


def price_band(
    reference: float,
    side: str,
    high_limit: float,
    low_limit: float,
    max_slippage: float,
) -> Tuple[float, float]:
    """返回以首次盘口为基准的一次改价保护区间。"""
    reference = float(reference)
    max_slippage = float(max_slippage)
    if reference <= 0 or not 0 <= max_slippage <= 1:
        raise ValueError("价格或最大偏离比例无效")
    if side == "buy":
        return reference, min(reference * (1 + max_slippage), float(high_limit))
    if side == "sell":
        return max(reference * (1 - max_slippage), float(low_limit)), reference
    raise ValueError(f"未知交易方向: {side}")


def apply_receipts(account: AccountSnapshot, receipts: Sequence["Receipt"]) -> AccountSnapshot:
    """只按真实成交股数、价格和费用生成新的账户快照。"""
    cash = float(account.cash)
    holdings = dict(account.holdings)
    filled_statuses = {"filled", "partial", "partially_filled", "partial_cancelled"}
    for receipt in receipts:
        shares = int(receipt.shares)
        if shares < 0:
            raise ValueError(f"成交股数不能为负: {receipt.code}")
        if shares == 0:
            continue
        if receipt.status not in filled_statuses:
            raise ValueError(f"非成交状态包含成交股数: {receipt.code} {receipt.status}")
        value = shares * float(receipt.price)
        cost = float(receipt.cost)
        if value < 0 or cost < 0:
            raise ValueError(f"成交金额或费用无效: {receipt.code}")

        if receipt.side == "sell":
            holding = holdings.get(receipt.code)
            if holding is None or shares > holding.shares:
                raise ValueError(f"卖出回执超过持仓: {receipt.code}")
            remaining = holding.shares - shares
            if remaining:
                holdings[receipt.code] = HoldingSnapshot(
                    shares=remaining,
                    available_shares=max(holding.available_shares - shares, 0),
                    held_days=holding.held_days,
                )
            else:
                holdings.pop(receipt.code)
            cash += value - cost
        elif receipt.side == "buy":
            spend = value + cost
            if spend > cash + 1e-8:
                raise ValueError(f"买入回执导致现金不足: {receipt.code}")
            old = holdings.get(receipt.code, HoldingSnapshot(0, 0, 0))
            holdings[receipt.code] = HoldingSnapshot(
                shares=old.shares + shares,
                available_shares=old.available_shares,
                held_days=old.held_days if old.shares else 0,
            )
            cash -= spend
        else:
            raise ValueError(f"未知成交方向: {receipt.side}")
    return AccountSnapshot(cash=cash, holdings=holdings)


def _buy_skip_reason(quote: Optional[QuoteSnapshot], exec_date: str) -> str:
    if quote is None:
        return "no_quote"
    if not quote.timestamp.startswith(exec_date):
        return "stale_quote"
    if quote.risk_blocked:
        return quote.risk_reason or "risk_blocked"
    if not quote.buyable:
        return quote.status or "not_buyable"
    if quote.ask1 <= 0:
        return "invalid_ask1"
    if quote.high_limit > 0 and quote.ask1 >= quote.high_limit:
        return "blocked_limit"
    return ""


def _affordable_lot_shares(budget: float, price: float, lot: int, open_cost: float, min_cost: float) -> int:
    shares = int(budget // (price * lot)) * lot
    while shares >= lot:
        value = shares * price
        if value + max(value * open_cost, min_cost) <= budget + 1e-8:
            return shares
        shares -= lot
    return 0


def plan_buys(package: SignalPackage, account_after_sells: AccountSnapshot, market: MarketSnapshot) -> PlanResult:
    """从锁定的 Top100 中补选，并按实际卖后现金计算整手买单。"""
    if package.exec_date != market.exec_date:
        raise ValueError(f"执行日不一致: signal={package.exec_date}, market={market.exec_date}")
    if not package.gate_on:
        return PlanResult(orders=(), skips=())

    holdings = {code for code, holding in account_after_sells.holdings.items() if holding.shares > 0}
    slots = max(int(package.params.get("topk", 50)) - len(holdings), 0)
    if slots == 0:
        return PlanResult(orders=(), skips=())

    selected = []
    skips = []
    for candidate in package.candidates:
        if len(selected) >= slots:
            break
        if candidate.code in holdings:
            skips.append(PlanSkip(candidate.code, "buy", "already_held"))
            continue
        skip_reason = _buy_skip_reason(market.quotes.get(candidate.code), package.exec_date)
        if skip_reason:
            skips.append(PlanSkip(candidate.code, "buy", skip_reason))
            continue
        selected.append(candidate)

    if not selected:
        return PlanResult(orders=(), skips=tuple(skips))

    budget = float(account_after_sells.cash) * float(package.params.get("risk_degree", 0.95)) / len(selected)
    lot = int(package.params.get("lot", 100))
    open_cost = float(package.params.get("open_cost", 0.0))
    min_cost = float(package.params.get("min_cost", 0.0))
    max_slippage = float(package.params.get("max_slippage", 0.003))
    orders = []
    for candidate in selected:
        quote = market.quotes[candidate.code]
        shares = _affordable_lot_shares(budget, quote.ask1, lot, open_cost, min_cost)
        if shares < lot:
            skips.append(PlanSkip(candidate.code, "buy", "insufficient_for_one_lot"))
            continue
        floor, ceiling = price_band(
            quote.ask1,
            "buy",
            quote.high_limit,
            quote.low_limit,
            max_slippage,
        )
        orders.append(
            PlannedOrder(
                code=candidate.code,
                side="buy",
                shares=shares,
                limit_price=float(quote.ask1),
                reason="top100_entry",
                price_floor=floor,
                price_ceiling=ceiling,
                candidate_rank=candidate.rank,
                batch_id=package.batch_id,
            )
        )
    return PlanResult(orders=tuple(orders), skips=tuple(skips))
