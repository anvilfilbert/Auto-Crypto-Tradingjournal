"""
backtest_optimizer.py — Bayesian optimizer for backtester parameters using Optuna.
Maximises Sharpe ratio across the parameter search space defined in BacktestParams.
"""
import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Optional
import time as _time

import optuna

optuna.logging.set_verbosity(optuna.logging.WARNING)

from backtest_engine import BacktestParams, run_backtest

_logger = logging.getLogger(__name__)

# ── Async job registry ─────────────────────────────────────────────────────────
_jobs: dict = {}
_jobs_lock = threading.Lock()
_JOB_TTL = 3600  # evict jobs older than 1 hour


@dataclass
class _OptJob:
    job_id:  str
    symbol:  str
    status:  str = "running"  # running | complete | error
    result:  Optional[dict] = None
    error:   Optional[str] = None
    started: float = field(default_factory=_time.time)


def _evict_old_jobs() -> None:
    # Caller must hold _jobs_lock
    cutoff = _time.time() - _JOB_TTL
    stale = [jid for jid, j in _jobs.items() if j.started < cutoff]
    for jid in stale:
        del _jobs[jid]


def start_optimizer_job(symbol: str, timeframe: str = "4H",
                        days: int = 180, n_trials: int = 50) -> str:
    """Start an async optimizer run in a daemon thread. Returns job_id immediately."""
    job_id = str(uuid.uuid4())
    job = _OptJob(job_id=job_id, symbol=symbol)
    with _jobs_lock:
        _evict_old_jobs()
        _jobs[job_id] = job

    def _run():
        try:
            result = run_optimizer(symbol, timeframe, days, n_trials)
            with _jobs_lock:
                if job_id in _jobs:
                    _jobs[job_id].status = "complete"
                    _jobs[job_id].result = result
        except Exception:
            _logger.exception("Optimizer job %s failed", job_id)
            with _jobs_lock:
                if job_id in _jobs:
                    _jobs[job_id].status = "error"
                    _jobs[job_id].error = "Optimizer failed — check server logs"

    threading.Thread(target=_run, daemon=True, name=f"optuna-{job_id[:8]}").start()
    return job_id


def get_job_status(job_id: str) -> Optional[dict]:
    """Return job status dict or None if job_id not found."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return None
    return {"job_id": job.job_id, "symbol": job.symbol,
            "status": job.status, "result": job.result, "error": job.error}


def _objective(trial: optuna.Trial, symbol: str, timeframe: str, days: int,
               end_offset_days: int = 0) -> float:
    """Optuna objective: sample params -> run backtest -> return Sharpe (or penalty)."""
    params = BacktestParams(
        wt_oversold    = trial.suggest_float("wt_oversold",    -80,   -40),
        rsi_max        = trial.suggest_float("rsi_max",         45,    70),
        adx_min        = trial.suggest_float("adx_min",         10,    25),
        min_confluence = trial.suggest_float("min_confluence",  0.25,  0.55),
        sl_pct         = trial.suggest_float("sl_pct",         0.05,  0.20),
        tp1_pct        = trial.suggest_float("tp1_pct",        0.03,  0.10),
        tp2_pct        = trial.suggest_float("tp2_pct",        0.08,  0.20),
    )
    result = run_backtest(symbol, timeframe, days, params,
                          end_offset_days=end_offset_days)
    if result.total_trades < 10:
        return -999.0  # penalise strategies that barely trade
    return result.sharpe


def run_walk_forward(symbol: str, timeframe: str = "4H",
                     n_trials: int = 50, days: int = 180) -> dict:
    """
    Candle-based walk-forward test (rewritten 2026-05-24).

    Splits a fixed price-history window (default 180d) chronologically 70/30:
      - Train window: older 70% of `days` (end_offset_days = test portion)
      - Test  window: most recent 30% (end_offset_days = 0)

    Windows are guaranteed non-overlapping. Works for any symbol with sufficient
    price history — does NOT depend on journal position count.

    Returns train Sharpe + test Sharpe + generalization verdict so the operator
    can judge whether an optimization result is real or curve-fit.
    """
    from backtest_engine import run_backtest, BacktestParams

    if days < 30:
        return {"error": f"days={days} too small — need at least 30 for a meaningful split"}

    train_days = max(14, round(days * 0.70))
    test_days  = max(7,  days - train_days)

    # Phase 1: optimize on training window (ends `test_days` ago — no overlap)
    try:
        train_params = run_optimizer(symbol, timeframe,
                                     days=train_days, n_trials=n_trials,
                                     end_offset_days=test_days)
    except Exception as e:
        return {"error": f"Optimizer failed: {e}"}

    if not train_params:
        return {"error": "Optimizer returned no params — likely no valid trades in train window"}

    test_p = BacktestParams(**{k: v for k, v in train_params.items()
                               if k in BacktestParams.__dataclass_fields__})

    # Phase 2: test best params on out-of-sample window (most recent test_days)
    try:
        test_result = run_backtest(symbol, timeframe,
                                   days=test_days, params=test_p,
                                   end_offset_days=0)
    except Exception as e:
        return {"error": f"Test backtest failed: {e}"}

    # Also run the same params on the train window for comparison
    try:
        train_result = run_backtest(symbol, timeframe,
                                    days=train_days, params=test_p,
                                    end_offset_days=test_days)
        train_sharpe   = round(train_result.sharpe, 3)
        train_trades   = train_result.total_trades
        train_win_rate = round(train_result.win_rate, 1)
    except Exception:
        train_sharpe   = None
        train_trades   = 0
        train_win_rate = None

    test_sharpe = round(test_result.sharpe, 3) if test_result else None
    # Generalizes if OOS is positive AND retains at least half of in-sample Sharpe
    generalizes = (test_sharpe is not None and test_sharpe > 0
                   and train_sharpe is not None and train_sharpe > 0
                   and test_sharpe > train_sharpe * 0.5)

    return {
        "symbol":         symbol,
        "timeframe":      timeframe,
        "total_days":     days,
        "train_days":     train_days,
        "test_days":      test_days,
        "train_sharpe":   train_sharpe,
        "test_sharpe":    test_sharpe,
        "train_trades":   train_trades,
        "test_trades":    test_result.total_trades if test_result else 0,
        "train_win_rate": train_win_rate,
        "test_win_rate":  round(test_result.win_rate, 1) if test_result else None,
        "generalizes":    generalizes,
        "best_params":    train_params,
    }


def start_walk_forward_job(symbol: str, timeframe: str = "4H",
                           n_trials: int = 50, days: int = 180) -> str:
    """Start an async walk-forward job in a daemon thread. Returns job_id immediately."""
    job_id = str(uuid.uuid4())
    job = _OptJob(job_id=job_id, symbol=symbol)
    with _jobs_lock:
        _evict_old_jobs()
        _jobs[job_id] = job

    def _run():
        try:
            result = run_walk_forward(symbol, timeframe, n_trials, days)
            with _jobs_lock:
                if job_id in _jobs:
                    _jobs[job_id].status = "complete"
                    _jobs[job_id].result = result
        except Exception:
            _logger.exception("Walk-forward job %s failed", job_id)
            with _jobs_lock:
                if job_id in _jobs:
                    _jobs[job_id].status = "error"
                    _jobs[job_id].error = "Walk-forward failed — check server logs"

    threading.Thread(target=_run, daemon=True, name=f"wf-{job_id[:8]}").start()
    return job_id


def run_optimizer(symbol: str = "BTCUSDT", timeframe: str = "4H",
                  days: int = 180, n_trials: int = 100,
                  end_offset_days: int = 0) -> dict:
    """
    Run Optuna Bayesian optimization. Returns best params dict.
    Typical runtime on Pi: 5-15 min with n_trials=100.
    end_offset_days: passed through to run_backtest to shift the window backward.
    """
    import time as _time_mod
    t0 = _time_mod.time()
    study = optuna.create_study(direction="maximize")
    study.optimize(
        lambda t: _objective(t, symbol, timeframe, days,
                             end_offset_days=end_offset_days),
        n_trials=n_trials,
        n_jobs=1,
    )
    completed = [t for t in study.trials if t.state.name == "COMPLETE"]
    if not completed:
        return {}
    best_params = study.best_params
    best_sharpe = study.best_value if study.best_value > -999.0 else None
    duration_sec = round(_time_mod.time() - t0, 1)

    # Save to optimizer_runs history
    try:
        import json as _json
        from database import db_conn as _db_conn
        with _db_conn() as _conn:
            _conn.execute("""
                INSERT INTO optimizer_runs (symbol, timeframe, days, n_trials, best_sharpe, best_params, duration_sec)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol, timeframe, days, n_trials,
                round(best_sharpe, 4) if best_sharpe is not None else None,
                _json.dumps(best_params),
                duration_sec,
            ))
            _conn.commit()
    except Exception:
        pass  # non-fatal — job result is in-memory regardless

    return best_params
