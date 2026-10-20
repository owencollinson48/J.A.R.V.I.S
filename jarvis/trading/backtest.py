"""Deterministic candle replay. Signal decisions never see a later candle.

This is a model of execution on supplied data, not TradingView order history.
Unknown intrabar ordering is resolved against the position, rather than choosing
the profitable sequence. Fees and friction are charged explicitly in P/L.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from zoneinfo import ZoneInfo

from .analytics import analyze
from . import strategy, risk


def dec(value):
    if isinstance(value, bool): raise ValueError('Financial values must be finite numbers')
    try: number = Decimal(str(value))
    except Exception: raise ValueError('Financial values must be finite numbers') from None
    if not number.is_finite(): raise ValueError('Financial values must be finite numbers')
    if number.adjusted()>18 or number.as_tuple().exponent < -18 or len(number.as_tuple().digits)>24:
        raise ValueError('Financial value exceeds supported numeric precision')
    return number


def stamp(value, *, end=False):
    if isinstance(value, datetime): parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if len(value) == 10:
                parsed = parsed.replace(tzinfo=timezone.utc)
                if end: parsed += timedelta(days=1, microseconds=-1)
        except ValueError: raise ValueError('Use an ISO date or a timezone-aware timestamp') from None
    else: raise ValueError('Use an ISO date or a timezone-aware timestamp')
    if parsed.tzinfo is None: raise ValueError('Candle timestamps must include a timezone')
    return parsed.astimezone(timezone.utc)


def normalized(candles):
    if not isinstance(candles, list) or len(candles) > 50000:
        raise ValueError('Supply at most 50,000 observed candles per backtest')
    result = []
    for candle in candles:
        if not isinstance(candle, dict): raise ValueError('Each candle must be an OHLC record')
        if candle.get('closed', True) is not True: continue
        try:
            when = stamp(candle['time'])
            values = {key: dec(candle[key]) for key in ('open','high','low','close')}
        except KeyError: raise ValueError('Candle time, open, high, low and close are required') from None
        if (min(values.values()) <= 0 or values['high'] < max(values.values()) or
                values['low'] > min(values.values())):
            raise ValueError('Candle prices are inconsistent')
        result.append({**candle, 'time':when.isoformat(), **{key:str(value) for key,value in values.items()}, 'closed':True})
    result.sort(key=lambda candle: candle['time'])
    if len({candle['time'] for candle in result}) != len(result):
        raise ValueError('Duplicate candle times must be reconciled before a backtest')
    return result


def entry_session(when, config):
    """Check the execution time too; a valid signal may expire before a fill."""
    for item in config['sessions']:
        local = when.astimezone(ZoneInfo(item['timezone']))
        hour, minute = (int(part) for part in item['open'].split(':'))
        # A pre-open interval can begin on the previous local calendar day.
        for offset in (0, 1):
            day = local.date() + timedelta(days=offset)
            if day.weekday() not in item.get('weekdays', [0,1,2,3,4]): continue
            opening = datetime.combine(day, datetime.min.time(), tzinfo=local.tzinfo).replace(hour=hour,minute=minute)
            if (opening-timedelta(minutes=config['pre_open_minutes']) <= local <=
                    opening+timedelta(minutes=config['post_open_minutes'])):
                return item['name']
    return None


def round_tick(price, tick, upward=False):
    return (price/tick).to_integral_value(rounding=ROUND_CEILING if upward else ROUND_FLOOR)*tick


def friction(config):
    multiplier = dec(config['contract_multiplier'])
    return ((dec(config.get('spread',0))+2*dec(config.get('slippage',0)))*multiplier +
            2*dec(config.get('commission_per_contract',0)))


def run_backtest(candles, config, *, correlated=None, initial_equity='100000', start=None, end=None, strategy_version='V1'):
    """Replay completed bars, generating orders at a close and filling later.

    A limit is filled on the first subsequent bar that touches its price. Market
    and stop entries are supported for explicit deterministic test adapters.
    Historical data is not used to establish a live market-data connection.
    """
    validator = getattr(strategy, 'validate_config', None) or getattr(risk, 'validate_config', None)
    config = validator(config) if validator else dict(config)
    if config.get('symbol','NQ') != 'NQ': raise ValueError('Only NQ is traded; ES is an SMT reference only')
    balance = initial = dec(initial_equity)
    if initial <= 0: raise ValueError('Initial equity must be positive')
    data, paired = normalized(candles), normalized(correlated or [])
    if any(bar.get('symbol','NQ') != 'NQ' for bar in data):
        raise ValueError('Primary history must be NQ; ES is an SMT reference only')
    if any(bar.get('symbol','ES') != 'ES' for bar in paired):
        raise ValueError('SMT reference history must be ES')
    begin, finish = stamp(start) if start else None, stamp(end,end=True) if end else None
    if begin and finish and begin > finish: raise ValueError('Backtest start must precede its end')
    selected = [(index, candle) for index,candle in enumerate(data)
                if (begin is None or stamp(candle['time']) >= begin) and
                   (finish is None or stamp(candle['time']) <= finish)]
    multiplier, step = dec(config['contract_multiplier']), dec(config['quantity_step'])
    bar_duration = timedelta(minutes=config['timeframe_minutes'])
    history_size = strategy.required_history(config)
    paired_cursor = 0
    positions, pending, trades, rejected, seen = [], [], [], [], set()
    period_days, period_weeks = {}, {}
    stop_before_target = 'When a bar touches both stop and target, the stop fills first.'
    assumptions = [
        'Backtest simulation on supplied completed candles; no TradingView orders are submitted.',
        'Timestamps represent candle CLOSE times in UTC. Missing candles are not invented.',
        'Signals are evaluated at a completed close. Orders cannot fill before the next observed candle.',
        stop_before_target,
        'Stops gap adversely to the observed open; favourable take-profit gaps fill at the target.',
        'Spread plus two one-way slippages and round-trip commissions are explicit trading costs.',
        'Break-even/trailing adjustments based on a bar take effect on the following bar.',
        'Limit/stop fills require the whole candle to remain inside an entry session when the touch time is unknown.',
        'Open positions are liquidated at the last supplied close, with costs, for the reported terminal equity.',
        'Daily/weekly loss baselines reset at observed UTC calendar day/ISO week boundaries.',
    ]

    def mark_equity(price):
        return balance + sum((price-p['entry'])*p['sign']*multiplier*p['remaining'] for p in positions)

    def close_piece(position, price, quantity, candle, reason):
        nonlocal balance
        gross = (price-position['entry'])*position['sign']*multiplier*quantity
        costs = friction(config)*quantity
        net = gross-costs
        balance += net
        position['gross'] += gross; position['costs'] += costs; position['net'] += net
        position['remaining'] -= quantity
        position['exit_weighted'] += price*quantity
        position['exits'].append({'time':candle['time'],'price':str(price),'quantity':str(quantity),
                                  'gross_pnl':str(gross),'costs':str(costs),'net_pnl':str(net),'reason':reason})
        if position['remaining'] > 0: return
        setup = position['setup']; duration = (stamp(candle['time'])-position['entry_time']).total_seconds()
        planned = abs(position['target']-position['entry'])/abs(position['entry']-position['initial_stop'])
        record = {**setup, 'id':position['id'],'setup_id':setup['setup_id'],'symbol':'NQ','instrument':'NQ',
                  'dataset':'backtest','status':'closed','strategy_version':strategy_version,
                  'entry_price':str(position['entry']),'exit_price':str(position['exit_weighted']/position['quantity']),
                  'entry_time':position['entry_time'].isoformat(),'exit_time':candle['time'],
                  'stop_loss':str(position['initial_stop']),'take_profit':str(position['target']),
                  'quantity':str(position['quantity']),'market_session':position['session'],
                  'planned_rr':str(planned),'realised_r':str(position['net']/position['risk_amount']) if position['risk_amount'] else None,
                  'gross_pnl':str(position['gross']),'costs':str(position['costs']),'net_pnl':str(position['net']),
                  'duration_seconds':duration,'exit_reason':reason,'exits':position['exits'],
                  'setup_type':setup.get('entry_type') or setup.get('evidence',{}).get('entry_type') or setup.get('setup_type','continuation')}
        trades.append(record)
        positions.remove(position)

    def manage(position, candle):
        low, high, opened = (dec(candle[key]) for key in ('low','high','open'))
        long = position['sign'] == 1
        stopped = low <= position['stop'] if long else high >= position['stop']
        target = high >= position['target'] if long else low <= position['target']
        if stopped:
            price = min(opened,position['stop']) if long else max(opened,position['stop'])
            close_piece(position,price,position['remaining'],candle,position.get('stop_reason','stop_loss'))
            return
        excursion = (high-position['entry']) if long else (position['entry']-low)
        distance = abs(position['entry']-position['initial_stop'])
        partial_r = dec(config.get('partial_take_profit_r',0))
        if partial_r > 0 and not position['partial_done'] and excursion >= partial_r*distance:
            quantity = (position['quantity']*dec(config.get('partial_fraction','.5'))/step).to_integral_value(rounding=ROUND_FLOOR)*step
            quantity = min(quantity,position['remaining'])
            if quantity > 0:
                price = position['entry']+position['sign']*partial_r*distance
                close_piece(position,price,quantity,candle,'partial_take_profit')
            position['partial_done'] = True
            if position not in positions: return
        if target:
            close_piece(position,position['target'],position['remaining'],candle,'take_profit')
            return
        break_even = dec(config.get('break_even_r',0))
        if break_even > 0 and excursion >= break_even*distance:
            price = position['entry']+position['sign']*friction(config)/multiplier
            price = round_tick(price,dec(config['tick_size']),upward=long)
            if (long and price > position['stop']) or (not long and price < position['stop']):
                position['stop'],position['stop_reason'] = price,'break_even_stop'
        trailing = dec(config.get('trailing_points',0))
        if trailing > 0:
            price = round_tick(high-trailing if long else low+trailing,dec(config['tick_size']),upward=not long)
            if (long and price > position['stop']) or (not long and price < position['stop']):
                position['stop'],position['stop_reason'] = price,'trailing_stop'

    for index,candle in selected:
        when, opened = stamp(candle['time']), dec(candle['open'])
        open_time = when-bar_duration
        prior_price = dec(data[index-1]['close']) if index else opened
        equity_before = mark_equity(prior_price)
        day, week = open_time.date().isoformat(), open_time.isocalendar()[:2]
        day_start = period_days.setdefault(day,equity_before)
        week_start = period_weeks.setdefault(week,equity_before)
        for order in list(pending):
            setup = order['setup']; long = setup['direction']=='long'
            session = entry_session(open_time,config)
            order_type = setup.get('order_type','limit')
            if not session or (order_type!='market' and entry_session(when,config)!=session):
                rejected.append({'setup_id':setup['setup_id'],'time':candle['time'],'reason':'Entry session expired before a confirmed fill','conditions':setup.get('conditions',{})})
                pending.remove(order); continue
            wanted = dec(setup['entry']); price = None
            if order_type=='market': price = opened
            elif order_type=='limit' and ((long and dec(candle['low'])<=wanted) or (not long and dec(candle['high'])>=wanted)):
                price = min(opened,wanted) if long else max(opened,wanted)
            elif order_type=='stop' and ((long and dec(candle['high'])>=wanted) or (not long and dec(candle['low'])<=wanted)):
                price = max(opened,wanted) if long else min(opened,wanted)
            if price is None: continue
            pending.remove(order)
            stop, target = dec(setup['stop_loss']), dec(setup['take_profit'])
            if not (stop < price < target if long else target < price < stop):
                rejected.append({'setup_id':setup['setup_id'],'time':candle['time'],'reason':'Observed fill crossed the setup stop or target'}); continue
            # A close later in this bar cannot finance an order at its open.
            # A second order also cannot use a same-bar limit fill's notional
            # opening profit, because the open preceded that entry.
            equity = balance + sum((opened-p['entry'])*p['sign']*multiplier*p['remaining']
                                   for p in positions if p.get('entry_bar') != candle['time'])
            if equity <= 0:
                rejected.append({'setup_id':setup['setup_id'],'time':candle['time'],'reason':'Account equity is nonpositive; new entries are suspended'})
                continue
            portfolio = {'positions':[{'symbol':'NQ','status':'open','quantity':str(p['remaining']),
                         'entry_price':str(p['entry']),
                         'current_price':str(p['entry'] if p.get('entry_bar')==candle['time'] else opened),
                         'unrealized_pnl':str(Decimal(0) if p.get('entry_bar')==candle['time'] else
                                             (opened-p['entry'])*p['sign']*multiplier*p['remaining'])} for p in positions],
                         'pending_orders':[], 'daily_loss':str(max(Decimal(0),day_start-equity)),
                         'weekly_loss':str(max(Decimal(0),week_start-equity)),
                         'day_start_equity':str(day_start),'week_start_equity':str(week_start)}
            sized = risk.size_position(str(equity),str(price),str(stop),config,portfolio)
            if not sized.get('passed'):
                rejected.append({'setup_id':setup['setup_id'],'time':candle['time'],'reason':'Risk check failed',
                                 'reasons':sized.get('reasons',[]),'conditions':setup.get('conditions',{})}); continue
            quantity = dec(sized['quantity'])
            position = {'id':setup['setup_id']+'-bt','setup':setup,'entry':price,'entry_time':open_time,'entry_bar':candle['time'],
                        'stop':stop,'initial_stop':stop,'target':target,'sign':Decimal(1 if long else -1),
                        'quantity':quantity,'remaining':quantity,'risk_amount':dec(sized['risk_amount']),
                        'gross':Decimal(0),'costs':Decimal(0),'net':Decimal(0),'exit_weighted':Decimal(0),
                        'exits':[],'session':session,'partial_done':False}
            positions.append(position)
        for position in list(positions): manage(position,candle)
        while paired_cursor < len(paired) and paired[paired_cursor]['time'] <= candle['time']:
            paired_cursor += 1
        paired_prefix = paired[max(0,paired_cursor-history_size):paired_cursor]
        setup = strategy.evaluate(data[max(0,index+1-history_size):index+1],config,correlated=paired_prefix)
        if not isinstance(setup,dict): raise ValueError('Strategy must return a deterministic setup record')
        if not setup.get('passed'):
            rejected.append({'time':candle['time'],'setup_id':setup.get('setup_id'),
                             'reason':'Mandatory strategy conditions were not satisfied',
                             'rating':setup.get('rating',0),'conditions':setup.get('conditions',{})})
            continue
        if setup.get('symbol','NQ') != 'NQ' or setup.get('direction') not in {'long','short'}:
            rejected.append({'time':candle['time'],'reason':'Only validated NQ long/short setups are eligible'}); continue
        identity = setup.get('setup_id')
        if not isinstance(identity,str) or not identity: raise ValueError('A validated setup requires a unique setup_id')
        if setup.get('order_type','limit') not in {'limit','market','stop'}:
            rejected.append({'time':candle['time'],'setup_id':identity,'reason':'Unsupported order type'}); continue
        if identity not in seen:
            seen.add(identity); pending.append({'setup':setup})
    if selected:
        last = selected[-1][1]
        for position in list(positions): close_piece(position,dec(last['close']),position['remaining'],last,'end_of_data')
    report = analyze(trades,str(initial))
    actual_start,actual_end = (selected[0][1]['time'],selected[-1][1]['time']) if selected else (None,None)
    gaps = sum(stamp(right['time'])-stamp(left['time'])>bar_duration for left,right in zip(data,data[1:]))
    coverage = {'available_start':data[0]['time'] if data else None,'available_end':data[-1]['time'] if data else None,
                'requested_start':start,'requested_end':end,'actual_start':actual_start,'actual_end':actual_end,
                'available_candles':len(data),'tested_candles':len(selected),'warmup_candles':selected[0][0] if selected else 0,
                'correlated_candles':len(paired),'gaps':gaps,
                'complete_requested_period':bool(selected and (begin is None or stamp(data[0]['time'])<=begin) and (finish is None or stamp(data[-1]['time'])>=finish))}
    if gaps: assumptions.append('Observed timestamp gaps remain gaps; fills can only occur on an observed later bar.')
    return {'dataset':'backtest','symbol':'NQ','strategy_version':strategy_version,
            'initial_equity':str(initial),'ending_equity':str(balance),'trades':trades,'rejected':rejected,
            'metrics':report['metrics'],'charts':report['charts'],'equity_curve':report['equity_curve'],
            'coverage':coverage,'assumptions':assumptions+report.get('assumptions',[]),
            'unfilled_orders':[{'setup_id':order['setup']['setup_id'],'reason':'No observed later eligible fill'} for order in pending]}
