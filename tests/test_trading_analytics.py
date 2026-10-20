"""Trade statistics are reproducible, finite and derived from recorded results."""
import copy
import csv
from decimal import Decimal, getcontext
import io
import json
import re

import pytest

from jarvis.trading.analytics import analyze, export_csv, export_pdf


def trade(pnl, day=1, **extra):
    return {"id": f"t-{day}-{pnl}", "symbol": "NQ", "status": "closed", "direction": "long",
            "entry_time": f"2026-10-{day:02}T13:30:00+00:00", "exit_time": f"2026-10-{day:02}T14:30:00+00:00",
            "net_pnl": str(pnl), "gross_pnl": str(Decimal(str(pnl)) + Decimal("4")), "costs": "4",
            "planned_rr": "2.5", "realized_r": str(Decimal(str(pnl)) / 100),
            "session": "New York", "strategy_version": "V1", "setup_type": "FVG", **extra}


def test_financial_identities_and_peak_drawdown_use_chronological_net_outcomes():
    trades = [trade(-50, 3), trade(200, 1), trade(-100, 2), trade(0, 4), trade(50, 5)]
    original = copy.deepcopy(trades)
    report = analyze(trades, "1000")
    metrics = report["metrics"]
    assert metrics["total_trades"] == 5 and metrics["wins"] == 2 and metrics["losses"] == 2
    assert metrics["break_even_trades"] == 1 and metrics["win_rate"] == "40"
    assert metrics["gross_profit"] == "250" and metrics["gross_loss"] == "150"
    assert metrics["gross_pnl"] == "120" and metrics["trading_costs"] == "20"
    assert metrics["net_profit"] == "100" and metrics["ending_equity"] == "1100"
    assert metrics["equity_growth"] == "10"
    assert metrics["average_win"] == "125" and metrics["average_loss"] == "-75"
    assert metrics["expected_value_per_trade"] == "20"
    assert Decimal(metrics["profit_factor"]) == Decimal(250) / 150
    assert metrics["max_drawdown"] == "150" and metrics["max_drawdown_pct"] == "12.5"
    assert metrics["average_drawdown"] == "100"
    assert metrics["longest_winning_streak"] == 1 and metrics["longest_losing_streak"] == 2
    assert metrics["average_planned_rr"] == "2.5" and metrics["average_realized_r"] == "0.2"
    assert metrics["average_trade_duration_seconds"] == "3600"
    assert [row["equity"] for row in report["equity_curve"]] == ["1000", "1200", "1100", "1050", "1050", "1100"]
    assert trades == original


def test_decimal_results_do_not_depend_on_callers_decimal_precision():
    previous = getcontext().prec
    getcontext().prec = 6
    try:
        report = analyze([trade("0.1"), trade("0.2", 2)], "1000")
        assert report["metrics"]["net_profit"] == "0.3"
        assert getcontext().prec == 6
    finally:
        getcontext().prec = previous


def test_empty_results_have_defined_counts_and_no_invented_ratios():
    report = analyze([])
    metrics = report["metrics"]
    assert metrics["total_trades"] == 0 and metrics["net_profit"] == "0"
    assert metrics["ending_equity"] == "100000"
    for name in ("win_rate", "average_win", "average_loss", "average_planned_rr", "average_realized_r",
                 "profit_factor", "expected_value_per_trade", "sharpe_ratio", "sortino_ratio", "recovery_factor"):
        assert metrics[name] is None
    assert report["charts"]["daily_pnl"] == []
    json.dumps(report, allow_nan=False)


def test_open_and_unrecorded_results_do_not_become_closed_performance():
    report = analyze([trade("10"), {"status": "open", "net_pnl": "1000000"},
                      {"status": "closed", "net_pnl": None}, {"status": "cancelled", "net_pnl": "999999"}])
    assert report["metrics"]["total_trades"] == 1
    assert report["metrics"]["net_profit"] == "10"
    assert report["excluded_trades"] == 3


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "oops", True, None])
def test_invalid_equity_is_not_serialised_as_a_financial_figure(value):
    with pytest.raises(ValueError, match="finite"):
        analyze([], value)


@pytest.mark.parametrize("value", ["0", "-1"])
def test_nonpositive_start_equity_cannot_produce_misleading_returns(value):
    with pytest.raises(ValueError, match="positive"):
        analyze([], value)


@pytest.mark.parametrize("field,value", [("net_pnl", "NaN"), ("costs", "Infinity"), ("planned_rr", "NaN"),
                                        ("realized_r", "-Infinity"), ("gross_pnl", True)])
def test_invalid_recorded_numeric_fields_fail_the_report(field, value):
    with pytest.raises(ValueError, match="finite"):
        analyze([trade("5", **{field: value})])


def test_inconsistent_cost_identity_is_rejected():
    with pytest.raises(ValueError, match="minus costs"):
        analyze([trade("5", gross_pnl="10", costs="1")])
    with pytest.raises(ValueError, match="cannot be negative"):
        analyze([trade("5", gross_pnl="4", costs="-1")])


def test_missing_optional_measurements_remain_unavailable():
    data = {"status": "closed", "net_pnl": "2", "exit_time": "2026-10-01T13:00:00Z"}
    metrics = analyze([data])["metrics"]
    for name in ("trading_costs", "gross_pnl", "average_planned_rr", "average_realized_r", "average_trade_duration_seconds"):
        assert metrics[name] is None


@pytest.mark.parametrize("exit_time", ["2026-10-01T13:00:00", "garbage", None])
def test_exit_times_need_explicit_timezones(exit_time):
    with pytest.raises(ValueError, match="explicit timezone"):
        analyze([trade("2", exit_time=exit_time)])


def test_trade_duration_cannot_run_backwards():
    with pytest.raises(ValueError, match="precede"):
        analyze([trade("2", entry_time="2026-10-02T14:00:00Z")])


def test_utc_exit_dates_are_used_for_both_reports_and_chart_groups():
    report = analyze([trade("100", exit_time="2026-10-01T00:30:00+10:00", entry_time="2026-09-30T23:30:00+10:00")])
    assert report["charts"]["daily_pnl"][0]["label"] == "2026-09-30"
    assert report["charts"]["monthly_pnl"][0]["label"] == "2026-09"
    assert report["charts"]["performance_by_weekday"][0]["label"] == "Wednesday"


def test_british_journal_aliases_and_unknown_groups_are_supported():
    data = trade("50")
    data.pop("realized_r")
    data.pop("session")
    data.update(realised_r="0.5", market_session="London")
    report = analyze([data])
    assert report["metrics"]["average_realized_r"] == report["metrics"]["average_realised_r"] == "0.5"
    assert report["charts"]["win_rate_by_session"][0]["label"] == "London"
    assert report["charts"]["r_distribution"] == [{"label": "0.5", "value": 1}]


def test_chart_series_partition_the_same_recorded_total():
    report = analyze([trade("20", 1), trade("-10", 2, session="London", setup_type="IFVG", strategy_version="V2")])
    for name in ("daily_pnl", "monthly_pnl", "profit_by_setup_type", "performance_by_instrument",
                 "performance_by_weekday", "performance_by_strategy_version"):
        assert sum((Decimal(row["net_pnl"]) for row in report["charts"][name]), Decimal(0)) == Decimal("10")
    sessions = {row["label"]: row for row in report["charts"]["win_rate_by_session"]}
    assert sessions["London"]["value"] == "0" and sessions["New York"]["value"] == "100"


def test_daily_ratios_use_independent_dates_and_no_infinite_risk_free_profit():
    one_day = analyze([trade("20"), trade("10")])["metrics"]
    assert one_day["daily_return_samples"] == 1 and one_day["sharpe_ratio"] is None
    no_downside = analyze([trade("20", 1), trade("10", 2)])["metrics"]
    assert no_downside["sharpe_ratio"] is not None and no_downside["sortino_ratio"] is None
    mixed = analyze([trade("20", 1), trade("-10", 2)])["metrics"]
    assert Decimal(mixed["sharpe_ratio"]).is_finite() and Decimal(mixed["sortino_ratio"]).is_finite()


def test_account_ruin_does_not_generate_invalid_returns_or_hide_losses():
    metrics = analyze([trade("-150", 1), trade("-10", 2)], "100")["metrics"]
    assert metrics["ending_equity"] == "-60" and metrics["max_drawdown"] == "160"
    assert metrics["max_drawdown_pct"] == "160"
    assert metrics["daily_return_samples"] == 1 and metrics["sharpe_ratio"] is None


def test_csv_contains_all_rows_evidence_and_safe_text_without_rewriting_negative_pnl():
    rows = [trade("-50", symbol="=HYPERLINK(\"bad\")", evidence={"bos": True}), trade("20", 2)]
    result = list(csv.DictReader(io.StringIO(export_csv(rows))))
    assert len(result) == 2 and result[0]["net_pnl"] == "-50"
    assert result[0]["symbol"].startswith("'=")
    assert json.loads(result[0]["evidence"]) == {"bos": True}
    assert result[0]["realised_r"] == "-0.5" and result[0]["market_session"] == "New York"


def test_pdf_has_valid_cross_references_and_escapes_literal_text():
    report = analyze([trade("20")])
    report["dataset"] = "independent_paper (test) \\ local"
    result = export_pdf(report)
    assert result.startswith(b"%PDF-1.4") and result.endswith(b"%%EOF\n")
    offset = int(re.search(rb"startxref\n(\d+)\n", result).group(1))
    assert result[offset:].startswith(b"xref\n")
    assert b"independent_paper \\(test\\) \\\\ local" in result
    lines = result[offset:].splitlines()
    size = int(lines[1].split()[1])
    for object_id in range(1, size):
        object_offset = int(lines[2 + object_id].split()[0])
        assert result[object_offset:].startswith(f"{object_id} 0 obj\n".encode())


def test_pdf_paginates_without_silently_dropping_trade_journal_rows():
    report = analyze([trade("20")])
    report["trades"] = [trade("20", id="row-" + str(i)) for i in range(80)]
    result = export_pdf(report)
    assert result.count(b"/Type /Page ") >= 2
    assert b"row-0" in result and b"row-79" in result
