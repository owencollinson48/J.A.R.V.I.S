"""Reproducible realised-trade analytics and dependency-free report exports.

Prices and money remain Decimal until serialisation.  These are realised equity
statistics, not broker balances or a mark-to-market portfolio valuation.
"""
from collections import defaultdict
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import io
import json
import textwrap


ZERO = Decimal("0")
HUNDRED = Decimal("100")


def _decimal(value, label="financial value"):
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        number = Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation):
        raise ValueError(f"{label} must be a finite number") from None
    if not number.is_finite():
        raise ValueError(f"{label} must be a finite number")
    return number


def _serial(value):
    if value is None:
        return None
    if value == ZERO:
        return "0"
    return format(value.normalize(), "f")


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result.astimezone(timezone.utc) if result.tzinfo is not None else None


def _optional(trade, field):
    value = trade.get(field)
    return None if value is None or value == "" else _decimal(value, field)


def _groups(records, group):
    values = defaultdict(list)
    for record in records:
        values[group(record)].append(record["pnl"])
    result = []
    for label, outcomes in sorted(values.items()):
        wins = sum(value > ZERO for value in outcomes)
        profit = sum(outcomes, ZERO)
        result.append({"label": str(label), "value": _serial(profit), "net_pnl": _serial(profit),
                       "trades": len(outcomes), "wins": wins,
                       "win_rate": _serial(Decimal(wins) / len(outcomes) * HUNDRED)})
    return result


def _analyze(trades, initial_equity):
    initial = _decimal(initial_equity, "Initial equity")
    if initial <= ZERO:
        raise ValueError("Initial equity must be positive")
    if not isinstance(trades, (list, tuple)):
        raise ValueError("Trades must be a list")
    records, excluded = [], 0
    for index, trade in enumerate(trades):
        if not isinstance(trade, dict):
            raise ValueError("Each trade must be an object")
        if trade.get("status", "closed") != "closed" or trade.get("net_pnl") in (None, ""):
            excluded += 1
            continue
        exit_time = _time(trade.get("exit_time"))
        if exit_time is None:
            raise ValueError("Closed trades need an exit_time with an explicit timezone")
        entry_time = _time(trade.get("entry_time"))
        if entry_time and entry_time > exit_time:
            raise ValueError("Trade exit_time cannot precede entry_time")
        records.append({"trade": trade, "pnl": _decimal(trade["net_pnl"], "net_pnl"),
                        "time": exit_time, "entry_time": entry_time, "index": index})
    records.sort(key=lambda item: (item["time"], item["index"]))
    outcomes = [record["pnl"] for record in records]
    count = len(outcomes)
    wins = [value for value in outcomes if value > ZERO]
    losses = [value for value in outcomes if value < ZERO]
    winning_profit, losing_loss = sum(wins, ZERO), -sum(losses, ZERO)
    net = sum(outcomes, ZERO)
    equity, peak, peak_pct = initial, initial, ZERO
    curve = [{"time": None, "equity": _serial(initial), "drawdown": "0", "drawdown_pct": "0"}]
    drawdowns = []
    run_win = run_loss = longest_win = longest_loss = 0
    daily = defaultdict(lambda: ZERO)
    for record in records:
        equity += record["pnl"]
        peak = max(peak, equity)
        drawdown = peak - equity
        percentage = drawdown / peak * HUNDRED
        peak_pct = max(peak_pct, percentage)
        drawdowns.append(drawdown)
        stamp = record["time"].isoformat()
        curve.append({"time": stamp, "equity": _serial(equity), "drawdown": _serial(drawdown),
                      "drawdown_pct": _serial(percentage)})
        day = record["time"].date().isoformat()
        daily[day] += record["pnl"]
        if record["pnl"] > ZERO:
            run_win, run_loss = run_win + 1, 0
        elif record["pnl"] < ZERO:
            run_loss, run_win = run_loss + 1, 0
        else:
            run_win = run_loss = 0
        longest_win, longest_loss = max(longest_win, run_win), max(longest_loss, run_loss)
    maximum = max(drawdowns, default=ZERO)
    planned = [_optional(record["trade"], "planned_rr") for record in records]
    realised = [_optional(record["trade"], "realized_r") if record["trade"].get("realized_r") is not None
                else _optional(record["trade"], "realised_r") for record in records]
    planned_values = [value for value in planned if value is not None]
    realised_values = [value for value in realised if value is not None]
    gross_values = [_optional(record["trade"], "gross_pnl") for record in records]
    cost_values = [_optional(record["trade"], "costs") for record in records]
    if any(value is not None and value < ZERO for value in cost_values):
        raise ValueError("Trading costs cannot be negative")
    for record, gross, costs in zip(records, gross_values, cost_values):
        if gross is not None and costs is not None and gross - costs != record["pnl"]:
            raise ValueError("Trade gross_pnl minus costs must equal net_pnl")
    durations = []
    for record in records:
        value = (Decimal(str((record["time"] - record["entry_time"]).total_seconds()))
                 if record["entry_time"] is not None else _optional(record["trade"], "duration_seconds"))
        if value is not None:
            if value < ZERO:
                raise ValueError("Trade duration cannot be negative")
            durations.append(value)
    daily_returns, daily_curve, daily_start = [], [], initial
    for day, profit in sorted(daily.items()):
        value = profit / daily_start if daily_start > ZERO else None
        if value is not None:
            daily_returns.append(value)
        daily_curve.append({"label": day, "time": day, "value": _serial(profit),
                            "net_pnl": _serial(profit), "return": _serial(value)})
        daily_start += profit
    sharpe = sortino = None
    if len(daily_returns) >= 2:
        average = sum(daily_returns, ZERO) / len(daily_returns)
        variance = sum(((value - average) ** 2 for value in daily_returns), ZERO) / (len(daily_returns) - 1)
        downside = sum((min(value, ZERO) ** 2 for value in daily_returns), ZERO) / len(daily_returns)
        annualisation = Decimal(252).sqrt()
        if variance > ZERO:
            sharpe = average / variance.sqrt() * annualisation
        if downside > ZERO:
            sortino = average / downside.sqrt() * annualisation
    metrics = {
        "total_trades": count, "wins": len(wins), "losses": len(losses),
        "break_even_trades": count - len(wins) - len(losses),
        "win_rate": _serial(Decimal(len(wins)) / count * HUNDRED) if count else None,
        "average_win": _serial(winning_profit / len(wins)) if wins else None,
        "average_loss": _serial(-losing_loss / len(losses)) if losses else None,
        "gross_profit": _serial(winning_profit), "gross_loss": _serial(losing_loss),
        "gross_pnl": _serial(sum(gross_values, ZERO)) if all(value is not None for value in gross_values) else None,
        "net_profit": _serial(net),
        "average_planned_rr": _serial(sum(planned_values, ZERO) / len(planned_values)) if planned_values else None,
        "average_realized_r": _serial(sum(realised_values, ZERO) / len(realised_values)) if realised_values else None,
        "profit_factor": _serial(winning_profit / losing_loss) if losing_loss > ZERO else None,
        "expected_value_per_trade": _serial(net / count) if count else None,
        "max_drawdown": _serial(maximum), "max_drawdown_pct": _serial(peak_pct),
        "average_drawdown": _serial(sum(drawdowns, ZERO) / count) if count else None,
        "longest_winning_streak": longest_win, "longest_losing_streak": longest_loss,
        "recovery_factor": _serial(net / maximum) if maximum > ZERO else None,
        "sharpe_ratio": _serial(sharpe), "sortino_ratio": _serial(sortino),
        "average_trade_duration_seconds": _serial(sum(durations, ZERO) / len(durations)) if durations else None,
        "trading_costs": _serial(sum(cost_values, ZERO)) if all(value is not None for value in cost_values) else None,
        "initial_equity": _serial(initial), "ending_equity": _serial(equity),
        "equity_growth": _serial(net / initial * HUNDRED),
        "daily_return_samples": len(daily_returns), "planned_rr_samples": len(planned_values),
        "realized_r_samples": len(realised_values), "duration_samples": len(durations),
    }
    metrics["average_realised_r"] = metrics["average_realized_r"]
    monthly = _groups(records, lambda record: record["time"].strftime("%Y-%m"))
    by_session = _groups(records, lambda record: record["trade"].get("session") or record["trade"].get("market_session") or "Unknown")
    weekday = _groups(records, lambda record: record["time"].strftime("%A"))
    weekday.sort(key=lambda item: ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].index(item["label"]))
    distribution = defaultdict(int)
    for value in realised_values:
        distribution[_serial(value)] += 1
    charts = {
        "equity_curve": curve,
        "drawdown": [{"time": item["time"], "value": item["drawdown"], "percentage": item["drawdown_pct"]} for item in curve],
        "daily_pnl": daily_curve, "monthly_pnl": monthly,
        "win_rate_by_session": [{**item, "value": item["win_rate"]} for item in by_session],
        "profit_by_setup_type": _groups(records, lambda record: record["trade"].get("setup_type") or "Unknown"),
        "r_distribution": [{"label": value, "value": count} for value, count in sorted(distribution.items(), key=lambda item: Decimal(item[0]))],
        "performance_by_instrument": _groups(records, lambda record: record["trade"].get("instrument") or record["trade"].get("symbol") or "Unknown"),
        "performance_by_weekday": weekday,
        "performance_by_strategy_version": _groups(records, lambda record: record["trade"].get("strategy_version") or "Unknown"),
    }
    assumptions = [
        "Only closed trades with recorded net P/L are included; open positions are excluded.",
        "Equity and drawdown use realised P/L in UTC exit-time order; they exclude intratrade/unrealised drawdown.",
        "Gross profit/loss sum positive/negative net trade outcomes; gross_pnl separately reports P/L before costs when supplied.",
        "Average drawdown is the mean high-water drawdown after each closed trade, including zero observations.",
        "Daily/monthly/weekday figures use UTC exit dates. Daily returns use realised P/L divided by that day's starting realised equity.",
        "Sharpe uses sample standard deviation; Sortino uses root mean squared negative daily returns, zero target/risk-free rate, and sqrt(252) annualisation.",
        "Daily-return samples include dates with closed trades only; these ratios are not complete mark-to-market portfolio or calendar-day statistics.",
        "Undefined ratios and unavailable observations are null, not infinity or fabricated zero. Historical results do not establish future profitability.",
    ]
    if len(daily_returns) < 2:
        assumptions.append("Fewer than two observed daily returns: Sharpe and Sortino are unavailable.")
    if any(item["return"] is None for item in daily_curve):
        assumptions.append("Returns after nonpositive realised equity are unavailable; inspect account insolvency and model assumptions.")
    return {"metrics": metrics, "equity_curve": curve, "charts": charts,
            "assumptions": assumptions, "excluded_trades": excluded}


def analyze(trades, initial_equity="100000"):
    """Return JSON-safe realised metrics without mutating input or global precision."""
    with localcontext() as context:
        context.prec = 28
        return _analyze(trades, initial_equity)


CSV_FIELDS = ("dataset", "id", "strategy_version", "instrument", "symbol", "direction", "status",
              "entry_time", "exit_time", "entry_price", "exit_price", "quantity", "stop_loss", "take_profit",
              "market_session", "setup_type", "setup_rating", "planned_rr", "realised_r", "gross_pnl", "costs",
              "net_pnl", "duration_seconds", "exit_reason", "setup_id", "evidence")
_NUMERIC_CSV = {"entry_price", "exit_price", "quantity", "stop_loss", "take_profit", "setup_rating", "planned_rr",
                "realised_r", "gross_pnl", "costs", "net_pnl", "duration_seconds"}


def export_csv(trades):
    """Export every supplied journal row, escaping spreadsheet formula text."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, lineterminator="\r\n")
    writer.writeheader()
    for trade in trades:
        row = {}
        for field in CSV_FIELDS:
            alias = {"entry_price": "entry", "exit_price": "exit", "market_session": "session", "realised_r": "realized_r"}
            value = trade.get(field, trade.get(alias.get(field), ""))
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, sort_keys=True)
            if value is None:
                value = ""
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                if field not in _NUMERIC_CSV:
                    value = "'" + value
                else:
                    try:
                        _decimal(value, field)
                    except ValueError:
                        value = "'" + value
            row[field] = value
        writer.writerow(row)
    return output.getvalue()


def export_pdf(report):
    """Create a plain-text multipage PDF with exact metric values and assumptions."""
    if not isinstance(report, dict):
        raise ValueError("A report object is required")
    metrics = report.get("metrics", report)
    if not isinstance(metrics, dict):
        raise ValueError("Report metrics must be an object")
    lines = ["JARVIS PAPER TRADING REPORT", "", "Realised results; simulated execution only."]
    if report.get("dataset"):
        lines.append("Dataset: " + str(report["dataset"]))
    lines.append("")
    for label, value in metrics.items():
        if not isinstance(value, (list, dict)):
            lines.extend(textwrap.wrap(f"{label.replace('_', ' ')}: {'Unavailable' if value is None else value}", 92) or [""])
    lines.extend(["", "ASSUMPTIONS AND LIMITATIONS"])
    for item in report.get("assumptions", []):
        lines.extend(textwrap.wrap(str(item), 92) or [""])
    trades = report.get("trades", [])
    if trades:
        lines.extend(["", "TRADE JOURNAL"])
        for trade in trades:
            line = " | ".join(str(trade.get(key, "")) for key in
                              ("id", "symbol", "direction", "entry_time", "exit_time", "net_pnl", "exit_reason"))
            lines.extend(textwrap.wrap(line, 92) or [""])
    pages = [lines[offset:offset + 52] for offset in range(0, len(lines), 52)]
    # All text uses literal PDF strings; user text never becomes PDF operators.
    def escaped(value):
        return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").replace("\r", " ").replace("\n", " ")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    page_ids = []
    for page in pages:
        page_id = len(objects) + 1
        page_ids.append(page_id)
        stream_id = page_id + 1
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {stream_id} 0 R >>".encode("ascii"))
        instructions = "BT\n/F1 10 Tf\n12 TL\n36 756 Td\n" + "\n".join(f"({escaped(line)}) Tj\nT*" for line in page) + "\nET\n"
        data = instructions.encode("latin-1", errors="replace")
        objects.append(f"<< /Length {len(data)} >>\nstream\n".encode("ascii") + data + b"endstream")
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[1] = f"<< /Type /Pages /Count {len(page_ids)} /Kids [{kids}] >>".encode("ascii")
    result = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, value in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{index} 0 obj\n".encode("ascii") + value + b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    result.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    return bytes(result)
