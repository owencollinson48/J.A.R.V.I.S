"""Read-only, bounded strategy comparison on the same observed market data.

Parameter selection uses training data only. Held-out and expanding-prefix
results stay separate so they cannot silently become in-sample selection data.
"""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import random


MAX_VERSIONS = 6
MAX_CANDLES = 10000
MONTE_CARLO_SAMPLES = 200
MONTE_CARLO_SEED = 1245794902
CONTROLLED_FIELDS = (
    "symbol", "smt_symbol", "timeframe_minutes", "contract_multiplier", "tick_size", "quantity_step",
    "spread", "slippage", "commission_per_contract", "max_spread", "max_slippage", "risk_pct",
    "daily_loss_pct", "weekly_loss_pct", "max_positions", "max_exposure_pct", "max_data_age_seconds",
)


def run_backtest(*args, **kwargs):
    # Delay import so research reports can be independently tested and do not
    # instantiate any execution, connection or persistence service.
    from .backtest import run_backtest as replay
    return replay(*args, **kwargs)


def _number(value, label="value"):
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{label} must be a finite number") from None
    if not result.is_finite():
        raise ValueError(f"{label} must be a finite number")
    return result


def _text(value):
    return "0" if value == 0 else format(value.normalize(), "f")


def _time(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        raise ValueError("Research candles need ISO timestamps with an explicit timezone") from None
    if parsed.tzinfo is None:
        raise ValueError("Research candles need ISO timestamps with an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _candles(values):
    if not isinstance(values, list) or len(values) > MAX_CANDLES:
        raise ValueError(f"Research accepts at most {MAX_CANDLES:,} observed candles; select a bounded period")
    result = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("Research candles must be OHLC objects")
        if value.get("closed", True) is not True:
            continue
        result.append({**value, "time": _time(value.get("time")).isoformat()})
    result.sort(key=lambda value: value["time"])
    if len({value["time"] for value in result}) != len(result):
        raise ValueError("Reconcile duplicate candle timestamps before comparing strategies")
    return result


def _versions(values):
    if isinstance(values, dict):
        values = [{"id": identity, "config": config} for identity, config in values.items()]
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_VERSIONS:
        raise ValueError(f"Compare between 1 and {MAX_VERSIONS} strategy versions")
    result, identities = [], set()
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get("config"), dict):
            raise ValueError("Each strategy version needs an id and a config object")
        identity = value.get("id", value.get("name", value.get("version")))
        if not isinstance(identity, str) or not identity.strip() or len(identity) > 80:
            raise ValueError("Strategy version ids must contain 1 to 80 characters")
        if identity in identities:
            raise ValueError("Strategy version ids must be unique")
        identities.add(identity)
        result.append({"id": identity, "config": deepcopy(value["config"])})
    return result


def _compact(report):
    return {"metrics": report.get("metrics", {}), "coverage": report.get("coverage", {}),
            "trade_count": len(report.get("trades", [])), "rejected_count": len(report.get("rejected", []))}


def _metric(report, name):
    value = report.get("metrics", {}).get(name)
    return Decimal(0) if value is None else _number(value, name)


def _quantile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    location = Decimal(len(ordered) - 1) * fraction
    lower = int(location)
    upper = min(lower + 1, len(ordered) - 1)
    return _text(ordered[lower] + (ordered[upper] - ordered[lower]) * (location - lower))


def _distribution(values):
    return {"p05": _quantile(values, Decimal(".05")), "median": _quantile(values, Decimal(".5")),
            "p95": _quantile(values, Decimal(".95"))}


def _monte_carlo(trades, identity, initial):
    pnl = [_number(trade["net_pnl"], "Recorded net P/L") for trade in trades
           if trade.get("status", "closed") == "closed" and trade.get("net_pnl") is not None]
    result = {"seed": MONTE_CARLO_SEED, "simulations": MONTE_CARLO_SAMPLES,
              "method": "IID bootstrap with replacement of recorded closed-trade net P/L",
              "versions": [], "assumptions": [
                  "Resamples observed net outcomes, not market prices or future trade opportunities.",
                  "IID resampling does not preserve serial correlation, changing regimes or unknown tail losses.",
                  "Distribution quantiles describe this resampling experiment, not a guarantee or a confidence interval for future profitability."]}
    if not pnl:
        result["reason"] = "No recorded closed trades are available to resample."
        return result
    generator = random.Random(MONTE_CARLO_SEED)
    terminals, drawdowns = [], []
    losses = 0
    for _ in range(MONTE_CARLO_SAMPLES):
        equity = peak = initial
        maximum = Decimal(0)
        for _ in range(len(pnl)):
            equity += pnl[generator.randrange(len(pnl))]
            peak = max(peak, equity)
            maximum = max(maximum, peak - equity)
        profit = equity - initial
        terminals.append(profit)
        drawdowns.append(maximum)
        losses += profit < 0
    result["versions"].append({"id": identity, "observed_trades": len(pnl),
        "terminal_pnl": _distribution(terminals), "max_drawdown": _distribution(drawdowns),
        "loss_probability": _text(Decimal(losses) / MONTE_CARLO_SAMPLES),
        "loss_probability_pct": _text(Decimal(losses) / MONTE_CARLO_SAMPLES * 100)})
    return result


def _compare(candles, versions, correlated, initial_equity):
    observed = _candles(candles)
    reference = _candles([] if correlated is None else correlated)
    versions = _versions(versions)
    initial = _number(initial_equity, "Initial equity")
    if initial <= 0:
        raise ValueError("Initial equity must be positive")
    warnings = [
        "Research never activates or replaces a strategy; applying a candidate requires your explicit approval.",
        "Historical performance and bootstrap experiments do not establish future profitability.",
        "Data coverage, futures roll adjustments, omitted trading days and execution assumptions can materially change results.",
        "Walk-forward rows evaluate each fixed supplied version; early folds overlap the candidate-selection training period. Only the final 30% holdout is strictly after training selection.",
    ]
    output = {"candidate_id": None, "versions": [], "monte_carlo": _monte_carlo([], None, initial),
              "findings": [], "warnings": warnings, "requires_approval": True,
              "active_strategy_changed": False, "sensitivity": [],
              "coverage": {"observed_candles": len(observed), "reference_candles": len(reference),
                           "research_candle_limit": MAX_CANDLES},
              "method": "Chronological 70/30 train/holdout and three expanding-prefix fixed-version walk-forward tests; no parameter fitting inside test folds."}
    if len(observed) < 10:
        output["status"] = "insufficient_data"
        warnings.append("At least 10 observed completed candles are required for chronological research splits. No performance or candidate was invented.")
        return output
    baseline = versions[0]["config"]
    controlled = {field: deepcopy(baseline[field]) for field in CONTROLLED_FIELDS if field in baseline}
    output["controlled_assumptions"] = {"initial_equity": _text(initial), **controlled}
    split = max(1, int(len(observed) * .7))
    output["split"] = {"training_start": observed[0]["time"], "training_end": observed[split - 1]["time"],
                       "out_of_sample_start": observed[split]["time"], "end": observed[-1]["time"]}
    if len(versions) > 1:
        warnings.append(f"{len(versions)} variants were compared. Selecting the best training score creates multiple-comparison and selection bias; holdout results were not used to select the candidate.")
    full_reports, rankings = {}, []
    for version in versions:
        identity = version["id"]
        configured = {**deepcopy(baseline), **version["config"]}
        held_fixed = []
        for field in CONTROLLED_FIELDS:
            if field in baseline:
                if configured.get(field) != baseline[field]:
                    held_fixed.append(field)
                configured[field] = deepcopy(baseline[field])
            elif field in configured:
                configured.pop(field)
                held_fixed.append(field)
        if held_fixed:
            warnings.append(f"{identity}: held {', '.join(held_fixed)} fixed to the baseline assumptions for a controlled comparison.")
        changed = {key: {"baseline": deepcopy(baseline.get(key)), "candidate": deepcopy(value)}
                   for key, value in configured.items() if baseline.get(key) != value}
        output["sensitivity"].append({"id": identity, "parameter_changes": changed,
                                      "note": "Observed results for supplied variations only; no automatic grid search or causal claim."})

        def replay(prefix, start=None, end=None):
            last = prefix[-1]["time"]
            aligned_reference = [bar for bar in reference if bar["time"] <= last]
            return run_backtest(prefix, deepcopy(configured), correlated=aligned_reference,
                                initial_equity=_text(initial), start=start, end=end,
                                strategy_version=identity)

        full = replay(observed, end=observed[-1]["time"])
        training = replay(observed[:split], end=observed[split - 1]["time"])
        held_out = replay(observed, start=observed[split]["time"], end=observed[-1]["time"])
        folds = []
        for fold in range(3):
            test_start = int(len(observed) * (fold + 2) / 5)
            test_end = int(len(observed) * (fold + 3) / 5)
            evaluation = replay(observed[:test_end], start=observed[test_start]["time"], end=observed[test_end - 1]["time"])
            folds.append({"fold": fold + 1, "train_end": observed[test_start - 1]["time"],
                          "test_start": observed[test_start]["time"], "test_end": observed[test_end - 1]["time"],
                          **_compact(evaluation)})
        record = {"id": identity, "train": _compact(training), "out_of_sample": _compact(held_out),
                  "full": _compact(full), "walk_forward": folds}
        training_count = len(training.get("trades", []))
        if training_count:
            score = _metric(training, "net_profit") / (_metric(training, "max_drawdown") + initial * Decimal(".01"))
            record["training_selection_score"] = _text(score)
            rankings.append((score, identity))
        else:
            record["training_selection_score"] = None
        full_reports[identity] = full
        output["versions"].append(record)
        for label, report in (("training", training), ("out-of-sample", held_out)):
            count = len(report.get("trades", []))
            if count < 30:
                warnings.append(f"{identity}: only {count} {label} closed trades; statistical evidence is insufficient to claim a reliable improvement.")
        if _metric(training, "net_profit") > 0 and _metric(held_out, "net_profit") <= 0:
            warnings.append(f"{identity}: positive training P/L did not persist in the holdout; possible overfitting or regime change needs investigation.")
        test_wins = sum(_metric({"metrics": fold["metrics"]}, "net_profit") > 0 for fold in folds)
        if test_wins < 2:
            warnings.append(f"{identity}: fewer than two of three chronological walk-forward test folds were profitable; the observed improvement is unstable.")
    if rankings:
        # Python's stable max keeps the earlier supplied version on exact ties.
        candidate = max(rankings, key=lambda item: item[0])[1]
        output["candidate_id"] = candidate
        output["monte_carlo"] = _monte_carlo(full_reports[candidate].get("trades", []), candidate, initial)
        output["monte_carlo"]["data_scope"] = "Selected candidate's recorded full-period closed trades, including the separately reported holdout."
        output["findings"].append(f"{candidate} has the highest training-only score: net P/L divided by (maximum realised drawdown plus 1% of initial equity). This is a research candidate, not an approved strategy change.")
        output["findings"].append("Inspect its separate holdout and walk-forward results before deciding whether further testing is warranted; they were not used for selection.")
    else:
        warnings.append("No version produced a closed training trade; there is no evidence-based candidate to recommend.")
    output["status"] = "completed"
    return output


def compare_versions(candles, versions, *, correlated=None, initial_equity="100000"):
    """Compare supplied versions without orders, persistence or active mutation."""
    with localcontext() as context:
        context.prec = 28
        return _compare(candles, versions, correlated, initial_equity)
