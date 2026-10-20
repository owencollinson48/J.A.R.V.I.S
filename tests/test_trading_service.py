"""Persistent trading boundaries exercised with real rules, sizing and paper fills.

Only native Windows observation is adapted. A few execution tests replace the
pure signal evaluator with a named synthetic setup so persistence and risk can
be checked independently from the strategy's separately tested pattern rules.
These tests do not connect to TradingView or use actual market data.
"""
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from jarvis.store import Store
from jarvis.trading import connection, service as service_module
from jarvis.trading.service import TradingService


BASE = datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc)  # NY 10:00 Monday.


def candle(when=BASE, *, opening="1000", high="1002", low="998", close="1000", **extra):
    return {"time": when.isoformat(), "open": opening, "high": high, "low": low,
            "close": close, "volume": "10", "closed": True, **extra}


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = Store(tmp_path / "trading.db")
    clock = SimpleNamespace(now=BASE)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.now.astimezone(tz) if tz else clock.now.replace(tzinfo=None)

    monkeypatch.setattr(service_module, "datetime", Clock)
    inputs = []
    observation = {"window": {"id": 7, "pid": 44, "process": "TradingView.exe", "title": "TradingView"},
                   "elements": []}
    wrapper = SimpleNamespace(descendants=lambda depth: [])
    desktop = SimpleNamespace(
        inspect=lambda identity, cancel=None: copy.deepcopy(observation),
        window=lambda identity: (observation["window"], wrapper),
        screenshot=lambda identity, cancel=None: {"image_id": "a" * 32},
        act=lambda *args, **kwargs: inputs.append((args, kwargs)),
    )
    applications = SimpleNamespace(launch=lambda *args: {"verified": True, "window_id": 7})
    trading = TradingService(store, applications, desktop)
    result = SimpleNamespace(service=trading, store=store, clock=clock,
                             applications=applications, desktop=desktop,
                             observation=observation, inputs=inputs)
    yield result
    store.db.close()


def ingest(env, rows=None, *, symbol="NQ", kind="live", timeframe=5):
    return env.service.ingest({"symbol": symbol, "kind": kind,
                               "source": "Synthetic integration-test fixture; not market data",
                               "timeframe_minutes": timeframe,
                               "candles": rows or [candle(env.clock.now)]})


def independent(env):
    env.service.update_config({"execution_backend": "independent_paper"})


def qualifying_signal(monkeypatch, *, identity="synthetic-nq-setup", entry="1000", stop="990", target="1025"):
    def evaluate(bars, config, *, correlated=None):
        return {"setup_id": identity, "time": bars[-1]["time"], "symbol": "NQ",
                "passed": True, "direction": "long", "entry": entry,
                "stop_loss": stop, "take_profit": target, "order_type": "market",
                "market_session": "New York", "rating": 100,
                "conditions": {"sweep": True, "bos": True, "continuation": True, "session": True},
                "evidence": {"fixture": "Controlled synthetic signal for service invariants"}}
    monkeypatch.setattr(service_module, "evaluate", evaluate)


def start_pending(env, monkeypatch):
    independent(env)
    qualifying_signal(monkeypatch)
    ingest(env)
    env.service.control("mode", "PAPER")
    env.service.tick()
    state = env.service._state()
    assert len(state["orders"]) == 1
    assert state["orders"][0]["status"] == "pending"
    assert not state["positions"], "Submission alone must never be reported as a fill"
    return state["orders"][0]


def advance(env, minutes=5, **prices):
    env.clock.now += timedelta(minutes=minutes)
    ingest(env, [candle(env.clock.now, **prices)])
    env.service.tick()


def test_default_native_account_has_no_fabricated_connection_or_account(env):
    snapshot = env.service.snapshot()
    assert snapshot["mode"] == "OFF"
    assert snapshot["execution_backend"] == "tradingview_desktop"
    assert snapshot["account"] is None
    assert snapshot["connection"]["tradingview"]["verified"] is False
    assert snapshot["connection"]["tradingview"]["orders_enabled"] is False
    assert snapshot["metrics"]["total_trades"] == 0
    assert snapshot["metrics"]["win_rate"] is None
    assert snapshot["metrics"]["profit_factor"] is None


def test_native_title_and_menu_paper_option_are_not_selected_account_proof(env, monkeypatch):
    monkeypatch.setattr(connection.platform, "system", lambda: "Windows")
    env.observation["elements"] = [{"name": "Paper Trading", "type": "MenuItem"},
                                   {"name": "Balance $100000", "type": "Text"},
                                   {"name": "Equity $100000", "type": "Text"},
                                   {"name": "Available funds $100000", "type": "Text"}]
    status = env.service.control("connect")
    assert not status["verified"] and not status["orders_enabled"]
    with pytest.raises(ValueError, match="verified"):
        env.service.control("mode", "PAPER")
    assert env.service._state()["mode"] == "OFF" and not env.inputs


def test_verified_native_account_still_requires_validated_order_transport(env, monkeypatch):
    monkeypatch.setattr(connection.platform, "system", lambda: "Windows")
    env.observation["elements"] = [{"name": "Account", "type": "ComboBox", "text": "Paper Trading"},
                                   {"name": "Balance $100000", "type": "Text"},
                                   {"name": "Equity $100000", "type": "Text"},
                                   {"name": "Available funds $100000", "type": "Text"}]
    status = env.service.control("connect")
    assert status["verified"] and status["account"]["equity"] == "100000"
    assert status["orders_enabled"] is False
    with pytest.raises(ValueError, match="disabled"):
        env.service.control("mode", "PAPER")
    assert not env.inputs and not env.service._state()["orders"]


def test_browser_mentioning_tradingview_cannot_be_native_account(env, monkeypatch):
    monkeypatch.setattr(connection.platform, "system", lambda: "Windows")
    env.observation["window"]["process"] = "chrome.exe"
    status = env.service.control("connect")
    assert not status["connected"] and not status["verified"]
    assert "not the installed" in status["reason"] and not env.inputs


def test_missing_historical_and_es_only_data_cannot_start_paper_entries(env):
    independent(env)
    ingest(env, kind="historical")
    ingest(env, symbol="ES")
    with pytest.raises(ValueError, match="historical imports"):
        env.service.control("mode", "PAPER")
    env.service.tick()
    assert env.service._state()["mode"] == "OFF"
    assert not env.service._state()["orders"]


def test_stale_live_data_suspends_active_paper_and_never_fills_pending_order(env, monkeypatch):
    start_pending(env, monkeypatch)
    env.clock.now += timedelta(hours=1)
    env.service.tick()
    state = env.service._state()
    assert state["mode"] == "OFF" and "stale" in state["suspension_reason"]
    assert state["orders"][0]["status"] == "pending" and not state["positions"]


def test_identical_import_is_idempotent_and_conflicting_batch_is_atomic(env):
    original = candle()
    ingest(env, [original], kind="historical")
    ingest(env, [original], kind="historical")
    assert len(env.service.bars()) == 1
    earlier = candle(BASE - timedelta(minutes=5))
    changed = {**original, "close": "1001"}
    with pytest.raises(ValueError, match="existing history was preserved"):
        ingest(env, [earlier, changed], kind="historical")
    assert len(env.service.bars()) == 1
    assert env.service.bars()[0]["close"] == "1000"


@pytest.mark.parametrize("row", [candle(BASE + timedelta(minutes=5)), candle(closed=False)])
def test_future_and_incomplete_candles_are_rejected_without_writes(env, row):
    with pytest.raises(ValueError):
        ingest(env, [row])
    assert env.store.rows("SELECT * FROM trading_bars") == []


def test_historical_and_live_records_remain_separate(env):
    ingest(env, [candle(close="1001")], kind="historical")
    ingest(env, [candle(close="999")], kind="live")
    assert env.service.bars(kind="historical")[0]["close"] == "1001"
    assert env.service.bars(kind="live")[0]["close"] == "999"
    assert {row["kind"] for row in env.service.snapshot()["data"]} == {"historical", "live"}


def test_monitor_uses_real_strategy_records_rejection_and_never_trades_es(env):
    independent(env)
    ingest(env)
    ingest(env, symbol="ES")
    env.service.control("mode", "MONITOR")
    env.service.tick()
    snapshot = env.service.snapshot()
    assert not snapshot["orders"] and not snapshot["positions"]
    assert len(snapshot["setups"]) == 1 and snapshot["setups"][0]["status"] == "rejected"
    assert snapshot["setups"][0]["symbol"] == "NQ"
    assert all(not setup["passed"] for setup in snapshot["setups"])


def test_simulated_submission_requires_next_bar_and_deduplicates_setup(env, monkeypatch):
    order = start_pending(env, monkeypatch)
    assert Decimal(order["quantity"]) == 2  # $500 budget / $200 integer-contract risk.
    env.service.tick()
    env.service.tick()
    assert len(env.service._state()["orders"]) == 1
    advance(env)
    state = env.service._state()
    assert len(state["orders"]) == 1 and state["orders"][0]["status"] == "filled"
    assert len(state["positions"]) == 1
    assert state["positions"][0]["entry_time"] > order["created_at"]
    assert env.service.journal()["trades"] == []
    env.service.tick()
    assert env.service._state() == state


@pytest.mark.parametrize("initial", ["rejected", "monitor"])
def test_same_setup_can_progress_to_first_submission_without_being_lost_to_dedupe(env, monkeypatch, initial):
    independent(env)
    qualifying_signal(monkeypatch)
    qualified_evaluate = service_module.evaluate

    def progressing(bars, config, *, correlated=None):
        result = qualified_evaluate(bars, config, correlated=correlated)
        if initial == "rejected" and len(bars) == 1:
            result["passed"] = False
            result["conditions"]["continuation"] = False
        return result

    monkeypatch.setattr(service_module, "evaluate", progressing)
    ingest(env)
    env.service.control("mode", "MONITOR" if initial == "monitor" else "PAPER")
    env.service.tick()
    assert env.service._state()["orders"] == []
    env.service.control("mode", "PAPER")
    advance(env)
    state = env.service._state()
    assert len(state["orders"]) == 1 and state["orders"][0]["status"] == "pending"
    assert len(env.store.rows("SELECT * FROM trading_setups")) == 1
    advance(env)
    assert len(env.service._state()["orders"]) == 1


def test_full_size_nq_exposure_limit_does_not_invent_fractional_contracts(env, monkeypatch):
    independent(env)
    qualifying_signal(monkeypatch, entry="20000", stop="19990", target="20025")
    ingest(env, [candle(opening="20000", high="20002", low="19998", close="20000")])
    env.service.control("mode", "PAPER")
    env.service.tick()
    state = env.service._state()
    assert not state["orders"] and not state["positions"]
    setup = env.service.snapshot()["setups"][0]
    assert setup["status"] == "risk_rejected" and setup["risk"]["quantity"] == "0"
    assert any("exposure" in reason for reason in setup["risk"]["reasons"])


def test_service_does_not_average_into_an_existing_losing_nq_position(env, monkeypatch):
    start_pending(env, monkeypatch)
    advance(env, low="994", close="995")
    qualifying_signal(monkeypatch, identity="different-synthetic-setup")
    advance(env, opening="995", high="1001", low="994", close="995")
    state = env.service._state()
    assert len(state["positions"]) == 1 and len(state["orders"]) == 1
    setups = env.service.snapshot()["setups"]
    rejected = next(setup for setup in setups if setup["setup_id"].endswith("different-synthetic-setup"))
    assert rejected["status"] == "risk_rejected"
    assert any("losing NQ position" in reason for reason in rejected["risk"]["reasons"])


def test_off_prevents_new_entries_while_existing_paper_bracket_is_managed(env, monkeypatch):
    start_pending(env, monkeypatch)
    advance(env)
    env.service.control("mode", "OFF")
    advance(env, high="1026", low="999", close="1025")
    state = env.service._state()
    assert state["mode"] == "OFF" and not state["positions"]
    assert len(state["orders"]) == 1
    journal = env.service.journal()
    assert len(journal["trades"]) == 1
    assert journal["trades"][0]["exit_reason"] == "take_profit"
    assert Decimal(journal["metrics"]["net_profit"]) == 1000
    assert journal["dataset"] == "paper"


def test_emergency_is_persistent_cancels_pending_and_requires_explicit_reset(env, monkeypatch):
    start_pending(env, monkeypatch)
    stopped = env.service.control("emergency")
    assert stopped["mode"] == "OFF" and stopped["emergency_stop"] is True
    assert stopped["orders"][0]["status"] == "cancelled"
    restarted = TradingService(env.store, env.applications, env.desktop)
    restarted.recover()
    assert restarted.snapshot()["emergency_stop"] is True
    for mode in ("MONITOR", "PAPER"):
        with pytest.raises(ValueError, match="explicitly reset"):
            restarted.control("mode", mode)
    reset = restarted.control("reset_emergency")
    assert reset["mode"] == "OFF" and reset["emergency_stop"] is False
    env.clock.now += timedelta(minutes=5)
    ingest(env)
    restarted.tick()
    assert restarted._state()["orders"][0]["status"] == "cancelled"
    assert restarted._state()["positions"] == []


def test_restart_forces_off_without_losing_pending_order_identity(env, monkeypatch):
    order = start_pending(env, monkeypatch)
    restarted = TradingService(env.store, env.applications, env.desktop)
    restarted.recover()
    snapshot = restarted.snapshot()
    assert snapshot["mode"] == "OFF" and "Restart" in snapshot["suspension_reason"]
    assert snapshot["orders"][0]["id"] == order["id"]


@pytest.mark.parametrize("stop", ["off", "restart"])
def test_off_or_restart_prevents_pending_entry_from_creating_new_position(env, monkeypatch, stop):
    start_pending(env, monkeypatch)
    if stop == "off":
        env.service.control("mode", "OFF")
    else:
        env.service = TradingService(env.store, env.applications, env.desktop)
        env.service.recover()
    advance(env)
    state = env.service._state()
    assert state["mode"] == "OFF" and state["positions"] == []
    assert all(order["status"] != "filled" for order in state["orders"])
    assert env.service.journal()["trades"] == []


def test_emergency_remains_latched_if_optional_close_has_no_current_price(env, monkeypatch):
    start_pending(env, monkeypatch)
    advance(env)
    env.clock.now += timedelta(hours=1)
    with pytest.raises(ValueError, match="fresh NQ price"):
        env.service.control("emergency", close_positions=True)
    state = env.service._state()
    assert state["mode"] == "OFF" and state["emergency_stop"] is True
    assert len(state["positions"]) == 1


def test_strategy_proposal_never_changes_active_until_explicit_approval(env):
    original = env.service.snapshot()["active_strategy_id"]
    proposal = env.service.update_config({"rr": 3})
    version = proposal["proposed_strategy"]["id"]
    assert proposal["active_unchanged"] and env.service.config()["rr"] == 2.5
    for false_approval in (False, None, "yes", 1):
        with pytest.raises(ValueError, match="Explicit approval"):
            env.service.activate(version, approved=false_approval)
    assert env.service.snapshot()["active_strategy_id"] == original
    activated = env.service.activate(version, approved=True)
    assert activated["active_strategy_id"] == version and env.service.config()["rr"] == 3


def test_active_strategy_cannot_change_with_open_exposure(env, monkeypatch):
    start_pending(env, monkeypatch)
    advance(env)
    version = env.service.propose("Research 3R", {"rr": 3})["id"]
    env.service.control("mode", "OFF")
    with pytest.raises(ValueError, match="open positions"):
        env.service.activate(version, approved=True)


def test_backtest_never_creates_paper_orders_or_fabricated_performance(env):
    rows = [candle(BASE - timedelta(minutes=5 * (9-index))) for index in range(10)]
    ingest(env, rows, kind="historical")
    before = env.service._state()
    report = env.service.backtest({})
    assert report["dataset"] == "backtest" and report["coverage"]["available_candles"] == 10
    assert report["trades"] == [] and report["metrics"]["total_trades"] == 0
    assert report["metrics"]["win_rate"] is None
    assert env.service._state() == before
    assert env.service.journal("paper")["trades"] == []
    assert env.service.run_report(report["id"]) == report
    assert env.store.rows("SELECT * FROM trading_runs")


def test_backtest_journal_cannot_contaminate_paper_metrics_and_source_trades_are_immutable(env):
    ingest(env, kind="historical")
    report = env.service.backtest({})
    trade = {"trade_id": "backtest-test-1", "symbol": "NQ", "status": "closed",
             "entry_time": (BASE-timedelta(minutes=5)).isoformat(), "exit_time": BASE.isoformat(),
             "net_pnl": "-250", "gross_pnl": "-240", "costs": "10"}
    original = copy.deepcopy(trade)
    env.service._save_trades([trade], "backtest", report["id"])
    assert trade == original
    assert env.service.journal("paper")["metrics"]["total_trades"] == 0
    backtest = env.service.journal("backtest", run_id=report["id"])
    assert backtest["metrics"]["net_profit"] == "-250" and backtest["dataset"] == "backtest"
    assert backtest["trades"][0]["dataset"] == "backtest"
    assert env.service.snapshot()["metrics"]["total_trades"] == 0


def test_repeated_backtests_keep_each_runs_realized_trade_records_separate(env, monkeypatch):
    from jarvis.trading import strategy
    qualifying_signal(monkeypatch)
    monkeypatch.setattr(strategy, "evaluate", service_module.evaluate)
    env.clock.now += timedelta(minutes=10)
    ingest(env, [candle(BASE), candle(BASE+timedelta(minutes=5)),
                 candle(BASE+timedelta(minutes=10), high="1026", low="999", close="1025")], kind="historical")
    before = env.service._state()
    reports = [env.service.backtest({}) for _ in range(2)]
    assert reports[0]["id"] != reports[1]["id"]
    assert env.service._state() == before
    for report in reports:
        assert len(report["trades"]) == 1
        journal = env.service.journal("backtest", run_id=report["id"])
        assert len(journal["trades"]) == 1
        assert Decimal(journal["metrics"]["net_profit"]) == 1000
    assert len(env.service.journal("backtest")["trades"]) == 2
    assert env.service.journal("paper")["trades"] == []


def test_unknown_volume_import_remains_usable_by_real_strategy(env):
    independent(env)
    row = candle()
    row.pop("volume")
    ingest(env, [row])
    env.service.control("mode", "MONITOR")
    env.service.tick()
    assert len(env.service.snapshot()["setups"]) == 1
    assert env.service.bars(kind="live")[0]["volume"] is None


def test_requested_version_reads_its_own_timeframe_not_active_version(env):
    proposal = env.service.propose("One minute experiment", {"timeframe_minutes": 1})
    rows = [candle(BASE - timedelta(minutes=9-index)) for index in range(10)]
    ingest(env, rows, kind="historical", timeframe=1)
    report = env.service.backtest({"strategy_id": proposal["id"]})
    assert report["strategy_id"] == proposal["id"]
    assert report["coverage"]["available_candles"] == 10
    assert env.service.config()["timeframe_minutes"] == 5


def test_batched_contiguous_live_candles_reconcile_every_bar_in_order(env, monkeypatch):
    start_pending(env, monkeypatch)
    env.clock.now += timedelta(minutes=10)
    ingest(env, [candle(BASE + timedelta(minutes=5)),
                 candle(BASE + timedelta(minutes=10), high="1026", low="999", close="1025")])
    env.service.tick()
    state = env.service._state()
    assert state["mode"] == "PAPER" and not state["positions"]
    assert len(state["orders"]) == 1 and state["orders"][0]["status"] == "filled"
    trades = env.service.journal()["trades"]
    assert len(trades) == 1 and trades[0]["entry_time"] == (BASE+timedelta(minutes=5)).isoformat()
    assert trades[0]["exit_time"] == (BASE+timedelta(minutes=10)).isoformat()


def test_tick_order_and_setup_commit_together_on_persistence_failure(env, monkeypatch):
    independent(env)
    qualifying_signal(monkeypatch)
    ingest(env)
    env.service.control("mode", "PAPER")
    original_write = env.service._write

    def unavailable(state):
        raise OSError("Synthetic persistence interruption before state commit")

    monkeypatch.setattr(env.service, "_write", unavailable)
    with pytest.raises(OSError, match="Synthetic persistence interruption"):
        env.service.tick()
    assert env.store.rows("SELECT * FROM trading_setups") == [], "A failed commit must not consume the setup ID"
    assert env.service._state()["orders"] == []
    monkeypatch.setattr(env.service, "_write", original_write)
    env.service.tick()
    assert len(env.service._state()["orders"]) == 1
    assert len(env.store.rows("SELECT * FROM trading_setups")) == 1


def test_concurrent_off_during_slow_evaluation_prevents_any_order_or_submission_audit(env, monkeypatch):
    independent(env)
    qualifying_signal(monkeypatch)
    evaluate = service_module.evaluate
    entered = threading.Event()
    release = threading.Event()

    def slow_evaluate(*args, **kwargs):
        entered.set()
        assert release.wait(5), "Test did not release the synthetic slow evaluation"
        return evaluate(*args, **kwargs)

    monkeypatch.setattr(service_module, "evaluate", slow_evaluate)
    ingest(env)
    env.service.control("mode", "PAPER")
    with ThreadPoolExecutor(max_workers=2) as workers:
        tick = workers.submit(env.service.tick)
        try:
            assert entered.wait(2), "The synthetic qualifying evaluation must be in flight"
            stop = workers.submit(env.service.control, "mode", "OFF")
            assert env.service.entry_stop.wait(2), "OFF must signal cancellation before waiting for the database lock"
        finally:
            release.set()
        tick.result(timeout=3)
        stop.result(timeout=3)
    state = env.service._state()
    assert state["mode"] == "OFF" and state["orders"] == [] and state["positions"] == []
    assert env.store.rows("SELECT * FROM trading_setups") == []
    assert env.store.rows("SELECT * FROM trading_trades") == []
    assert env.store.rows("SELECT * FROM trading_audit WHERE event='simulator_order_submitted'") == []


def test_generic_workflow_created_before_stop_cannot_restart_paper_entries(env):
    from jarvis.execution import RunContext
    independent(env)
    ingest(env)
    conversation = env.store.conversation("Trading follow-up")["id"]
    old = RunContext(env.store, conversation, "yes")
    env.service.control("mode", "OFF")
    with pytest.raises(ValueError, match="predates the latest trading stop"):
        env.service.control("mode", "PAPER", cancel=old)
    state = env.service._state()
    assert state["mode"] == "OFF" and state["orders"] == [] and state["positions"] == []
    assert env.store.rows("SELECT * FROM trading_audit WHERE event='simulator_order_submitted'") == []
    changes = env.store.rows("SELECT payload FROM trading_audit WHERE event='mode_changed'")
    assert all(json.loads(change["payload"])["mode"] == "OFF" for change in changes)
    new = RunContext(env.store, conversation, "Start NQ paper trading again")
    restarted = env.service.control("mode", "PAPER", cancel=new)
    assert restarted["mode"] == "PAPER", "The user can still explicitly start a new workflow after stopping"


@pytest.fixture
def api(tmp_path, env):
    from fastapi.testclient import TestClient
    from jarvis.app import create_app
    app = create_app(tmp_path / "api-private")
    app.state.settings.update({"native_notifications": False})
    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.jarvis = app
        yield client


def local_session(api):
    assert api.get("/").status_code == 200
    api.headers["X-Jarvis-Client"] = "dashboard"


def test_trading_api_uses_existing_local_session_origin_and_mutation_protections(api):
    assert api.get("/api/trading").status_code == 401
    assert api.post("/api/trading/control", json={"action": "emergency"}).status_code == 401
    assert api.get("/").status_code == 200
    assert api.post("/api/trading/control", json={"action": "emergency"}).status_code == 403
    api.headers["X-Jarvis-Client"] = "dashboard"
    assert api.post("/api/trading/control", json={"action": "emergency"},
                    headers={"Origin": "https://unrelated.example"}).status_code == 403
    current = api.get("/api/trading")
    assert current.status_code == 200 and current.json()["emergency_stop"] is False
    assert current.json()["account"] is None


def test_trading_api_strategy_change_is_proposal_until_real_boolean_approval(api):
    local_session(api)
    original = api.get("/api/trading").json()["active_strategy_id"]
    proposal = api.patch("/api/trading/config", json={"rr": 3})
    assert proposal.status_code == 200 and proposal.json()["active_unchanged"] is True
    version = proposal.json()["proposed_strategy"]["id"]
    assert api.get("/api/trading").json()["active_strategy_id"] == original
    for value in (False, "true", 1):
        rejected = api.post(f"/api/trading/strategies/{version}/activate", json={"approved": value})
        assert rejected.status_code == 400 and "approval" in rejected.json()["detail"]
    accepted = api.post(f"/api/trading/strategies/{version}/activate", json={"approved": True})
    assert accepted.status_code == 200 and accepted.json()["active_strategy_id"] == version


def test_trading_api_data_import_and_backtest_do_not_create_paper_account_history(api):
    local_session(api)
    imported = api.post("/api/trading/data", json={"symbol": "NQ", "kind": "historical",
                                                "source": "Synthetic API test fixture",
                                                "candles": [candle()]})
    assert imported.status_code == 200
    forbidden = api.post("/api/trading/backtests", json={"symbol": "ES"})
    assert forbidden.status_code == 400 and "confirmation" in forbidden.json()["detail"]
    tested = api.post("/api/trading/backtests", json={})
    assert tested.status_code == 200, tested.text
    report = tested.json()
    assert report["metrics"]["total_trades"] == 0 and report["metrics"]["win_rate"] is None
    assert api.get(f'/api/trading/backtests/{report["id"]}').json() == report
    paper = api.get("/api/trading/journal", params={"dataset": "paper"})
    assert paper.status_code == 200 and paper.json()["trades"] == []
    state = api.get("/api/trading").json()
    assert state["mode"] == "OFF" and state["orders"] == [] and state["account"] is None
    for format_name, content_type in (("csv", "text/csv"), ("pdf", "application/pdf")):
        response = api.get("/api/trading/export", params={"dataset": "backtest", "run_id": report["id"], "format": format_name})
        assert response.status_code == 200 and response.headers["content-type"].startswith(content_type)
        assert "attachment" in response.headers["content-disposition"]
        if format_name == "pdf":
            assert response.content.startswith(b"%PDF-")


def test_trading_api_cannot_assert_account_verification_or_enable_live_broker(api):
    local_session(api)
    for url, method, payload in (
        ("/api/trading/control", "post", {"action": "mode", "mode": "PAPER", "verified": True}),
        ("/api/trading/config", "patch", {"execution_backend": "live_broker"}),
        ("/api/trading/strategies", "post", {"changes": {"symbol": "ES"}}),
        ("/api/trading/control", "post", {"action": "emergency", "close_positions": "false"}),
    ):
        response = getattr(api, method)(url, json=payload)
        assert response.status_code == 400, response.text
    blocked = api.post("/api/trading/control", json={"action": "mode", "mode": "PAPER"})
    assert blocked.status_code == 400
    state = api.get("/api/trading").json()
    assert state["mode"] == "OFF" and state["emergency_stop"] is False
    assert not state["connection"]["tradingview"]["orders_enabled"] and state["orders"] == []


@pytest.mark.parametrize("text,expected", [
    ("Jarvis, stop trading for today", "pause_today"),
    ("Please stop automated entries", "off"),
    ("Jarvis, halt trading immediately", "emergency"),
])
def test_chat_trading_stop_is_local_without_provider_and_survives_restart(api, monkeypatch, text, expected):
    from jarvis.provider import ProviderError
    local_session(api)
    called = []

    def disconnected_headers():
        called.append("headers")
        raise ProviderError("Synthetic disconnected model; local stops must still work")

    async def unavailable_model(*args, **kwargs):
        called.append("complete")
        raise AssertionError("A local trading stop must never request model inference")
        yield  # Retain the provider's async-generator protocol.

    provider = api.jarvis.state.assistant.provider
    monkeypatch.setattr(provider, "headers", disconnected_headers)
    monkeypatch.setattr(provider, "complete", unavailable_model)
    service = api.jarvis.state.trading
    state = service._state()
    state["mode"] = "MONITOR"
    service._write(state)
    conversation = api.post("/api/conversations").json()["id"]
    response = api.post(f"/api/conversations/{conversation}/chat", json={"content": text})
    assert response.status_code == 200, response.text
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert not any(event["type"] == "error" for event in events), events
    assert any(event["type"] == "message" for event in events)
    assert called == []
    tools = api.jarvis.state.tools
    recovered = TradingService(api.jarvis.state.store, tools.applications, tools.desktop)
    recovered.recover()
    stopped = recovered._state()
    assert stopped["mode"] == "OFF"
    assert stopped["emergency_stop"] is (expected == "emergency")
    assert bool(stopped["pause_until"]) is (expected == "pause_today")
    actions = api.jarvis.state.store.rows("SELECT name,arguments FROM action_log WHERE conversation_id=?", (conversation,))
    assert len(actions) == 1 and actions[0]["name"] == "trading_control"


@pytest.mark.parametrize("blocked_by", ["active", "pending"])
def test_local_trading_stop_bypasses_busy_and_pending_review_barriers(api, monkeypatch, blocked_by):
    from jarvis.execution import RunContext
    from jarvis.store import identifier, now
    local_session(api)
    store = api.jarvis.state.store
    assistant = api.jarvis.state.assistant
    conversation = store.conversation("Existing trading task")["id"]
    context = RunContext(store, conversation, "Start NQ trading")
    if blocked_by == "active":
        assistant.active.add(conversation)
        assistant.contexts[conversation] = context
    else:
        store.execute("INSERT INTO approvals(id,conversation_id,tool_call_id,name,arguments,preview,created_at) VALUES(?,?,?,?,?,?,?)",
                      (identifier(), conversation, "pending-control", "trading_control", '{"action":"mode","mode":"PAPER"}', "{}", now()))

    def forbidden_headers():
        raise AssertionError("A local stop must bypass model connection checks")

    monkeypatch.setattr(assistant.provider, "headers", forbidden_headers)
    try:
        response = api.post(f"/api/conversations/{conversation}/chat", json={"content": "Stop trading for today"})
        assert response.status_code == 200, response.text
        assert '"type": "message"' in response.text
        state = api.jarvis.state.trading._state()
        assert state["mode"] == "OFF" and state["pause_until"]
        if blocked_by == "active":
            assert context.event.is_set(), "The in-flight trading workflow must not restart entries after OFF"
        else:
            assert store.pending(conversation), "Stopping must not execute an older pending action"
    finally:
        assistant.active.discard(conversation)
        assistant.contexts.pop(conversation, None)
        context.finish("cancelled")


@pytest.mark.parametrize("text", [
    "Explain why I should not stop trading",
    "Don't stop trading for today",
    "What if I stop trading?",
    "Give an example of a stop trading command",
    "Tell me how to stop trading",
])
def test_explanations_negations_and_examples_do_not_trigger_local_stop(text):
    from jarvis.trading.commands import stop_intent
    assert stop_intent(text) is None


@pytest.mark.parametrize("text", ["Don’t stop trading", "Jarvis, don‘t stop trading for today"])
def test_smart_apostrophe_negation_cannot_turn_into_local_stop(text):
    from jarvis.trading.commands import stop_intent
    assert stop_intent(text) is None


@pytest.mark.parametrize("name,args,target", [
    ("desktop_pointer", {"window_id": 7, "x": 10, "y": 20}, "pointer"),
    ("desktop_action", {"action": "click", "window_id": 7, "element_id": "observed-order-button"}, "act"),
    ("desktop_action", {"action": "press_keys", "window_id": 7, "keys": "enter"}, "act"),
    ("browser_action", {"action": "click", "element_id": "order-button"}, "browser"),
    ("run_command", {"arguments": ["python", "--version"]}, "command"),
    ("open_application", {"name": "PowerShell", "arguments": ["-Command", "Write-Output synthetic-test"]}, "application"),
    ("open_file", {"path": "C:/synthetic-test/order-script.ps1"}, "file"),
])
def test_trading_request_cannot_bypass_deterministic_execution_with_raw_input(api, monkeypatch, name, args, target):
    from jarvis.execution import RunContext
    tools = api.jarvis.state.tools
    store = api.jarvis.state.store
    conversation = store.conversation("Start trading")["id"]
    context = RunContext(store, conversation, "Start automated NQ trading in TradingView")
    sent = []

    def forbidden(*args, **kwargs):
        sent.append((args, kwargs))
        raise AssertionError("Raw computer input must be rejected before dispatch")

    if target in {"pointer", "act"}:
        monkeypatch.setattr(tools.desktop, target, forbidden)
    elif target == "browser":
        monkeypatch.setattr(tools.browser, "run", forbidden)
    elif target == "application":
        monkeypatch.setattr(tools.computer, "open_application", forbidden)
    elif target == "file":
        monkeypatch.setattr(tools.computer, "open_file", forbidden)
    else:
        monkeypatch.setattr(tools.computer, "run_command", forbidden)
    with pytest.raises(ValueError, match="deterministic trading service"):
        tools.run(name, args, preview={}, conversation_id=conversation, context=context)
    assert sent == [] and tools.trading._state()["orders"] == []


@pytest.mark.parametrize("user_request", [
    "Research a better trading strategy",
    "Do not activate this strategy version",
    "Explain how I can approve a strategy",
    "Show an example of switching strategy versions",
    "Don’t activate this strategy version",
    "Should I activate strategy V2?",
    "How do I activate a strategy?",
    "Would you recommend I activate this strategy?",
])
def test_model_strategy_activation_needs_positive_current_user_approval(api, user_request):
    from jarvis.execution import RunContext
    tools = api.jarvis.state.tools
    proposal = tools.trading.propose("Research proposal", {"rr": 3})
    original = tools.trading._state()["active_strategy_id"]
    conversation = api.jarvis.state.store.conversation("Strategy review")["id"]
    context = RunContext(api.jarvis.state.store, conversation, user_request)
    with pytest.raises(ValueError, match="explicitly approve"):
        tools.run("trading_strategy", {"action": "activate", "version_id": proposal["id"]},
                  conversation_id=conversation, context=context)
    assert tools.trading._state()["active_strategy_id"] == original


@pytest.mark.parametrize("user_request", [
    "I approve activating this strategy version",
    "Can you activate this strategy version for me?",
])
def test_model_strategy_activation_accepts_explicit_current_approval(api, user_request):
    from jarvis.execution import RunContext
    tools = api.jarvis.state.tools
    proposal = tools.trading.propose("Approved 3R", {"rr": 3})
    conversation = api.jarvis.state.store.conversation("Strategy approval")["id"]
    context = RunContext(api.jarvis.state.store, conversation, user_request)
    result = tools.run("trading_strategy", {"action": "activate", "version_id": proposal["id"]},
                       conversation_id=conversation, context=context)
    assert result["active_strategy_id"] == proposal["id"]


@pytest.mark.parametrize("user_request", [
    "Do not reset the trading emergency stop",
    "Explain how to reset the emergency stop",
    "Never clear my emergency stop without asking",
    "Start paper trading",
    "Don’t reset the trading emergency stop",
    "How do I reset the emergency stop?",
    "Reset my reminders",
])
def test_negated_or_unrequested_emergency_reset_cannot_be_invented_by_model(api, user_request):
    from jarvis.execution import RunContext
    tools = api.jarvis.state.tools
    tools.trading.control("emergency")
    conversation = api.jarvis.state.store.conversation("Emergency state")["id"]
    context = RunContext(api.jarvis.state.store, conversation, user_request)
    with pytest.raises(ValueError, match="explicit user reset request"):
        tools.run("trading_control", {"action": "reset_emergency"}, conversation_id=conversation, context=context)
    assert tools.trading._state()["emergency_stop"] is True
    assert tools.trading._state()["mode"] == "OFF"


@pytest.mark.parametrize("user_request", [
    "Please reset the trading emergency stop",
    "Could you reset the trading emergency stop for me?",
])
def test_explicit_positive_emergency_reset_clears_latch_and_keeps_entries_off(api, user_request):
    from jarvis.execution import RunContext
    tools = api.jarvis.state.tools
    tools.trading.control("emergency")
    conversation = api.jarvis.state.store.conversation("Reset stop")["id"]
    context = RunContext(api.jarvis.state.store, conversation, user_request)
    result = tools.run("trading_control", {"action": "reset_emergency"}, conversation_id=conversation, context=context)
    assert result["mode"] == "OFF" and result["emergency_stop"] is False


@pytest.mark.parametrize("value", [True, False, "not-money", "NaN", "Infinity", {}, -1, 0])
def test_api_rejects_invalid_initial_equity_without_500_or_state_change(api, value):
    local_session(api)
    before = api.jarvis.state.trading._state()
    response = api.patch("/api/trading/config", json={"initial_equity": value})
    assert response.status_code == 400, response.text
    assert api.jarvis.state.trading._state() == before
