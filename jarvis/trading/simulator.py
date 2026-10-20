"""Deterministic, independent paper ledger; never sends a broker/TradingView order.

Pending entries fill from the next completed candle's open (or a reached
limit/stop), and are acknowledged at that candle's close time. Unknown intrabar
paths use stop first. Spread/slippage are explicit cash costs, not fabricated
market prices. The caller owns atomic persistence, risk validation and stop/mode
checks; this module copies every input and rejects non-NQ entry requests.
"""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, ROUND_FLOOR
from hashlib import sha256
from zoneinfo import ZoneInfo

from .data import decimal_value, normalize_candles, timestamp
from .risk import number, size_position


ZERO = Decimal("0")
ONE = Decimal("1")


def _money(value):
    return number(value)


def _identity(value):
    return sha256(str(value).encode("utf-8")).hexdigest()[:32]


class PaperSimulator:
    def __init__(self, config):
        self.config = deepcopy(config)
        self.multiplier = decimal_value(config.get("contract_multiplier", 20), "contract multiplier", positive=True)
        self.tick = decimal_value(config.get("tick_size", ".25"), "tick size", positive=True)
        if self.multiplier != 20 or self.tick != Decimal(".25"):
            raise ValueError("NQ E-mini contracts require $20 per point and a 0.25 point tick")
        self.spread = decimal_value(config.get("spread", 0), "spread", nonnegative=True)
        self.slippage = decimal_value(config.get("slippage", 0), "slippage", nonnegative=True)
        self.commission = decimal_value(config.get("commission_per_contract", 0), "commission", nonnegative=True)
        # Each side books half the spread, one slippage allowance and commission.
        self.side_cost = (self.spread / 2 + self.slippage) * self.multiplier + self.commission

    def _quantity(self, value):
        quantity = decimal_value(value, "quantity", positive=True)
        if quantity != quantity.to_integral_value() or quantity > 100000:
            raise ValueError("NQ positions require a positive whole number of E-mini contracts")
        return quantity

    def _price(self, value, label):
        price = decimal_value(value, label, positive=True)
        if price % self.tick:
            raise ValueError(f"{label} must align with the configured futures tick size")
        return price

    def _session(self, instant):
        sessions = self.config.get("sessions", [
            {"name": "London", "timezone": "Europe/London", "open": "08:00", "weekdays": [0, 1, 2, 3, 4]},
            {"name": "New York", "timezone": "America/New_York", "open": "09:30", "weekdays": [0, 1, 2, 3, 4]},
        ])
        for session in sessions:
            local = instant.astimezone(ZoneInfo(session["timezone"]))
            hour, minute = map(int, session["open"].split(":"))
            # Check the previous date too for a session window crossing midnight.
            for day in (local, local - timedelta(days=1), local + timedelta(days=1)):
                if day.weekday() not in session.get("weekdays", [0, 1, 2, 3, 4]):
                    continue
                opening = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
                start = opening - timedelta(minutes=self.config.get("pre_open_minutes", 30))
                end = opening + timedelta(minutes=self.config.get("post_open_minutes", 120))
                if start <= local <= end:
                    return session["name"]
        return None

    def submit(self, account, setup, quantity, *, order_type="market", limit_price=None, stop_price=None):
        if setup.get("symbol") != "NQ" or self.config.get("symbol", "NQ") != "NQ":
            raise ValueError("Only NQ may be traded; ES is reserved for SMT confirmation")
        if setup.get("passed") is not True:
            raise ValueError("A deterministic qualifying setup is required before simulation")
        if setup.get("direction") not in {"long", "short"}:
            raise ValueError("Order direction must be long or short")
        if order_type not in {"market", "limit", "stop"}:
            raise ValueError("Choose market, limit or stop simulation")
        created = timestamp(setup.get("time"))
        if not self._session(created):
            raise ValueError("New simulated orders require the mandatory session window")
        if decimal_value(account.get("equity", account.get("balance")), "account equity") <= 0:
            raise ValueError("Positive independent simulator equity is required")
        if self.spread > decimal_value(self.config.get("max_spread", 2), "maximum spread", nonnegative=True) or self.slippage > decimal_value(self.config.get("max_slippage", 2), "maximum slippage", nonnegative=True):
            raise ValueError("Configured spread or slippage exceeds the execution limit")
        identity = setup.get("setup_id")
        if not isinstance(identity, str) or not identity.strip() or len(identity) > 256:
            raise ValueError("A unique deterministic setup_id is required")
        quantity = self._quantity(quantity)
        entry = self._price(setup.get("entry"), "entry")
        if order_type == "limit":
            entry = self._price(limit_price if limit_price is not None else entry, "limit price")
        if order_type == "stop":
            entry = self._price(stop_price if stop_price is not None else entry, "stop entry price")
        stop = self._price(setup.get("stop_loss"), "stop-loss")
        target = self._price(setup.get("take_profit"), "take-profit")
        long = setup["direction"] == "long"
        if not (stop < entry < target if long else target < entry < stop):
            raise ValueError("The order needs a valid directional stop-loss and take-profit bracket")
        budget = decimal_value(account.get("equity", account.get("balance")), "account equity") * decimal_value(self.config.get("risk_pct", ".5"), "risk per trade", positive=True) / 100
        if isinstance(setup.get("risk"), dict) and setup["risk"].get("available_risk_budget") is not None:
            budget = min(budget, decimal_value(setup["risk"]["available_risk_budget"], "remaining risk budget", nonnegative=True))
        if (abs(entry-stop)*self.multiplier + 2*self.side_cost)*quantity > budget:
            raise ValueError("The simulated order would exceed the configured per-trade risk budget")
        return {"id": _identity("independent-paper:" + identity), "setup_id": identity,
                "status": "pending", "order_type": order_type, "symbol": "NQ", "direction": setup["direction"],
                "entry": _money(entry), "stop_loss": _money(stop), "take_profit": _money(target),
                "quantity": _money(quantity), "created_at": created.isoformat(),
                "risk_budget": _money(budget),
                "strategy_version": setup.get("strategy_version", "V1"),
                "market_session": setup.get("market_session", setup.get("session", self._session(created))),
                "rating": setup.get("rating", 0), "conditions": deepcopy(setup.get("conditions", {})),
                "evidence": deepcopy(setup.get("evidence", {})), "dataset": "independent_paper",
                "execution_model": "next completed candle; simulated reference prices with explicit costs"}

    @staticmethod
    def _unique(records, label):
        ids = [item.get("id") for item in records]
        if any(not isinstance(value, str) or not value for value in ids) or len(ids) != len(set(ids)):
            raise ValueError(f"{label} must have unique IDs; reconcile duplicate records before continuing")

    def _fill(self, order, bar):
        entry, opening = Decimal(order["entry"]), Decimal(bar["open"])
        low, high = Decimal(bar["low"]), Decimal(bar["high"])
        long = order["direction"] == "long"
        if order["order_type"] == "market":
            return opening
        if order["order_type"] == "limit":
            return min(opening, entry) if long and low <= entry else max(opening, entry) if not long and high >= entry else None
        return max(opening, entry) if long and high >= entry else min(opening, entry) if not long and low <= entry else None

    def _open(self, order, entry, when):
        quantity = Decimal(order["quantity"])
        position = {**deepcopy(order), "id": _identity("position:" + order["id"]), "order_id": order["id"],
                    "status": "open", "entry_price": _money(entry), "entry_time": when,
                    "initial_quantity": order["quantity"], "initial_stop_loss": order["stop_loss"],
                    "entry_costs": _money(quantity * self.side_cost), "exit_costs_paid": "0",
                    "gross_realized": "0", "exit_value": "0", "partial_taken": False, "events": []}
        return position

    def _exit(self, account, position, quantity, price, when, reason, closed):
        entry = Decimal(position["entry_price"])
        sign = ONE if position["direction"] == "long" else -ONE
        gross = (price - entry) * sign * quantity * self.multiplier
        fee = quantity * self.side_cost
        account["balance"] = _money(Decimal(account["balance"]) + gross - fee)
        position["quantity"] = _money(Decimal(position["quantity"]) - quantity)
        position["gross_realized"] = _money(Decimal(position["gross_realized"]) + gross)
        position["exit_costs_paid"] = _money(Decimal(position["exit_costs_paid"]) + fee)
        position["exit_value"] = _money(Decimal(position["exit_value"]) + price * quantity)
        position["events"].append({"time": when, "quantity": _money(quantity), "price": _money(price), "reason": reason})
        if Decimal(position["quantity"]) > 0:
            return
        position["status"] = "closed"
        initial = Decimal(position["initial_quantity"])
        total_gross = Decimal(position["gross_realized"])
        costs = Decimal(position["entry_costs"]) + Decimal(position["exit_costs_paid"])
        initial_risk = abs(entry - Decimal(position["initial_stop_loss"])) * self.multiplier * initial
        target_rr = abs(Decimal(position["take_profit"]) - entry) / abs(entry - Decimal(position["initial_stop_loss"]))
        closed.append({"id": position["id"], "setup_id": position["setup_id"], "order_id": position["order_id"],
                       "dataset": "independent_paper", "symbol": "NQ", "direction": position["direction"],
                       "strategy_version": position["strategy_version"], "entry_price": position["entry_price"],
                       "exit_price": _money(Decimal(position["exit_value"]) / initial),
                       "entry_time": position["entry_time"], "exit_time": when,
                       "quantity": _money(initial), "stop_loss": position["initial_stop_loss"],
                       "take_profit": position["take_profit"], "market_session": position["market_session"],
                       "evidence": deepcopy(position["evidence"]), "conditions": deepcopy(position["conditions"]),
                       "rating": position["rating"], "planned_rr": _money(target_rr),
                       "realised_r": _money((total_gross - costs) / initial_risk),
                       "gross_pnl": _money(total_gross), "costs": _money(costs), "net_pnl": _money(total_gross - costs),
                       "duration_seconds": (timestamp(when) - timestamp(position["entry_time"])).total_seconds(),
                       "exit_reason": reason, "exit_events": deepcopy(position["events"]),
                       "execution_model": position["execution_model"]})

    def _mark(self, account, positions, prices, when):
        unrealized, reserved = ZERO, ZERO
        for position in positions:
            price = prices.get(position["symbol"])
            if price is None:
                raise ValueError("A current observed price is required to value each simulated position")
            quantity, entry = Decimal(position["quantity"]), Decimal(position["entry_price"])
            sign = ONE if position["direction"] == "long" else -ONE
            gross_unrealized = (price - entry) * sign * quantity * self.multiplier
            position["current_price"] = _money(price)
            position["unrealized_pnl"] = _money(gross_unrealized)
            unrealized += gross_unrealized - quantity * self.side_cost
            reserved += abs(entry - Decimal(position["stop_loss"])) * quantity * self.multiplier
        account["equity"] = _money(Decimal(account["balance"]) + unrealized)
        account["available_funds"] = _money(max(ZERO, Decimal(account["equity"]) - reserved))
        account["unrealized_pnl"] = _money(unrealized)
        account["as_of"] = when
        account["funds_model"] = "Independent simulated equity less stop-risk reserve; not broker/exchange margin or TradingView funds."

    def on_bar(self, account, orders, positions, bar):
        account, orders, positions = deepcopy(account), deepcopy(orders), deepcopy(positions)
        self._unique(orders, "Orders")
        self._unique(positions, "Positions")
        bar = normalize_candles([bar], "NQ", self.config.get("timeframe_minutes", 5))[0]
        when, instant = bar["time"], timestamp(bar["time"])
        if account.get("as_of") and instant < timestamp(account["as_of"]):
            raise ValueError("An older candle cannot replace current simulator state")
        account["balance"] = _money(decimal_value(account.get("balance"), "account balance"))
        closed = []
        previous_equity = decimal_value(account.get("equity", account["balance"]), "account equity")
        opening_price = Decimal(bar["open"])
        for position in positions:
            quantity = Decimal(position["quantity"])
            sign = ONE if position["direction"] == "long" else -ONE
            position["current_price"] = _money(opening_price)
            position["unrealized_pnl"] = _money((opening_price - Decimal(position["entry_price"])) * sign * quantity * self.multiplier)
        self._mark(account, positions, {"NQ": opening_price}, when)
        for order in orders:
            if order.get("symbol") != "NQ":
                raise ValueError("Only NQ orders may enter the independent simulator")
            if order["status"] != "pending" or instant <= timestamp(order["created_at"]):
                continue
            if not self._session(instant):
                order.update(status="cancelled", reason="Mandatory entry session window closed", cancelled_at=when)
                continue
            entry = self._fill(order, bar)
            if entry is None:
                continue
            stop, target = Decimal(order["stop_loss"]), Decimal(order["take_profit"])
            valid = stop < entry < target if order["direction"] == "long" else target < entry < stop
            if not valid:
                order.update(status="cancelled", reason="Next-bar fill would invalidate the stop/target bracket", cancelled_at=when)
                continue
            fill_risk = (abs(entry-stop)*self.multiplier + 2*self.side_cost) * Decimal(order["quantity"])
            if fill_risk > Decimal(order["risk_budget"]):
                order.update(status="cancelled", reason="Next-bar fill would exceed its per-trade risk budget", cancelled_at=when)
                continue
            portfolio = {"positions": positions,
                "pending_orders": [item for item in orders if item["id"] != order["id"] and item.get("status") == "pending"],
                "daily_loss": _money(decimal_value(account.get("daily_loss", 0), "daily loss", nonnegative=True) + max(ZERO, previous_equity - Decimal(account["equity"]))),
                "weekly_loss": _money(decimal_value(account.get("weekly_loss", 0), "weekly loss", nonnegative=True) + max(ZERO, previous_equity - Decimal(account["equity"]))),
                "day_start_equity": account.get("day_start_equity", previous_equity),
                "week_start_equity": account.get("week_start_equity", previous_equity)}
            sizing = size_position(account["equity"], entry, stop, self.config, portfolio)
            if not sizing["passed"] or Decimal(order["quantity"]) > Decimal(sizing["quantity"]):
                order.update(status="cancelled", reason="Fill-time risk validation failed: " + "; ".join(sizing["reasons"] or ["requested quantity exceeds the current risk allowance"]), cancelled_at=when)
                continue
            position = self._open(order, entry, when)
            if any(item.get("order_id") == order["id"] for item in positions):
                raise ValueError("Order already has a position; reconcile before any further simulation")
            order.update(status="filled", filled_at=when, fill_price=_money(entry), position_id=position["id"])
            positions.append(position)
            position["current_price"] = _money(opening_price)
            position["unrealized_pnl"] = _money((opening_price-entry) * (ONE if order["direction"] == "long" else -ONE) * Decimal(position["quantity"]) * self.multiplier)
            account["balance"] = _money(Decimal(account["balance"]) - Decimal(position["entry_costs"]))
            self._mark(account, positions, {"NQ": opening_price}, when)
        for position in positions:
            if position.get("symbol") != "NQ":
                raise ValueError("ES cannot be a simulated position; it is SMT confirmation only")
            if position.get("last_bar_time") and instant <= timestamp(position["last_bar_time"]):
                continue
            position["last_bar_time"] = when
            long = position["direction"] == "long"
            low, high, opening, closing = (Decimal(bar[key]) for key in ("low", "high", "open", "close"))
            stop, target, entry = Decimal(position["stop_loss"]), Decimal(position["take_profit"]), Decimal(position["entry_price"])
            quantity = Decimal(position["quantity"])
            stop_hit, target_hit = (low <= stop, high >= target) if long else (high >= stop, low <= target)
            if stop_hit:
                self._exit(account, position, quantity, min(stop, opening) if long else max(stop, opening), when, "stop_loss", closed)
            elif target_hit:
                self._exit(account, position, quantity, target, when, "take_profit", closed)
            else:
                initial_distance = abs(entry - Decimal(position["initial_stop_loss"]))
                partial_r = decimal_value(self.config.get("partial_take_profit_r", 0), "partial R", nonnegative=True)
                partial_level = entry + (ONE if long else -ONE) * initial_distance * partial_r
                if partial_r and not position["partial_taken"] and (high >= partial_level if long else low <= partial_level):
                    fraction = decimal_value(self.config.get("partial_fraction", ".5"), "partial fraction", positive=True)
                    if fraction >= 1:
                        raise ValueError("Partial fraction must be smaller than one")
                    partial_quantity = (quantity * fraction).to_integral_value(rounding=ROUND_FLOOR)
                    if partial_quantity > 0:
                        self._exit(account, position, partial_quantity, partial_level, when, "partial_take_profit", closed)
                        position["partial_taken"] = True
                # Stops move only after this complete bar and apply next bar.
                break_even = decimal_value(self.config.get("break_even_r", 0), "break-even R", nonnegative=True)
                favourable = closing - entry if long else entry - closing
                if break_even and favourable >= break_even * initial_distance:
                    stop = max(stop, entry) if long else min(stop, entry)
                trailing = decimal_value(self.config.get("trailing_points", 0), "trailing distance", nonnegative=True)
                if trailing and favourable > 0:
                    candidate = closing - trailing if long else closing + trailing
                    # Round protective stops outwards to a tradable tick.
                    ticks = (candidate / self.tick).to_integral_value(rounding=ROUND_FLOOR)
                    candidate = ticks * self.tick if long else (ticks if candidate == ticks*self.tick else ticks+1) * self.tick
                    stop = max(stop, candidate) if long else min(stop, candidate)
                position["stop_loss"] = _money(stop)
        positions = [position for position in positions if position["status"] == "open"]
        self._mark(account, positions, {"NQ": Decimal(bar["close"])}, when)
        return {"account": account, "orders": orders, "positions": positions, "closed_trades": closed}

    @staticmethod
    def cancel_pending(orders):
        result = deepcopy(orders)
        for order in result:
            if order.get("status") == "pending":
                order.update(status="cancelled", reason="Explicit paper-order cancellation")
        return result

    def close_positions(self, account, positions, prices, reason="emergency"):
        account, positions = deepcopy(account), deepcopy(positions)
        self._unique(positions, "Positions")
        closed, observed_prices, when = [], {}, None
        for position in positions:
            if position.get("symbol") != "NQ":
                raise ValueError("Only independent NQ paper positions may be closed")
            quote = prices.get(position["symbol"])
            if not isinstance(quote, dict):
                raise ValueError("Closing needs an observed price and aware time, not an invented quote")
            price = self._price(quote.get("price", quote.get("close")), "observed close price")
            instant = timestamp(quote.get("time"))
            if instant < timestamp(position["entry_time"]) or (account.get("as_of") and instant < timestamp(account["as_of"])):
                raise ValueError("A stale quote cannot close current simulated positions")
            when = instant.isoformat()
            observed_prices[position["symbol"]] = price
            self._exit(account, position, Decimal(position["quantity"]), price, when, reason, closed)
        when = when or account.get("as_of")
        self._mark(account, [], observed_prices, when)
        return {"account": account, "positions": [], "closed_trades": closed}
