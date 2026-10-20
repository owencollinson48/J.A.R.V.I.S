"""The independent simulator never claims a TradingView fill or trades ES."""
from copy import deepcopy
from decimal import Decimal

import pytest

from jarvis.trading.simulator import PaperSimulator


def make_sim(config):
    return PaperSimulator({"max_exposure_pct": 1000, **config})


def account():
    return {"balance": "100000", "equity": "100000", "available_funds": "100000"}


def setup(**changes):
    return {"setup_id": "sweep-bos-2024-01-02-1", "symbol": "NQ", "passed": True,
        "time": "2024-01-02T14:35:00Z", "direction": "long", "entry": "17000",
        "stop_loss": "16990", "take_profit": "17025", "strategy_version": "V2",
        "rating": 90, "evidence": {"liquidity_sweep": {"level": "16992"}},
        "conditions": {"bos": {"passed": True}}, **changes}


def bar(time="2024-01-02T14:40:00Z", **changes):
    return {"time": time, "open": "17000", "high": "17005", "low": "16995", "close": "17002", **changes}


def test_submission_is_stable_pending_and_never_mutates_caller_data():
    sim = make_sim({})
    cash, signal = account(), setup()
    before = deepcopy((cash, signal))
    first, second = sim.submit(cash, signal, 1), sim.submit(cash, signal, 1)
    assert first == second and first["status"] == "pending" and "fill_price" not in first
    assert first["dataset"] == "independent_paper" and first["strategy_version"] == "V2"
    assert (cash, signal) == before


def test_next_bar_only_fill_is_idempotent_and_preserves_evidence():
    sim = make_sim({})
    cash, pending = account(), sim.submit(account(), setup(), 1)
    before = deepcopy((cash, pending))
    same = sim.on_bar(cash, [pending], [], bar("2024-01-02T14:35:00Z"))
    assert not same["positions"] and same["orders"][0]["status"] == "pending"
    filled = sim.on_bar(same["account"], same["orders"], [], bar())
    assert filled["orders"][0]["status"] == "filled" and len(filled["positions"]) == 1
    assert filled["positions"][0]["entry_time"] == "2024-01-02T14:40:00+00:00"
    repeated = sim.on_bar(filled["account"], filled["orders"], filled["positions"], bar())
    assert repeated == filled and (cash, pending) == before
    assert repeated["positions"][0]["evidence"] == setup()["evidence"]


def test_same_bar_bracket_collision_is_stop_first_and_costs_reconcile():
    sim = make_sim({"spread": ".5", "slippage": ".25", "commission_per_contract": "2"})
    pending = sim.submit(account(), setup(), 1)
    result = sim.on_bar(account(), [pending], [], bar(high="17030", low="16980"))
    assert not result["positions"] and len(result["closed_trades"]) == 1
    trade = result["closed_trades"][0]
    assert trade["exit_reason"] == "stop_loss" and trade["exit_price"] == "16990"
    assert Decimal(trade["gross_pnl"]) == -200 and Decimal(trade["costs"]) == 24
    assert Decimal(trade["net_pnl"]) == -224
    assert Decimal(result["account"]["balance"]) == Decimal("99776")
    assert Decimal(trade["realised_r"]) == Decimal("-1.12")
    assert trade["dataset"] == "independent_paper" and "TradingView" not in str(trade)


def test_gap_stop_fills_at_worse_open_instead_of_the_unavailable_stop():
    sim = make_sim({})
    order = sim.submit(account(), setup(), 1)
    first = sim.on_bar(account(), [order], [], bar())
    result = sim.on_bar(first["account"], first["orders"], first["positions"],
        bar("2024-01-02T14:45:00Z", open="16980", high="16990", low="16970", close="16985"))
    assert result["closed_trades"][0]["exit_price"] == "16980"
    assert Decimal(result["account"]["balance"]) == 99600


@pytest.mark.parametrize("kind", ["limit", "stop"])
def test_unreached_limit_and_stop_orders_stay_pending(kind):
    sim = make_sim({})
    price = "16998" if kind == "limit" else "17004"
    order = sim.submit(account(), setup(), 1, order_type=kind,
        **({"limit_price": price} if kind == "limit" else {"stop_price": price}))
    result = sim.on_bar(account(), [order], [], bar(high="17003", low="16999"))
    assert result["orders"][0]["status"] == "pending" and not result["positions"]


@pytest.mark.parametrize("kind,price,expected", [("limit", "17000", "16999"), ("stop", "17000", "17001")])
def test_pending_orders_use_conservative_gap_price(kind, price, expected):
    sim = make_sim({})
    order = sim.submit(account(), setup(), 1, order_type=kind,
        **({"limit_price": price} if kind == "limit" else {"stop_price": price}))
    result = sim.on_bar(account(), [order], [], bar(open=expected))
    assert result["orders"][0]["fill_price"] == expected


def test_new_fill_cannot_cross_bracket_or_exceed_the_reserved_risk_budget():
    sim = make_sim({})
    order = sim.submit(account(), setup(), 2)
    result = sim.on_bar(account(), [order], [], bar(open="17005", high="17006", low="17000", close="17005"))
    assert not result["positions"] and result["orders"][0]["status"] == "cancelled"
    assert "risk budget" in result["orders"][0]["reason"]
    result = sim.on_bar(account(), [order], [], bar(open="16985", high="16990", low="16980", close="16988"))
    assert "bracket" in result["orders"][0]["reason"]


def test_remaining_daily_budget_is_preserved_at_submission_and_rechecked_at_fill():
    sim = make_sim({})
    with pytest.raises(ValueError, match="risk budget"):
        sim.submit(account(), setup(risk={"available_risk_budget": "300"}), 2)
    order = sim.submit(account(), setup(), 2)
    cash = {**account(), "daily_loss": "1800", "weekly_loss": "1800",
        "day_start_equity": "100000", "week_start_equity": "100000"}
    result = sim.on_bar(cash, [order], [], bar())
    assert not result["positions"] and result["orders"][0]["status"] == "cancelled"
    assert "risk" in result["orders"][0]["reason"].lower()


def test_pending_entry_cannot_average_into_an_existing_losing_nq_position():
    sim = make_sim({})
    order = sim.submit(account(), setup(), 1)
    first = sim.on_bar(account(), [order], [], bar())
    second = sim.submit(first["account"], setup(setup_id="new-sweep", time="2024-01-02T14:40:00Z"), 1)
    result = sim.on_bar(first["account"], first["orders"]+[second], first["positions"],
        bar("2024-01-02T14:45:00Z", open="16998", low="16995", close="16999"))
    rejected = result["orders"][-1]
    assert rejected["status"] == "cancelled" and "losing NQ" in rejected["reason"]
    assert len(result["positions"]) == 1
    assert result["positions"][0]["current_price"] == "16999"
    assert result["positions"][0]["unrealized_pnl"] == "-20"


def test_fill_rechecks_notional_exposure_even_if_order_was_accepted_earlier():
    permissive = make_sim({})
    order = permissive.submit(account(), setup(), 1)
    reduced = make_sim({"max_exposure_pct": 200})
    result = reduced.on_bar(account(), [order], [], bar())
    assert result["orders"][0]["status"] == "cancelled" and not result["positions"]
    assert "exposure" in result["orders"][0]["reason"].lower()


def test_es_incomplete_or_invalid_setup_cannot_be_submitted():
    sim = make_sim({})
    for changes in ({"symbol": "ES"}, {"passed": False}, {"direction": "buy"},
        {"stop_loss": "17001"}, {"take_profit": "16999"}, {"entry": "17000.10"}, {"setup_id": ""}):
        with pytest.raises(ValueError): sim.submit(account(), setup(**changes), 1)
    for quantity in (0, -1, .5, True, "NaN"):
        with pytest.raises(ValueError): sim.submit(account(), setup(), quantity)


def test_execution_limits_and_contract_specs_are_not_overridden():
    with pytest.raises(ValueError, match="E-mini"):
        make_sim({"contract_multiplier": 2})
    with pytest.raises(ValueError, match="spread or slippage"):
        make_sim({"spread": 3}).submit(account(), setup(), 1)
    with pytest.raises(ValueError, match="risk budget"):
        make_sim({}).submit(account(), setup(), 3)


def test_session_restriction_is_rechecked_at_fill_while_open_positions_are_managed():
    sim = make_sim({})
    signal = setup(time="2024-01-02T16:25:00Z")  # 11:25 New York, five minutes before close.
    order = sim.submit(account(), signal, 1)
    result = sim.on_bar(account(), [order], [], bar("2024-01-02T16:35:00Z"))
    assert result["orders"][0]["status"] == "cancelled" and not result["positions"]
    first = sim.on_bar(account(), [order], [], bar("2024-01-02T16:30:00Z"))
    assert first["positions"]
    after = sim.on_bar(first["account"], first["orders"], first["positions"],
        bar("2024-01-02T16:35:00Z", high="17025"))
    assert after["closed_trades"][0]["exit_reason"] == "take_profit"


def test_ny_dst_entry_window_moves_with_the_market_timezone():
    sim = make_sim({})
    # UTC 13:05 is pre-open in July but outside the January New York window.
    summer = setup(time="2024-07-02T13:05:00Z")
    assert sim.submit(account(), summer, 1)["status"] == "pending"
    with pytest.raises(ValueError, match="session"):
        sim.submit(account(), setup(time="2024-01-02T13:05:00Z"), 1)


def test_break_even_and_trailing_stops_apply_from_the_next_bar():
    sim = make_sim({"break_even_r": 1, "trailing_points": 5})
    order = sim.submit(account(), setup(), 1)
    first = sim.on_bar(account(), [order], [], bar(high="17015", low="16995", close="17012"))
    assert first["positions"][0]["stop_loss"] == "17007"
    assert not first["closed_trades"], "The newly computed stop cannot be applied to earlier intrabar lows"
    after = sim.on_bar(first["account"], first["orders"], first["positions"],
        bar("2024-01-02T14:45:00Z", open="17012", high="17014", low="17006", close="17008"))
    assert after["closed_trades"][0]["exit_price"] == "17007"


def test_partial_profit_is_aggregated_into_one_final_position_journal_record():
    sim = make_sim({"partial_take_profit_r": 1, "partial_fraction": ".5", "commission_per_contract": 1})
    order = sim.submit(account(), setup(), 2)
    first = sim.on_bar(account(), [order], [], bar(high="17012", close="17010"))
    assert first["positions"][0]["quantity"] == "1" and not first["closed_trades"]
    assert Decimal(first["account"]["balance"]) == 100197
    after = sim.on_bar(first["account"], first["orders"], first["positions"],
        bar("2024-01-02T14:45:00Z", open="17010", high="17025", low="17005", close="17020"))
    trade = after["closed_trades"][0]
    assert trade["quantity"] == "2" and trade["gross_pnl"] == "700"
    assert trade["costs"] == "4" and trade["net_pnl"] == "696"
    assert len(trade["exit_events"]) == 2 and Decimal(after["account"]["balance"]) == 100696


def test_short_position_uses_directional_bracket():
    sim = make_sim({})
    order = sim.submit(account(), setup(direction="short", stop_loss="17010", take_profit="16975"), 1)
    result = sim.on_bar(account(), [order], [], bar(low="16970", close="16980"))
    assert result["closed_trades"][0]["exit_price"] == "16975"
    assert Decimal(result["account"]["balance"]) == 100500


def test_emergency_cancel_and_close_require_current_observed_quotes():
    sim = make_sim({})
    order = sim.submit(account(), setup(), 1)
    cancelled = sim.cancel_pending([order])
    assert cancelled[0]["status"] == "cancelled" and order["status"] == "pending"
    first = sim.on_bar(account(), [order], [], bar())
    for quotes in ({}, {"NQ": "17002"}, {"NQ": {"price": "17002", "time": "2024-01-02T14:35:00Z"}}):
        with pytest.raises(ValueError): sim.close_positions(first["account"], first["positions"], quotes)
    result = sim.close_positions(first["account"], first["positions"],
        {"NQ": {"price": "17002", "time": "2024-01-02T14:40:00Z"}})
    assert result["closed_trades"][0]["exit_reason"] == "emergency"
    assert not result["positions"] and Decimal(result["account"]["balance"]) == 100040


def test_duplicate_ledger_ids_and_out_of_order_bars_suspend_instead_of_refilling():
    sim = make_sim({})
    order = sim.submit(account(), setup(), 1)
    with pytest.raises(ValueError, match="unique"):
        sim.on_bar(account(), [order, order], [], bar())
    first = sim.on_bar(account(), [order], [], bar())
    with pytest.raises(ValueError, match="older"):
        sim.on_bar(first["account"], first["orders"], first["positions"], bar("2024-01-02T14:35:00Z"))
