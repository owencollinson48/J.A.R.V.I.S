"""Imported prices stay real, completed, ordered and timezone explicit."""
from copy import deepcopy

import pytest

from jarvis.trading.data import coverage, normalize_candles, parse_csv


def candle(time="2024-01-02T14:35:00Z", **changes):
    return {"time": time, "open": "17000", "high": "17010", "low": "16990", "close": "17005", **changes}


def test_close_times_normalize_to_utc_and_missing_volume_stays_unknown():
    source = [candle("2024-01-02T09:35:00-05:00")]
    before = deepcopy(source)
    result = normalize_candles(source)
    assert result[0]["time"] == "2024-01-02T14:35:00+00:00"
    assert result[0]["volume"] is None and result[0]["symbol"] == "NQ"
    assert source == before


@pytest.mark.parametrize("changes", [
    {"open": "NaN"}, {"high": "Infinity"}, {"low": "-Infinity"}, {"close": True},
    {"high": "16000"}, {"low": "18000"}, {"volume": "-1"}, {"open": "0"},
    {"time": "2024-01-02T14:35:00"}, {"time": 1704206100}, {"time": "not-a-date"},
    {"closed": False}, {"closed": "false"}, {"closed": 1}, {"symbol": "ES"},
    {"timestamp": "2024-01-02T14:40:00Z"},
    {"close": "1e99999"}, {"close": "1e-99999"},
])
def test_invalid_or_incomplete_prices_are_rejected(changes):
    with pytest.raises(ValueError): normalize_candles([candle(**changes)])


@pytest.mark.parametrize("times", [
    ["2024-01-02T14:35:00Z", "2024-01-02T14:35:00Z"],
    ["2024-01-02T14:40:00Z", "2024-01-02T14:35:00Z"],
])
def test_duplicates_and_out_of_order_input_are_not_silently_changed(times):
    with pytest.raises(ValueError, match="strictly increasing"):
        normalize_candles([candle(value) for value in times])


def test_gaps_are_reported_without_fabrication():
    rows = normalize_candles([candle(), candle("2024-01-02T14:50:00Z")])
    result = coverage(rows)
    assert len(rows) == 2 and result["count"] == 2
    assert result["calendar_missing_intervals"] == 2 and result["calendar_gap_count"] == 1
    assert "exchange/session closures" in result["note"]
    assert result["volume_missing"] == 2


def test_csv_supports_close_timestamp_alias_and_es_only_as_separate_data():
    text = "\ufeffTimestamp, Open,High,Low,Close,Volume,Closed\n2024-01-02T14:35:00Z,4800,4810,4790,4805,12,true\n"
    result = parse_csv(text, symbol="ES")
    assert result[0]["symbol"] == "ES" and result[0]["volume"] == "12"
    assert result[0]["time"] == "2024-01-02T14:35:00+00:00"


@pytest.mark.parametrize("text", ["", "time,open,high,low\nx,1,2,1\n",
    "time,time,open,high,low,close\nx,x,1,2,1,2\n",
    "time,open,high,low,close\n2024-01-02T14:35:00Z,1,2,1,2,extra\n"])
def test_malformed_csv_is_explicitly_rejected(text):
    with pytest.raises(ValueError): parse_csv(text)


@pytest.mark.parametrize("frame", [0, -1, 1441, True, 1.5])
def test_timeframe_validation(frame):
    with pytest.raises(ValueError): normalize_candles([candle()], timeframe_minutes=frame)


def test_empty_coverage_is_not_made_up_market_data():
    result = coverage([])
    assert result["count"] == 0 and result["start"] is None and result["end"] is None
