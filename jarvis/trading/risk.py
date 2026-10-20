"""Decimal, paper-only NQ position sizing with limits independent of the model."""
from decimal import Decimal, InvalidOperation, ROUND_FLOOR


DEFAULTS = {
    'symbol': 'NQ', 'risk_pct': '.5', 'daily_loss_pct': '2', 'weekly_loss_pct': '5',
    'max_positions': 2, 'max_exposure_pct': '200', 'contract_multiplier': '20',
    'tick_size': '.25', 'quantity_step': '1', 'spread': '0', 'slippage': '0',
    'commission_per_contract': '0', 'max_spread': '2', 'max_slippage': '2',
}
TERMINAL_STATUSES = {'closed', 'cancelled', 'canceled', 'rejected', 'expired', 'filled_closed'}


def decimal(value, label, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(f'{label} must be a finite number')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f'{label} must be a finite number') from None
    if not result.is_finite() or (positive and result <= 0) or (nonnegative and result < 0):
        raise ValueError(f'{label} must be a finite {"positive" if positive else "nonnegative" if nonnegative else ""} number')
    if result.adjusted() > 18 or result.as_tuple().exponent < -18 or len(result.as_tuple().digits) > 24:
        raise ValueError(f'{label} exceeds supported numeric precision')
    return result


def number(value):
    """Stable non-exponential representation for JSON and SQLite."""
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def _active(entries, label):
    if not isinstance(entries, list) or len(entries) > 10000 or any(not isinstance(item, dict) for item in entries):
        raise ValueError(f'{label} must be a bounded list of position/order records')
    return [item for item in entries if str(item.get('status', 'open')).lower() not in TERMINAL_STATUSES]


def _position_exposure(position, multiplier):
    quantity = position.get('quantity', position.get('size'))
    price = next((position[key] for key in ('current_price', 'mark_price', 'entry_price', 'entry')
                  if position.get(key) is not None), None)
    if quantity is None or price is None:
        return None
    if position.get('contract_multiplier') is not None:
        multiplier = decimal(position['contract_multiplier'], 'Position multiplier', positive=True)
    elif str(position.get('symbol', '')).upper() != 'NQ':
        return None
    return abs(decimal(quantity, 'Position quantity')) * decimal(price, 'Position price', positive=True) * multiplier


def size_position(equity, entry, stop_loss, config, portfolio=None):
    """Return integer NQ contracts or a rejected decision; never submit an order.

    Spread/slippage are points and commission is per contract per side. Daily
    and weekly losses are nonnegative currency amounts, including any losses
    the caller reserves for existing positions. Missing position exposure is
    a blocker rather than an assumption that an existing position costs zero.
    """
    if not isinstance(config, dict):
        raise ValueError('Risk configuration must be an object')
    if portfolio is not None and not isinstance(portfolio, dict):
        raise ValueError('Portfolio must be an object')
    cfg, portfolio = {**DEFAULTS, **config}, portfolio or {}
    equity = decimal(equity, 'Account equity', positive=True)
    entry = decimal(entry, 'Entry price', positive=True)
    stop = decimal(stop_loss, 'Stop-loss price', positive=True)
    multiplier = decimal(cfg['contract_multiplier'], 'Contract multiplier', positive=True)
    tick = decimal(cfg['tick_size'], 'Tick size', positive=True)
    step = decimal(cfg['quantity_step'], 'Quantity step', positive=True)
    if cfg['symbol'] != 'NQ' or multiplier != 20 or tick != Decimal('.25') or step != 1:
        raise ValueError('Only NQ E-mini contracts are enabled: multiplier 20, tick 0.25, quantity step 1')
    percentages = {name: decimal(cfg[name], name, positive=True) for name in
                   ('risk_pct', 'daily_loss_pct', 'weekly_loss_pct', 'max_exposure_pct')}
    if any(percentages[name] > 100 for name in ('risk_pct', 'daily_loss_pct', 'weekly_loss_pct')):
        raise ValueError('Risk and loss percentages must not exceed 100')
    if percentages['max_exposure_pct'] > 100000:
        raise ValueError('Maximum exposure percentage is too large')
    maximum = cfg['max_positions']
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 100:
        raise ValueError('Maximum positions must be an integer from 1 to 100')
    costs = {name: decimal(cfg[name], name, nonnegative=True) for name in
             ('spread', 'slippage', 'commission_per_contract', 'max_spread', 'max_slippage')}
    positions = _active(portfolio.get('positions', []), 'Positions')
    pending = _active(portfolio.get('pending_orders', []), 'Pending orders')
    reasons = []
    if entry == stop:
        reasons.append('Stop-loss must differ from the entry price.')
    if entry % tick or stop % tick:
        reasons.append('NQ entry and stop-loss must align to the 0.25-point tick.')
    if costs['spread'] > costs['max_spread']:
        reasons.append('Observed spread exceeds the configured maximum.')
    if costs['slippage'] > costs['max_slippage']:
        reasons.append('Expected slippage exceeds the configured maximum.')
    if len(positions) + len(pending) >= maximum:
        reasons.append('Maximum simultaneous positions and pending entries reached.')
    for position in positions:
        if str(position.get('symbol', '')).upper() != 'NQ':
            continue
        values = [decimal(position[key], 'Position P/L') for key in ('unrealized_pnl', 'net_pnl', 'pnl')
                  if position.get(key) is not None]
        if not values:
            reasons.append('Existing NQ position has no verified P/L; adding exposure is blocked.')
        elif min(values) < 0:
            reasons.append('Adding to a losing NQ position is disabled.')
    daily_loss = decimal(portfolio.get('daily_loss', 0), 'Daily loss', nonnegative=True)
    weekly_loss = decimal(portfolio.get('weekly_loss', 0), 'Weekly loss', nonnegative=True)
    day_equity = decimal(portfolio.get('day_start_equity', equity), 'Day-start equity', positive=True)
    week_equity = decimal(portfolio.get('week_start_equity', equity), 'Week-start equity', positive=True)
    daily_budget = day_equity * percentages['daily_loss_pct'] / 100
    weekly_budget = week_equity * percentages['weekly_loss_pct'] / 100
    if daily_loss >= daily_budget:
        reasons.append('Daily loss limit reached; new entries are suspended.')
    if weekly_loss >= weekly_budget:
        reasons.append('Weekly loss limit reached; new entries are suspended.')
    risk_budget = equity * percentages['risk_pct'] / 100
    available_risk = max(Decimal(0), min(risk_budget, daily_budget-daily_loss, weekly_budget-weekly_loss))
    unit_risk = abs(entry-stop) * multiplier + (costs['spread'] + 2*costs['slippage']) * multiplier + 2*costs['commission_per_contract']
    exposure_limit = equity * percentages['max_exposure_pct'] / 100
    if portfolio.get('exposure_notional') is not None:
        exposure = decimal(portfolio['exposure_notional'], 'Current exposure', nonnegative=True)
    else:
        exposures = [_position_exposure(item, multiplier) for item in positions+pending]
        if any(value is None for value in exposures):
            reasons.append('Existing position/order exposure is unavailable; new entries are suspended.')
        exposure = sum((value for value in exposures if value is not None), Decimal(0))
    remaining_exposure = max(Decimal(0), exposure_limit-exposure)
    risk_quantity = (available_risk/unit_risk).to_integral_value(rounding=ROUND_FLOOR) if unit_risk else Decimal(0)
    exposure_quantity = (remaining_exposure/(entry*multiplier)).to_integral_value(rounding=ROUND_FLOOR)
    quantity = min(risk_quantity, exposure_quantity)
    if quantity < 1:
        if risk_quantity < 1:
            reasons.append('Risk budget is insufficient for one integer NQ contract at this stop and cost estimate.')
        if exposure_quantity < 1:
            reasons.append('Remaining exposure allowance is insufficient for one NQ contract.')
    if reasons:
        quantity = Decimal(0)
    return {'passed': not reasons, 'quantity': number(quantity),
            'risk_amount': number(quantity*unit_risk), 'risk_budget': number(risk_budget),
            'available_risk_budget': number(available_risk), 'unit_risk': number(unit_risk),
            'exposure_notional': number(exposure), 'proposed_exposure': number(quantity*entry*multiplier),
            'exposure_limit': number(exposure_limit), 'reasons': list(dict.fromkeys(reasons))}
