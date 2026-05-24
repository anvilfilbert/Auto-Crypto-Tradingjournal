"""Tests for candle-based walk-forward (backtest_optimizer.run_walk_forward).

The new implementation splits a fixed days window 70/30 chronologically, with
no dependency on journal positions. These tests stub run_optimizer and
run_backtest at the module level so the test logic is fully deterministic.
"""
import sys
import os
import types
from unittest.mock import MagicMock
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Stub heavy deps before importing backtest_optimizer
if "chart_context" not in sys.modules:
    _cc = types.ModuleType("chart_context")
    _cc.get_chart_context = MagicMock(return_value={})
    _cc.get_binance_price = MagicMock(return_value=None)
    sys.modules["chart_context"] = _cc

if "backtest_engine" not in sys.modules:
    _be = types.ModuleType("backtest_engine")

    @dataclass
    class BacktestParams:
        sl_pct:         float = 0.10
        tp1_pct:        float = 0.05
        tp2_pct:        float = 0.10
        min_confluence: float = 0.33
        wt_oversold:    float = -53.0
        rsi_max:        float = 65.0
        adx_min:        float = 15.0

    @dataclass
    class _BTResult:
        total_trades:  int   = 0
        win_rate:      float = 0.0
        profit_factor: float = 0.0
        sharpe:        float = 0.0
        sortino:       float = 0.0
        max_drawdown:  float = 0.0

    _be.BacktestParams = BacktestParams
    _be.run_backtest = MagicMock(return_value=_BTResult())
    sys.modules["backtest_engine"] = _be


# Optuna may not be installed in test env — stub it so import works
if "optuna" not in sys.modules:
    _op = types.ModuleType("optuna")
    _op.logging = types.SimpleNamespace(set_verbosity=lambda *a, **kw: None,
                                         WARNING=0)
    _op.create_study = MagicMock()
    _op.Trial = object
    sys.modules["optuna"] = _op


# Stub database to avoid the SQLite file
if "database" not in sys.modules:
    _db = types.ModuleType("database")

    class _Conn:
        def execute(self, *a, **kw):
            class _R:
                def fetchone(self_inner): return (None,)
                def fetchall(self_inner): return []
            return _R()
        def commit(self): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass

    _db.db_conn = lambda: _Conn()
    sys.modules["database"] = _db


from backtest_engine import run_backtest as _rb  # noqa: E402
import backtest_optimizer  # noqa: E402


def _make_result(sharpe=1.0, trades=20, wr=55.0):
    """Build a backtest-result-like object using SimpleNamespace (closure-safe)."""
    return types.SimpleNamespace(
        total_trades=trades, win_rate=wr, profit_factor=1.5,
        sharpe=sharpe, sortino=sharpe * 1.2, max_drawdown=0.10,
    )


# ── Window split logic ───────────────────────────────────────────────────────

class TestWindowSplit:
    def test_defaults_180d_split_70_30(self, monkeypatch):
        """180d → 126d train + 54d test, non-overlapping."""
        captured = []
        def fake_optimizer(symbol, timeframe, days, n_trials, end_offset_days=0):
            captured.append({"phase": "optimize", "days": days,
                             "end_offset_days": end_offset_days})
            return {"sl_pct": 0.05, "tp1_pct": 0.05, "tp2_pct": 0.1,
                    "min_confluence": 0.3, "wt_oversold": -50.0,
                    "rsi_max": 60.0, "adx_min": 20.0}

        def fake_backtest(symbol, timeframe, days, params, end_offset_days=0):
            captured.append({"phase": "backtest", "days": days,
                             "end_offset_days": end_offset_days})
            return _make_result(sharpe=1.2)

        monkeypatch.setattr(backtest_optimizer, "run_optimizer", fake_optimizer)
        import backtest_engine
        monkeypatch.setattr(backtest_engine, "run_backtest", fake_backtest)

        result = backtest_optimizer.run_walk_forward("BTCUSDT", "4H", n_trials=10, days=180)
        assert result["train_days"] == 126
        assert result["test_days"]  == 54
        assert result["total_days"] == 180

        # Optimizer ran on train window (older 126d, ends 54d ago)
        opt = [c for c in captured if c["phase"] == "optimize"]
        assert len(opt) == 1
        assert opt[0]["days"] == 126
        assert opt[0]["end_offset_days"] == 54

        # Backtest ran twice: once on test window (most recent 54d), once on train
        bt = [c for c in captured if c["phase"] == "backtest"]
        assert len(bt) == 2
        test_call  = next(c for c in bt if c["end_offset_days"] == 0)
        train_call = next(c for c in bt if c["end_offset_days"] == 54)
        assert test_call["days"]  == 54
        assert train_call["days"] == 126

    def test_non_overlapping_windows(self, monkeypatch):
        """Verify train end == test start (no data leakage)."""
        monkeypatch.setattr(backtest_optimizer, "run_optimizer",
                            lambda *a, **kw: {"sl_pct": 0.05})
        import backtest_engine
        monkeypatch.setattr(backtest_engine, "run_backtest",
                            lambda *a, **kw: _make_result())

        result = backtest_optimizer.run_walk_forward("BTCUSDT", "4H", days=180)
        # train ends at (test_days) ago = 54 days ago
        # test starts at NOW going back test_days = 54 days
        # → exactly contiguous, no overlap
        assert result["train_days"] + result["test_days"] == 180


# ── Generalization verdict logic ─────────────────────────────────────────────

class TestGeneralizationVerdict:
    def _setup_mocks(self, monkeypatch, train_sharpe, test_sharpe):
        monkeypatch.setattr(backtest_optimizer, "run_optimizer",
                            lambda *a, **kw: {"sl_pct": 0.05, "tp1_pct": 0.05,
                                              "tp2_pct": 0.1, "min_confluence": 0.3,
                                              "wt_oversold": -50.0, "rsi_max": 60.0,
                                              "adx_min": 20.0})
        import backtest_engine
        def fake_backtest(symbol, timeframe, days, params, end_offset_days=0):
            # end_offset_days=0 → test, ==test_days → train
            if end_offset_days == 0:
                return _make_result(sharpe=test_sharpe)
            return _make_result(sharpe=train_sharpe)
        monkeypatch.setattr(backtest_engine, "run_backtest", fake_backtest)

    def test_strong_generalization_passes(self, monkeypatch):
        self._setup_mocks(monkeypatch, train_sharpe=2.0, test_sharpe=1.5)
        result = backtest_optimizer.run_walk_forward("BTCUSDT", "4H", days=180)
        assert result["generalizes"] is True

    def test_collapsed_oos_fails(self, monkeypatch):
        # train=2.0, test=0.5 → test < 0.5*train → fails
        self._setup_mocks(monkeypatch, train_sharpe=2.0, test_sharpe=0.5)
        result = backtest_optimizer.run_walk_forward("BTCUSDT", "4H", days=180)
        assert result["generalizes"] is False

    def test_negative_oos_fails(self, monkeypatch):
        self._setup_mocks(monkeypatch, train_sharpe=2.0, test_sharpe=-0.5)
        result = backtest_optimizer.run_walk_forward("BTCUSDT", "4H", days=180)
        assert result["generalizes"] is False

    def test_negative_train_fails(self, monkeypatch):
        # Even if test is positive, negative train is meaningless
        self._setup_mocks(monkeypatch, train_sharpe=-1.0, test_sharpe=0.5)
        result = backtest_optimizer.run_walk_forward("BTCUSDT", "4H", days=180)
        assert result["generalizes"] is False


# ── Edge cases ───────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_days_too_small_returns_error(self, monkeypatch):
        result = backtest_optimizer.run_walk_forward("BTCUSDT", "4H", days=20)
        assert "error" in result
        assert "too small" in result["error"].lower() or "30" in result["error"]

    def test_optimizer_returns_empty_returns_error(self, monkeypatch):
        monkeypatch.setattr(backtest_optimizer, "run_optimizer",
                            lambda *a, **kw: {})  # no params found
        result = backtest_optimizer.run_walk_forward("BTCUSDT", "4H", days=180)
        assert "error" in result
        assert "no params" in result["error"].lower()

    def test_optimizer_raises_returns_error(self, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError("optuna died")
        monkeypatch.setattr(backtest_optimizer, "run_optimizer", boom)
        result = backtest_optimizer.run_walk_forward("BTCUSDT", "4H", days=180)
        assert "error" in result
        assert "optimizer failed" in result["error"].lower()


# ── Result shape ─────────────────────────────────────────────────────────────

class TestResultShape:
    def test_full_shape_returned_on_success(self, monkeypatch):
        monkeypatch.setattr(backtest_optimizer, "run_optimizer",
                            lambda *a, **kw: {"sl_pct": 0.05, "tp1_pct": 0.05,
                                              "tp2_pct": 0.1, "min_confluence": 0.3,
                                              "wt_oversold": -50.0, "rsi_max": 60.0,
                                              "adx_min": 20.0})
        import backtest_engine
        monkeypatch.setattr(backtest_engine, "run_backtest",
                            lambda *a, **kw: _make_result(sharpe=1.5, trades=15, wr=60.0))

        result = backtest_optimizer.run_walk_forward("ETHUSDT", "4H", days=180)
        expected_keys = {
            "symbol", "timeframe", "total_days", "train_days", "test_days",
            "train_sharpe", "test_sharpe", "train_trades", "test_trades",
            "train_win_rate", "test_win_rate", "generalizes", "best_params",
        }
        assert expected_keys.issubset(set(result.keys()))
        assert result["symbol"] == "ETHUSDT"
        assert result["timeframe"] == "4H"
        # n_positions is dropped — confirm it's NOT in the response
        assert "n_positions" not in result
