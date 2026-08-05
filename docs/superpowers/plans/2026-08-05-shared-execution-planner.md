# Shared Execution Planner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Qlib backtest, shadow replay, and the future QMT adapter use one pure execution planner, allowing only market-data, fill, latency, and broker constraints to create explainable differences.

**Architecture:** T-1 creates an immutable `SignalPackage` containing the Top100 ranked queue and locked strategy parameters. A pure planner exposes separate sell and buy stages over account and market snapshots; Qlib, shadow, and QMT adapters provide facts and execute returned orders. Shadow and QMT wait for actual sell receipts before planning buys, while the historical Qlib adapter simulates the same two-stage sequence synchronously.

**Tech Stack:** Python 3.9, pandas, pytest, existing Qlib `backtest_daily`, JSON/CSV state files, project `.venv`.

## Global Constraints

- Use `/Users/bytedance/code/qlib/.venv/bin/python`; do not install global dependencies.
- T-1 fixes model scores, gate state, and ranking; execution-day prices do not rescore the model.
- Candidate queue is Top100; target holdings are 50; maximum replacement is 2.
- Execution window is 09:30–09:31 after the first fresh valid quote.
- Sell first; wait at most 30 seconds; buy planning uses only actual post-sell cash and holdings.
- Buy/sell limit prices use ask1/bid1 with at most 0.3% reprice protection and one reprice.
- A buy unfilled for 30 seconds is cancelled and leaves cash; it does not switch to another backup.
- Insufficient cash for one lot leaves cash and does not expand the candidate queue.
- Stale signal, stale quote, unavailable account, gate mismatch, or unknown difference blocks real QMT execution.
- Preserve existing user changes in `my/docs/research_log.md`, `my/scripts/exp_gate905.py`, and `my/scripts/exp_gate_thermometer.py`.
- Do not validate real broker orders without a QMT trial environment; provide an adapter boundary and simulator first.

---

## Task 1: Pure planner data model and sell stage

**Files:** Create `my/quant/trade_planner.py` and `my/tests/test_trade_planner.py`.

**Interfaces:**

```python
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

def plan_sells(package: SignalPackage, account: AccountSnapshot,
               market: MarketSnapshot) -> PlanResult: ...
```

- [ ] **Step 1: Write failing tests** for gate-off liquidation, gate-on bottom-two selection, non-sellable holdings, hold-day protection, and deterministic ordering.

```python
def test_plan_sells_chooses_two_lowest_sellable_holdings():
    result = plan_sells(package, account, market)
    assert [(o.code, o.side) for o in result.orders] == [
        ("SH600001", "sell"), ("SH600002", "sell")
    ]
```

- [ ] **Step 2: Run the focused test and verify it fails:** `./.venv/bin/python -m pytest my/tests/test_trade_planner.py -q`; expected failure because the new module does not exist.
- [ ] **Step 3: Implement only the immutable data classes and `plan_sells`**. Sort held positions by `holding_scores.get(code, -inf)`, select at most `n_drop` sellable positions, and return explicit skip reasons. Do not import Qlib, ledger, or executor modules.
- [ ] **Step 4: Run the focused test and verify it passes:** `./.venv/bin/python -m pytest my/tests/test_trade_planner.py -q`.
- [ ] **Step 5: Commit:** `git add my/quant/trade_planner.py my/tests/test_trade_planner.py && git commit -m "feat: add shared trade planner sell stage"`.

## Task 2: Immutable signal package

**Files:** Create `my/quant/signal_package.py` and `my/tests/test_signal_package.py`; modify `my/quant/ledger.py`.

**Interfaces:**

```python
def build_signal_package(scores: pd.Series, signal_date: str, exec_date: str,
                         gate_on: bool, holding_codes: list[str],
                         params: dict, batch_id: str) -> SignalPackage: ...
def save_signal_package(package: SignalPackage, root: Path) -> Path: ...
def load_signal_package(exec_date: str, root: Path) -> SignalPackage: ...
```

- [ ] **Step 1: Write failing tests** for Top100 ordering, deterministic ties, current-holding scores, JSON round-trip, atomic rename, and date/checksum rejection.
- [ ] **Step 2: Run `./.venv/bin/python -m pytest my/tests/test_signal_package.py -q` and verify the expected import failure.**
- [ ] **Step 3: Implement package construction and JSON persistence**. Serialize `<root>/signals/<exec_date>.json.tmp`, rename atomically, and validate batch date, strategy parameters, and content hash on load. The package must not contain final buy shares.
- [ ] **Step 4: Run the focused tests and verify all pass.**
- [ ] **Step 5: Commit:** `git add my/quant/signal_package.py my/quant/ledger.py my/tests/test_signal_package.py && git commit -m "feat: persist immutable signal packages"`.

## Task 3: Buy planning and receipt application

**Files:** Modify `my/quant/trade_planner.py`, `my/quant/execution.py`, and `my/tests/test_trade_planner.py`.

**Interfaces:**

```python
class ExecutionAdapter(Protocol):
    def submit_and_wait(self, orders: Sequence[PlannedOrder], exec_date: str,
                        account: AccountSnapshot, market: MarketSnapshot,
                        wait_seconds: int) -> List[Receipt]: ...

def apply_receipts(account: AccountSnapshot,
                   receipts: Sequence[Receipt]) -> AccountSnapshot: ...
def plan_buys(package: SignalPackage, account_after_sells: AccountSnapshot,
              market: MarketSnapshot) -> PlanResult: ...
def price_band(reference: float, side: str, high_limit: float,
               low_limit: float, max_slippage: float) -> Tuple[float, float]: ...
```

- [ ] **Step 1: Write failing tests** for actual post-sell cash, Top100 backup filtering, one-lot affordability, bid/ask bands, blocked candidates, and partial sell receipts.

```python
def test_plan_buys_uses_actual_cash_after_partial_sell():
    updated = apply_receipts(account, [filled_sell, blocked_sell])
    result = plan_buys(package, updated, market)
    assert result.orders[0].shares == 100
```

- [ ] **Step 2: Run `./.venv/bin/python -m pytest my/tests/test_trade_planner.py -q` and verify missing-function failures.**
- [ ] **Step 3: Implement `plan_buys`** with slots `max(50 - len(updated_holdings), 0)`, rank traversal through Top100, held/stale/limit/suspended/ST/delist/long-suspend filtering, `cash * risk_degree / selected_count`, and 100-share floor. A below-lot candidate leaves cash and records a skip. Do not retry in the pure planner.
- [ ] **Step 4: Implement `apply_receipts` and the adapter protocol** so only filled shares change holdings and only filled sale proceeds become cash; partial, blocked, cancelled, rejected, and timed-out quantities never release buy slots. Keep `ShadowExecutor` as the first `ExecutionAdapter`; tests use a deterministic fake adapter to simulate partial fills and timeouts. Do not add a real QMT API call.
- [ ] **Step 5: Run `./.venv/bin/python -m pytest my/tests/test_trade_planner.py -q` and verify all planner tests pass.**
- [ ] **Step 6: Commit:** `git add my/quant/trade_planner.py my/quant/execution.py my/tests/test_trade_planner.py && git commit -m "feat: add shared buy planning and receipt accounting"`.

## Task 4: Two-phase shadow orchestration

**Files:** Modify `my/quant/nightly.py`, `my/quant/ledger.py`, `my/scripts/shadow_run.py`, and `my/tests/test_shadow_core.py`; create `my/tests/test_shadow_planner_integration.py`.

**Interfaces:** `nightly.prepare(asof, skip_update=False) -> dict`, `nightly.execute(exec_date, wait_seconds=30) -> dict`, and a compatibility `nightly.run_evening(...)` wrapper for historical backfill.

- [ ] **Step 1: Write failing integration tests** proving sell receipts persist before buy planning, blocked sells release no buy slot, failed buys remain cash, duplicate batches are idempotent, and restart from `sell_closed` does not repeat sells.
- [ ] **Step 2: Run `./.venv/bin/python -m pytest my/tests/test_shadow_core.py my/tests/test_shadow_planner_integration.py -q` and verify missing stage/API failures.**
- [ ] **Step 3: Extend the ledger** with `phase`, `pending_batch_id`, stage-aware orders and receipts, while defaulting old state files and CSVs to compatible values.
- [ ] **Step 4: Implement `nightly.prepare`** to check freshness, calculate T-1 scores/gate, build the next `SignalPackage`, and persist `signal_ready` without final share calculation.
- [ ] **Step 5: Implement `nightly.execute`** to load the package and actual account, plan/settle sells, wait/recover up to 30 seconds through the adapter, apply receipts, plan/settle buys, persist every receipt, and finish as `completed`, `partial`, or `aborted`.
- [ ] **Step 6: Keep `run_evening` as the backfill wrapper**: execute pending package for `asof`, mark to market, calculate the next gate, and prepare the next package. Add `prepare` and `execute` commands to `shadow_run.py`.
- [ ] **Step 7: Run the complete shadow tests** and verify no negative cash, oversell, duplicate order, or duplicate NAV.
- [ ] **Step 8: Commit:** `git add my/quant/nightly.py my/quant/ledger.py my/scripts/shadow_run.py my/tests/test_shadow_core.py my/tests/test_shadow_planner_integration.py && git commit -m "feat: execute shadow packages in sell-buy phases"`.

## Task 5: Qlib adapter using the shared planner

**Files:** Create `my/quant/qlib_adapter.py` and `my/tests/test_qlib_planner_adapter.py`; modify `my/quant/parity.py` and `my/tests/test_shadow_parity.py`.

**Interfaces:** `SharedPlannerStrategy.generate_trade_decision(execute_result=None) -> TradeDecisionWO` and `run_qlib_shared_planner_backtest(pred, gate_by_exec_date, start, end, factor_cache=None, impact_cost=None) -> dict[str, DailySnapshot]`.

- [ ] **Step 1: Write failing tests** for T-1 package dates, raw-share/factor conversion, temporary sell execution before buy planning, gate-off liquidation, Top100 backup selection, and forwarded Qlib exchange parameters.
- [ ] **Step 2: Run `./.venv/bin/python -m pytest my/tests/test_qlib_planner_adapter.py -q` and verify missing-adapter failures.**
- [ ] **Step 3: Implement the adapter**. Build `AccountSnapshot` from Qlib Position, convert adjusted shares to raw shares using the execution-day factor, build historical `MarketSnapshot` from Qlib Exchange tradability/open price, call the shared sell planner, simulate sells on a deep-copied Qlib position, apply receipts, call the buy planner, then convert raw planned shares back to Qlib adjusted order amounts.
- [ ] **Step 4: Route `parity.run_qlib_backtest` through this adapter** while preserving current report and snapshot APIs; retain the existing injectable `backtest_daily` test.
- [ ] **Step 5: Run `./.venv/bin/python -m pytest my/tests/test_shadow_parity.py my/tests/test_qlib_planner_adapter.py -q` and verify all pass.**
- [ ] **Step 6: Commit:** `git add my/quant/qlib_adapter.py my/quant/parity.py my/tests/test_shadow_parity.py my/tests/test_qlib_planner_adapter.py && git commit -m "feat: route qlib backtest through shared planner"`.

## Task 6: Rebuild historical parity and documentation

**Files:** Modify `my/quant/parity.py`, `my/scripts/compare_shadow_backtest.py`, `my/tests/test_shadow_parity.py`, `my/docs/shadow_mode.md`, `my/docs/features.md`, and `HANDOVER.md`.

- [ ] **Step 1: Add failing report tests** for stage-aware rows, candidate skip reasons, planner mismatch counts, direct rejection counts, and strict zero-impact output.
- [ ] **Step 2: Run `./.venv/bin/python -m pytest my/tests/test_shadow_parity.py -q` and verify the new assertions fail.**
- [ ] **Step 3: Update parity replay** so both sides consume the same `SignalPackage`, use the shared planner, record signal/exec dates and planned-vs-filled orders, and classify only explicit adapter statuses as execution constraints.
- [ ] **Step 4: Run the 379-day comparison** into a new ignored directory:

```bash
./.venv/bin/python my/scripts/compare_shadow_backtest.py \
  --output-dir my/artifacts/shadow_backtest_parity/shared-planner-20250102-20260728 \
  --strict-cost-control
```

- [ ] **Step 5: Inspect** 2025-01-15, all six historical limit-up candidates, at least five backup selections, partial/blocked receipts, and the largest NAV delta; record the evidence in the report.
- [ ] **Step 6: Update docs** with shared-planner commands, semantics, evidence, and the remaining QMT trial prerequisite.
- [ ] **Step 7: Commit:** `git add my/quant/parity.py my/scripts/compare_shadow_backtest.py my/tests/test_shadow_parity.py my/docs/shadow_mode.md my/docs/features.md HANDOVER.md && git commit -m "feat: validate shared planner across shadow and qlib"`.

## Task 7: Final verification and memory handoff

**Files:** Verify all changed `my/quant`, `my/scripts`, and `my/tests` files; modify `PROJECT_MEMORY.md`.

- [ ] **Step 1: Run the complete focused suite:**

```bash
./.venv/bin/python -m pytest my/tests/test_shadow_core.py my/tests/test_shadow_parity.py my/tests/test_trade_planner.py my/tests/test_signal_package.py my/tests/test_shadow_planner_integration.py my/tests/test_qlib_planner_adapter.py -q
```

Expected: zero failures.

- [ ] **Step 2: Compile changed modules:** `./.venv/bin/python -m py_compile my/quant/*.py my/scripts/shadow_run.py my/scripts/compare_shadow_backtest.py` (expected exit code 0).
- [ ] **Step 3: Run `git diff --check` and verify only the pre-existing research log and two untracked experiment scripts remain outside implementation commits.**
- [ ] **Step 4: Update `PROJECT_MEMORY.md`** with final commit, test count, artifact path, unresolved QMT-trial dependency, and the next safe action; do not add `my/quant_state/` artifacts.
- [ ] **Step 5: Commit:** `git add PROJECT_MEMORY.md && git commit -m "docs: record shared planner verification"`.
