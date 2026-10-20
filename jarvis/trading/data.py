"""Validate user-licensed OHLCV; timestamps denote completed candle closes.

No missing candles or prices are inferred. NQ and ES are independent datasets;
ES supplies optional SMT confirmation and is never a trading target.
"""
import csv
import io
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


MAX_CANDLES = 100000
MAX_CSV_BYTES = 20_000_000


def decimal_value(value, label, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{label} must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{label} must be a finite number") from None
    if not result.is_finite() or (positive and result <= 0) or (nonnegative and result < 0):
        raise ValueError(f"{label} must be {'positive' if positive else 'nonnegative' if nonnegative else 'a finite number'}")
    if result.adjusted() > 18 or result.as_tuple().exponent < -18 or len(result.as_tuple().digits) > 24:
        raise ValueError(f"{label} exceeds supported numeric precision")
    return result


def timestamp(value):
    """Require an explicit offset; local naive timestamps are ambiguous at DST."""
    if not isinstance(value, str):
        raise ValueError("Candle time must be an ISO 8601 close timestamp with a timezone")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ValueError("Candle time must be an ISO 8601 close timestamp with a timezone") from None


def timeframe(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1440:
        raise ValueError("Choose a candle timeframe from 1 to 1440 minutes")
    return value


def normalize_candles(rows, symbol="NQ", timeframe_minutes=5):
    """Return strictly increasing, completed candles with decimal string prices.

    ``time`` and ``timestamp`` both mean candle CLOSE time, never open time.
    An omitted volume remains unknown (None), rather than fabricated zero.
    Gaps are accepted and reported by coverage(), never forward-filled.
    """
    timeframe(timeframe_minutes)
    if symbol not in {"NQ", "ES"}:
        raise ValueError("Import NQ candles, or ES candles for SMT confirmation only")
    if not isinstance(rows, list) or not rows or len(rows) > MAX_CANDLES:
        raise ValueError(f"Import between 1 and {MAX_CANDLES} completed candles")
    result, previous = [], None
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"Candle {index + 1} must be an object")
        if row.get("symbol", symbol) != symbol:
            raise ValueError("Each imported dataset must contain only its selected symbol")
        closed = row.get("closed", True)
        if closed is not True and not (isinstance(closed, str) and closed.strip().lower() in {"true", "1"}):
            raise ValueError("Only explicitly completed candles may be imported")
        stamp = timestamp(row.get("time", row.get("timestamp")))
        if "time" in row and "timestamp" in row and timestamp(row["timestamp"]) != stamp:
            raise ValueError("Candle time and timestamp disagree")
        if previous is not None and stamp <= previous:
            raise ValueError("Candle times must be strictly increasing; duplicate or out-of-order rows are not accepted")
        values = {key: decimal_value(row.get(key), key, positive=True) for key in ("open", "high", "low", "close")}
        if values["low"] > min(values["open"], values["close"]) or values["high"] < max(values["open"], values["close"]) or values["low"] > values["high"]:
            raise ValueError(f"Candle {index + 1} has inconsistent OHLC prices")
        volume = row.get("volume")
        result.append({"time": stamp.isoformat(), "symbol": symbol, "closed": True,
                       **{key: str(value) for key, value in values.items()},
                       "volume": str(decimal_value(volume, "volume", nonnegative=True)) if volume not in (None, "") else None})
        previous = stamp
    return result


def parse_csv(text, symbol="NQ", timeframe_minutes=5):
    """Read permitted exported data; time/timestamp must contain close offsets."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_CSV_BYTES:
        raise ValueError("CSV must be text no larger than 20 MB")
    try:
        reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
        if not reader.fieldnames:
            raise ValueError("CSV needs a header row")
        headers = [name.strip().lower() for name in reader.fieldnames]
        if len(headers) != len(set(headers)):
            raise ValueError("CSV contains duplicate column headers")
        if not ({"open", "high", "low", "close"} <= set(headers) and {"time", "timestamp"} & set(headers)):
            raise ValueError("CSV needs time (or timestamp), open, high, low and close columns")
        rows = []
        for raw in reader:
            if None in raw:
                raise ValueError("CSV row has more values than headers")
            rows.append({name.strip().lower(): value.strip() if isinstance(value, str) else value for name, value in raw.items()})
            if len(rows) > MAX_CANDLES:
                raise ValueError(f"CSV exceeds {MAX_CANDLES} candles")
    except csv.Error as exc:
        raise ValueError("CSV could not be read: " + str(exc)) from None
    return normalize_candles(rows, symbol, timeframe_minutes)


def coverage(candles, timeframe_minutes=5):
    """Report calendar gaps, which may include normal exchange closures."""
    minutes = timeframe(timeframe_minutes)
    if not candles:
        return {"count": 0, "start": None, "end": None, "timeframe_minutes": minutes,
                "calendar_gap_count": 0, "calendar_missing_intervals": 0, "gaps": [],
                "note": "No candles available; no missing data is invented."}
    expected, missing, gaps = minutes * 60, 0, []
    previous = timestamp(candles[0]["time"])
    count = 0
    for candle in candles[1:]:
        current = timestamp(candle["time"])
        seconds = (current - previous).total_seconds()
        if seconds <= 0:
            raise ValueError("Coverage requires strictly increasing candle times")
        if seconds != expected:
            count += 1
            intervals = max(0, int(seconds // expected) - 1)
            missing += intervals
            if len(gaps) < 200:
                gaps.append({"after": previous.isoformat(), "before": current.isoformat(),
                             "seconds": seconds, "missing_intervals": intervals})
        previous = current
    return {"count": len(candles), "start": candles[0]["time"], "end": candles[-1]["time"],
            "timeframe_minutes": minutes, "calendar_gap_count": count,
            "calendar_missing_intervals": missing, "gaps": gaps, "gaps_truncated": count > len(gaps),
            "volume_missing": sum(candle.get("volume") is None for candle in candles),
            "note": "Calendar gaps include possible exchange/session closures. Missing bars, prices and volumes are never synthesized."}
