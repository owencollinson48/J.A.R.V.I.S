"""Research selection uses past training data and never applies a candidate."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import random

import pytest

from jarvis.trading import research


def candles(count=30):
    start = datetime(2026, 10, 1, 13, 0, tzinfo=timezone.utc)
    return [{"time": (start + timedelta(minutes=5 * i)).isoformat(), "open": "20000", "high": "20010",
             "low": "19990", "close": "20001", "volume": "100", "closed": True} for i in range(count)]


@pytest.fixture
def replay(monkeypatch):
    calls = []
    def fixture(data, config, *, correlated=None, initial_equity="100000", start=None, end=None, strategy_version="V1"):
        calls.append({"data": deepcopy(data), "config": deepcopy(config), "correlated": deepcopy(correlated),
                      "initial_equity": initial_equity, "start": start, "end": end, "strategy_version": strategy_version})
        # V2 deliberately wins training and fails the holdout; V1 has the best
        # held-out result. Candidate selection must still choose training V2.
        value = "200" if config.get("rr") == 3 and start is None else "-50" if config.get("rr") == 3 else "10"
        trade = {"net_pnl": value, "status": "closed", "exit_time": data[-1]["time"]}
        return {"trades": [trade] * 4, "rejected": [],
                "metrics": {"net_profit": str(Decimal(value) * 4), "max_drawdown": "100"},
                "coverage": {"first": data[0]["time"], "last": data[-1]["time"]}}
    monkeypatch.setattr(research, "run_backtest", fixture)
    return calls


def versions():
    return [{"id": "V1", "config": {"rr": 2.5, "risk_pct": .5, "spread": 1, "symbol": "NQ"}},
            {"id": "V2", "config": {"rr": 3, "risk_pct": 5, "spread": 0}}]


def test_candidate_selected_on_training_only_and_never_applied(replay):
    report = research.compare_versions(candles(), versions())
    assert report["candidate_id"] == "V2"
    assert report["requires_approval"] is True and report["active_strategy_changed"] is False
    second = report["versions"][1]
    assert second["train"]["metrics"]["net_profit"] == "800"
    assert second["out_of_sample"]["metrics"]["net_profit"] == "-200"
    assert any("possible overfitting" in warning for warning in report["warnings"])
    assert any("multiple-comparison" in warning for warning in report["warnings"])
    assert all("config" not in report for _ in [0]), "No active strategy config is returned as an applied replacement"


def test_identical_data_cost_risk_and_contract_assumptions_are_held_fixed(replay):
    data, configs = candles(), versions()
    before = deepcopy((data, configs))
    report = research.compare_versions(data, configs, initial_equity="50000")
    assert all(item["config"]["risk_pct"] == .5 and item["config"]["spread"] == 1 for item in replay)
    assert all(item["config"]["symbol"] == "NQ" and item["initial_equity"] == "50000" for item in replay)
    assert len(replay) == 12
    assert (data, configs) == before
    assert report["controlled_assumptions"]["risk_pct"] == .5
    assert any("held risk_pct, spread" in warning or "held spread, risk_pct" in warning for warning in report["warnings"])
    assert report["sensitivity"][1]["parameter_changes"] == {"rr": {"baseline": 2.5, "candidate": 3}}


def test_chronological_holdout_and_walk_forward_include_only_prefix_warmup(replay):
    data = candles(30)
    reference = candles(40)
    report = research.compare_versions(data, {"V1": {"rr": 2.5}}, correlated=reference)
    full, training, held_out, *folds = replay
    assert training["data"] == data[:21] and training["end"] == data[20]["time"]
    assert held_out["start"] == data[21]["time"]
    assert len(folds) == 3
    for call, (start, stop) in zip(folds, ((12, 18), (18, 24), (24, 30))):
        assert call["data"] == data[:stop]
        assert call["start"] == data[start]["time"] and call["end"] == data[stop - 1]["time"]
        assert all(bar["time"] <= call["data"][-1]["time"] for bar in call["correlated"])
    assert report["split"]["training_end"] < report["split"]["out_of_sample_start"]


def test_monte_carlo_is_seeded_local_and_uses_actual_recorded_outcomes(replay):
    state = random.getstate()
    first = research.compare_versions(candles(), versions())
    second = research.compare_versions(candles(), versions())
    assert random.getstate() == state and first["monte_carlo"] == second["monte_carlo"]
    experiment = first["monte_carlo"]
    assert experiment["simulations"] == 200
    result = experiment["versions"][0]
    assert result["id"] == "V2" and result["observed_trades"] == 4
    assert result["terminal_pnl"] == {"p05": "800", "median": "800", "p95": "800"}
    assert result["max_drawdown"] == {"p05": "0", "median": "0", "p95": "0"}
    assert result["loss_probability"] == "0"
    assert any("not a guarantee" in value for value in experiment["assumptions"])
    json.dumps(first, allow_nan=False)


def test_empty_data_reports_missing_evidence_without_invoking_a_backtest(replay):
    report = research.compare_versions([], versions())
    assert report["status"] == "insufficient_data"
    assert report["candidate_id"] is None and report["versions"] == [] and replay == []
    assert report["monte_carlo"]["versions"] == []


def test_no_closed_training_trades_cannot_create_a_candidate(monkeypatch):
    monkeypatch.setattr(research, "run_backtest", lambda *args, **kwargs:
                        {"trades": [], "rejected": [], "metrics": {"net_profit": "0", "max_drawdown": "0"}})
    report = research.compare_versions(candles(), versions())
    assert report["candidate_id"] is None and report["monte_carlo"]["versions"] == []
    assert any("No version produced" in value for value in report["warnings"])


@pytest.mark.parametrize("given", [[], {}, [dict(id="V1", config={})] * 7,
                                    [{"id": "same", "config": {}}, {"id": "same", "config": {}}],
                                    [{"id": "", "config": {}}], [{"id": "V1", "config": []}]])
def test_version_bounds_and_identity_are_explicit(replay, given):
    with pytest.raises(ValueError):
        research.compare_versions(candles(), given)
    assert replay == []


def test_oversize_input_is_rejected_without_hidden_downsampling(replay):
    with pytest.raises(ValueError, match="10,000"):
        research.compare_versions(candles(10001), versions())
    assert replay == []


def test_duplicate_or_naive_candle_times_do_not_enter_research(replay):
    data = candles()
    with pytest.raises(ValueError, match="duplicate"):
        research.compare_versions(data + [data[0]], versions())
    data[0]["time"] = "2026-10-01T13:00:00"
    with pytest.raises(ValueError, match="explicit timezone"):
        research.compare_versions(data, versions())
    assert replay == []


def test_uncompleted_candles_are_not_passed_to_any_strategy_test(replay):
    data = candles()
    data[-1]["closed"] = False
    research.compare_versions(data, versions())
    assert all(all(bar.get("closed", True) is True for bar in call["data"]) for call in replay)


def test_missing_costs_in_baseline_cannot_be_independently_changed_by_candidate(replay):
    report = research.compare_versions(candles(), [{"id": "V1", "config": {"rr": 2}},
                                                 {"id": "V2", "config": {"rr": 3, "spread": 999}}])
    assert all("spread" not in call["config"] for call in replay)
    assert any("held spread" in warning for warning in report["warnings"])


def test_real_backtest_integration_reports_absent_setups_without_fabricated_profit():
    # Repeated inside bars never sweep the actual observed high/low. Exercise
    # real strategy/risk/replay code, rather than fixture model decisions.
    report = research.compare_versions(candles(), [{"id": "V1", "config": {"rr": 2.5}}])
    assert report["coverage"]["observed_candles"] == 30 and report["candidate_id"] is None
    assert report["versions"][0]["full"]["trade_count"] == 0
    assert report["versions"][0]["full"]["metrics"]["net_profit"] == "0"
    assert report["versions"][0]["out_of_sample"]["metrics"]["win_rate"] is None
