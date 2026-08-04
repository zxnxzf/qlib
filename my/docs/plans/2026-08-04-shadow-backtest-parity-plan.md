# Shadow/Backtest Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Build and run a reproducible 2025-01-02 through 2026-07-28 comparison between the locked Qlib backtest and the current shadow replay, with daily order, holding, cash, NAV, and difference reports.

**Architecture:** Add a pure comparison module under my/quant/ and a thin orchestration script under my/scripts/. The script bulk-loads market bars once, runs Qlib and shadow paths independently with the same archived predictions and gate series, converts both outputs into shared snapshots, and writes ignored artifacts without touching the live shadow ledger.

**Tech Stack:** Python 3.9, pandas, Qlib backtest APIs, pytest, existing my.quant modules.

## Global Constraints

- Use /Users/bytedance/code/qlib/.venv/bin/python; install no dependencies.
- Compare 2025-01-02 through 2026-07-28; warm shadow replay from 2024-12-31.
- Use my/artifacts/candidate1_pred.pkl, 100,000 yuan, SH000905 gate, TopK=50, N_DROP=2, hold_thresh=1.
- Use open execution, 9.5% limit threshold, 0.1% impact, 0.05% open cost, 0.15% close cost, and 5 yuan minimum cost.
- Keep Qlib only_tradable=True and current shadow blocked-order behavior; do not add compatibility mode.
- Do not train a model, alter strategy parameters, write the live shadow state, or connect QMT.
- Use TDD for every implementation or defect fix.

---

### Task 1: Shared snapshot and comparison core

**Files:**
- Create: my/quant/parity.py
- Create: my/tests/test_shadow_parity.py

**Interfaces:**
- Produces: OrderSnapshot, DailySnapshot, and ParityResult dataclasses.
- Produces: compare_snapshots(qlib: dict[str, DailySnapshot], shadow: dict[str, DailySnapshot], cash_tolerance: float = 0.01) -> ParityResult.
- Produces: validate_snapshot_dates(snapshots, expected_dates: list[str]) -> None.

- [ ] **Step 1: Write failing equality and difference tests**

    from my.quant.parity import DailySnapshot, OrderSnapshot, compare_snapshots

    def test_equal_snapshots_have_full_parity():
        snap = DailySnapshot(
            "2025-01-02", 100000.0, 50000.0, True,
            {"SH600000": 5000},
            [OrderSnapshot("SH600000", "buy", 5000)],
            {},
        )
        result = compare_snapshots({snap.date: snap}, {snap.date: snap})
        assert result.summary["cash_match_rate"] == 1.0
        assert result.summary["holding_match_rate"] == 1.0
        assert result.summary["order_match_rate"] == 1.0

    def test_difference_is_located_by_date_and_stock():
        q = DailySnapshot("2025-01-02", 100000, 50000, True, {"SH600000": 5000}, [], {})
        s = DailySnapshot("2025-01-02", 99990, 49990, True, {"SH600000": 4900}, [], {})
        result = compare_snapshots({q.date: q}, {s.date: s})
        assert result.holdings_compare.iloc[0]["shares_delta"] == -100
        assert result.daily_compare.iloc[0]["nav_delta"] == -10

- [ ] **Step 2: Run RED**

    .venv/bin/python -m pytest my/tests/test_shadow_parity.py -q

Expected: ModuleNotFoundError for my.quant.parity.

- [ ] **Step 3: Implement minimal dataclasses and outer-join comparison**

    @dataclass(frozen=True)
    class OrderSnapshot:
        code: str
        side: str
        shares: int

    @dataclass
    class DailySnapshot:
        date: str
        nav: float
        cash: float
        gate_on: bool
        holdings: Dict[str, int]
        orders: List[OrderSnapshot]
        receipts: Dict[Tuple[str, str], str]

    @dataclass
    class ParityResult:
        daily_compare: pd.DataFrame
        holdings_compare: pd.DataFrame
        orders_compare: pd.DataFrame
        summary: dict

compare_snapshots must calculate shadow minus Qlib deltas and exact match rates, with 0.01 tolerance only for cash and NAV.

- [ ] **Step 4: Add RED tests for missing dates and gate mismatch**

    def test_missing_date_is_rejected():
        with pytest.raises(ValueError, match="缺少日期"):
            validate_snapshot_dates({}, ["2025-01-02", "2025-01-03"])

    def test_gate_mismatch_is_hard_error():
        q = DailySnapshot("2025-01-02", 100000, 100000, True, {}, [], {})
        s = DailySnapshot("2025-01-02", 100000, 100000, False, {}, [], {})
        with pytest.raises(ValueError, match="门控不一致"):
            compare_snapshots({q.date: q}, {s.date: s})

- [ ] **Step 5: Implement validation and run GREEN**

    .venv/bin/python -m pytest my/tests/test_shadow_parity.py -q

- [ ] **Step 6: Commit**

    git add my/quant/parity.py my/tests/test_shadow_parity.py
    git commit -m "feat: add shadow parity comparison core"

---

### Task 2: Bulk market cache and shadow replay adapter

**Files:**
- Modify: my/quant/parity.py
- Modify: my/tests/test_shadow_parity.py

**Interfaces:**
- Produces: MarketCache.from_qlib(start: str, end: str) -> MarketCache.
- Produces: MarketCache.day_bars(date: str, fields: tuple[str, ...]) -> pd.DataFrame.
- Produces: run_shadow_replay(pred, gate_by_exec_date, cache, warmup, start, end, state_dir, log=print) -> dict[str, DailySnapshot].

- [ ] **Step 1: Write RED cache projection test**

    def test_market_cache_returns_requested_fields():
        cache = MarketCache(synthetic_multiindex_bars)
        bars = cache.day_bars("2025-01-02", fields=("$close", "$factor"))
        assert bars.columns.tolist() == ["close", "factor", "prev_close"]
        assert bars.loc["SH600000", "close"] == 10.2

- [ ] **Step 2: Verify RED**

    .venv/bin/python -m pytest my/tests/test_shadow_parity.py::test_market_cache_returns_requested_fields -q

Expected: MarketCache import failure.

- [ ] **Step 3: Implement MarketCache**

from_qlib must call D.features once for $open, $close, $volume, $factor, and Ref($close,1), normalize to a (datetime, instrument) index, rename columns, and reject empty data. day_bars must reject missing dates and never forward-fill.

- [ ] **Step 4: Write RED warmup replay test**

    def test_shadow_replay_uses_warmup_only_to_create_first_order(tmp_path):
        snapshots = run_shadow_replay(
            pred=synthetic_pred,
            gate_by_exec_date=pd.Series({"2025-01-02": True}),
            cache=synthetic_cache,
            warmup="2024-12-31",
            start="2025-01-02",
            end="2025-01-02",
            state_dir=tmp_path,
            log=lambda _msg: None,
        )
        assert list(snapshots) == ["2025-01-02"]
        assert snapshots["2025-01-02"].holdings == {"SH600000": 9500}

- [ ] **Step 5: Implement isolated replay**

Temporarily replace data.day_bars, signal_.scores_for, and gate.gate_for_next_day; call nightly.run_evening; snapshot state after each comparison day; key orders by execution date; restore dependencies in finally. Reject a non-empty state_dir.

- [ ] **Step 6: Run GREEN**

    .venv/bin/python -m pytest my/tests/test_shadow_parity.py my/tests/test_shadow_core.py -q

- [ ] **Step 7: Commit**

    git add my/quant/parity.py my/tests/test_shadow_parity.py
    git commit -m "feat: add cached shadow parity replay"

---

### Task 3: Qlib recording adapter and report writer

**Files:**
- Modify: my/quant/parity.py
- Modify: my/tests/test_shadow_parity.py
- Create: my/scripts/compare_shadow_backtest.py

**Interfaces:**
- Produces: run_qlib_backtest(pred, gate_by_exec_date, start, end, log=print) -> dict[str, DailySnapshot].
- Produces: write_parity_artifacts(result, output_dir, metadata) -> None.
- CLI: compare_shadow_backtest.py --start YYYY-MM-DD --end YYYY-MM-DD [--output PATH].

- [ ] **Step 1: Write RED normalization tests**

    def test_position_to_holdings_excludes_cash_fields():
        position = FakePosition(cash=1234.5, holdings={"SH600000": 1000})
        assert position_to_holdings(position) == {"SH600000": 1000}

    def test_qlib_buy_order_is_normalized():
        assert normalize_qlib_order(FakeOrder("SH600000", BUY, 1000)) ==             OrderSnapshot("SH600000", "buy", 1000)

- [ ] **Step 2: Verify RED, then implement helpers and recording strategy**

Subclass existing GatedTopkDropout in parity.py, call super().generate_trade_decision, capture returned orders by trade date, and preserve execution unchanged. Run backtest_daily with the locked account and exchange settings; convert report and positions to snapshots.

- [ ] **Step 3: Write RED artifact test**

    def test_writer_creates_required_files(tmp_path):
        write_parity_artifacts(result, tmp_path, {"start": "2025-01-02", "end": "2026-07-28"})
        assert {p.name for p in tmp_path.iterdir()} == {
            "daily_compare.csv", "holdings_compare.csv", "orders_compare.csv",
            "summary.json", "report.md",
        }

- [ ] **Step 4: Implement writer and thin CLI**

The CLI creates a timestamped directory under my/artifacts/shadow_backtest_parity, loads predictions and gate data once, builds MarketCache, runs both adapters, validates dates, compares, writes artifacts, and prints output path plus final NAVs, maximum NAV delta, holding match rate, and order match rate.

- [ ] **Step 5: Run GREEN verification**

    .venv/bin/python -m pytest my/tests/test_shadow_parity.py my/tests/test_shadow_core.py -q
    .venv/bin/python -m py_compile my/quant/*.py my/scripts/compare_shadow_backtest.py
    git diff --check

- [ ] **Step 6: Commit**

    git add my/quant/parity.py my/tests/test_shadow_parity.py my/scripts/compare_shadow_backtest.py
    git commit -m "feat: compare shadow replay with qlib backtest"

---

### Task 4: Run and diagnose the real 18-month comparison

**Files:**
- Generate: my/artifacts/shadow_backtest_parity/<run-id>/*
- Modify production only after a new failing regression test proves a defect.

- [ ] **Step 1: Run real comparison**

    .venv/bin/python my/scripts/compare_shadow_backtest.py       --start 2025-01-02 --end 2026-07-28

Expected: new artifact directory; no live shadow state mutation.

- [ ] **Step 2: Verify five outputs and isolation**

Check summary.json, report.md, three CSV files, and git status for my/quant_state.

- [ ] **Step 3: Classify differences**

Inspect the largest absolute NAV deltas, every holding/order mismatch date, and receipts with blocked_limit, suspended, or no_data. Classify as execution_tradability, rounding_or_cost, initialization, or unexplained.

- [ ] **Step 4: Fix only unexplained defects with fresh RED/GREEN cycles**

Do not change intentional only_tradable semantics. After each proven fix, rerun unit tests and the real comparison.

- [ ] **Step 5: Save final verification evidence**

    .venv/bin/python -m pytest my/tests/test_shadow_parity.py my/tests/test_shadow_core.py -q
    .venv/bin/python -m py_compile my/quant/*.py my/scripts/compare_shadow_backtest.py
    git diff --check

Expected: no test/syntax/diff failures and no unexplained gate or date mismatch.

---

### Task 5: Document measured results

**Files:**
- Modify: my/docs/shadow_mode.md
- Modify: my/docs/features.md
- Modify: HANDOVER.md
- Modify: PROJECT_MEMORY.md

- [ ] **Step 1: Record exact results**

Document date range, both final NAVs, max NAV delta, holding/order match rates, affected tradability dates, artifact path, test count, and the remaining QMT backup-candidate decision.

- [ ] **Step 2: Refresh project memory and Git baseline**

Replace pending parity work with measured status; keep the 23:00 Q3 model backfill as next work.

- [ ] **Step 3: Final verification**

    git diff --check
    .venv/bin/python -m pytest my/tests/test_shadow_parity.py my/tests/test_shadow_core.py -q
    .venv/bin/python -m py_compile my/quant/*.py my/scripts/compare_shadow_backtest.py

- [ ] **Step 4: Commit documentation and any final proven fixes**

    git add my/docs/shadow_mode.md my/docs/features.md HANDOVER.md PROJECT_MEMORY.md       my/quant/parity.py my/tests/test_shadow_parity.py my/scripts/compare_shadow_backtest.py
    git commit -m "docs: record shadow-backtest parity result"
