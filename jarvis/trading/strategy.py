"""Completed-candle NQ strategy rules, independent of model or broker decisions.

All timestamps describe candle CLOSE times. Built higher timeframes require
every constituent bar; partial hours and partial sessions never become levels.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from hashlib import sha256
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_CONFIG = {
    'symbol':'NQ', 'smt_symbol':'ES', 'timeframe_minutes':5,
    'swing_sensitivity':1, 'sweep_lookback':48, 'bos_lookback':12,
    'max_setup_age_bars':12, 'entry_types':['fvg','ifvg','equilibrium'],
    'fvg_entry_fraction':.5, 'min_fvg_ticks':1, 'stop_buffer_ticks':1,
    'equilibrium_tolerance_ticks':1, 'require_rsi':False, 'require_smt':False,
    'rsi_period':14, 'rsi_divergence_lookback':20, 'smt_lookback':12, 'rr':2.5,
    'sessions':[
        {'name':'London','timezone':'Europe/London','open':'08:00','weekdays':[0,1,2,3,4],'duration_minutes':480},
        {'name':'New York','timezone':'America/New_York','open':'09:30','weekdays':[0,1,2,3,4],'duration_minutes':390}],
    'pre_open_minutes':30, 'post_open_minutes':120,
    'risk_pct':.5, 'daily_loss_pct':2, 'weekly_loss_pct':5, 'max_positions':2,
    'max_exposure_pct':200, 'contract_multiplier':20, 'tick_size':.25,
    'quantity_step':1, 'spread':0, 'slippage':0, 'commission_per_contract':0,
    'max_spread':2, 'max_slippage':2, 'break_even_r':0, 'trailing_points':0,
    'partial_take_profit_r':0, 'partial_fraction':.5, 'max_data_age_seconds':900,
}


def decimal(value, name='value'):
    if isinstance(value, bool): raise ValueError(f'{name} must be a finite number')
    try: result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f'{name} must be a finite number') from None
    if not result.is_finite(): raise ValueError(f'{name} must be a finite number')
    if result.adjusted()>18 or result.as_tuple().exponent < -18 or len(result.as_tuple().digits)>24:
        raise ValueError(f'{name} exceeds supported numeric precision')
    return result


def validate_config(changes, base=None):
    """Return a fresh full configuration; reject unknown or unsafe values."""
    if not isinstance(changes, dict) or (base is not None and not isinstance(base, dict)):
        raise ValueError('Strategy configuration must be an object')
    config = deepcopy(DEFAULT_CONFIG)
    for values in (base or {}, changes):
        if set(values)-set(DEFAULT_CONFIG): raise ValueError('Unknown trading configuration field: '+', '.join(sorted(map(str,set(values)-set(DEFAULT_CONFIG)))))
        config.update(deepcopy(values))
    if config['symbol'] != 'NQ' or config['smt_symbol'] != 'ES':
        raise ValueError('Only NQ may be traded; ES is reserved for SMT confirmation')
    integers = {
        'timeframe_minutes':(1,60), 'swing_sensitivity':(1,10), 'sweep_lookback':(3,5000),
        'bos_lookback':(2,500), 'max_setup_age_bars':(1,100), 'rsi_period':(2,100),
        'rsi_divergence_lookback':(4,500), 'smt_lookback':(3,500),
        'pre_open_minutes':(0,120), 'post_open_minutes':(1,480),
        'max_positions':(1,20), 'max_data_age_seconds':(1,86400),
    }
    for name, (low,high) in integers.items():
        value = config[name]
        if type(value) is not int or not low<=value<=high: raise ValueError(f'{name} must be an integer between {low} and {high}')
    if 60 % config['timeframe_minutes']: raise ValueError('timeframe_minutes must divide one hour exactly')
    bounds = {
        'fvg_entry_fraction':(0,1), 'min_fvg_ticks':(0,100), 'stop_buffer_ticks':(0,20),
        'equilibrium_tolerance_ticks':(0,20), 'rr':(.1,20), 'risk_pct':(.01,5),
        'daily_loss_pct':(.01,50), 'weekly_loss_pct':(.01,100), 'max_exposure_pct':(.01,10000),
        'spread':(0,100), 'slippage':(0,100), 'commission_per_contract':(0,1000),
        'max_spread':(0,100), 'max_slippage':(0,100), 'break_even_r':(0,20),
        'trailing_points':(0,10000), 'partial_take_profit_r':(0,20), 'partial_fraction':(.01,1),
    }
    for name, (low,high) in bounds.items():
        value = decimal(config[name],name)
        if not decimal(low)<=value<=decimal(high): raise ValueError(f'{name} must be between {low} and {high}')
        config[name] = float(value)
    for name, expected in (('contract_multiplier',20),('tick_size',.25),('quantity_step',1)):
        if decimal(config[name],name) != decimal(expected): raise ValueError(f'NQ requires {name}={expected}')
        config[name] = expected
    for name in ('require_rsi','require_smt'):
        if type(config[name]) is not bool: raise ValueError(f'{name} must be true or false')
    if (not isinstance(config['entry_types'],list) or not config['entry_types'] or
            len(config['entry_types'])>3 or any(not isinstance(value,str) or value not in {'fvg','ifvg','equilibrium'} for value in config['entry_types']) or
            len(set(config['entry_types']))!=len(config['entry_types'])):
        raise ValueError('entry_types must contain unique fvg, ifvg or equilibrium choices')
    if config['partial_take_profit_r'] and config['partial_take_profit_r']>=config['rr']:
        raise ValueError('A partial take-profit must precede the final R target')
    sessions = config['sessions']
    if not isinstance(sessions,list) or not 1<=len(sessions)<=8: raise ValueError('Configure between one and eight market sessions')
    names = set()
    for session in sessions:
        if not isinstance(session,dict) or set(session)-{'name','timezone','open','weekdays','duration_minutes'}:
            raise ValueError('Session fields are name, timezone, open, weekdays and optional duration_minutes')
        if not all(key in session for key in ('name','timezone','open','weekdays')): raise ValueError('A session needs name, timezone, open and weekdays')
        name = session['name']
        if not isinstance(name,str) or not name.strip() or len(name)>60 or name in names: raise ValueError('Session names must be unique and nonempty')
        names.add(name)
        try: ZoneInfo(session['timezone'])
        except (ZoneInfoNotFoundError,ValueError,TypeError): raise ValueError('Use a valid IANA market timezone') from None
        try:
            hour, minute = session['open'].split(':')
            if len(hour)!=2 or len(minute)!=2 or not (0<=int(hour)<=23 and 0<=int(minute)<=59): raise ValueError
        except (AttributeError,ValueError): raise ValueError('Session open must be HH:MM') from None
        weekdays = session['weekdays']
        if not isinstance(weekdays,list) or not weekdays or any(type(day) is not int or not 0<=day<=6 for day in weekdays) or len(set(weekdays))!=len(weekdays):
            raise ValueError('Session weekdays must be unique integers from 0 to 6')
        duration = session.setdefault('duration_minutes',480)
        if type(duration) is not int or not 1<=duration<=1440: raise ValueError('Session duration_minutes must be between 1 and 1440')
    return config


def _time(value):
    try: result = datetime.fromisoformat(str(value).replace('Z','+00:00'))
    except ValueError: raise ValueError('Candle time must be an ISO datetime with timezone') from None
    if result.tzinfo is None or result.utcoffset() is None: raise ValueError('Candle time must include a timezone')
    return result.astimezone(timezone.utc)


def _candles(values):
    if not isinstance(values,(list,tuple)): raise ValueError('Candles must be an ordered array')
    result = []
    previous = None
    for value in values:
        if not isinstance(value,dict): raise ValueError('Each candle must be an object')
        if type(value.get('closed',True)) is not bool: raise ValueError('Candle closed must be true or false')
        if not value.get('closed',True): continue
        stamp = _time(value.get('time'))
        if previous is not None and stamp<=previous: raise ValueError('Completed candle times must be unique and ascending')
        previous = stamp
        prices = {name:decimal(value.get(name),name) for name in ('open','high','low','close')}
        volume = decimal(value['volume'],'volume') if value.get('volume') is not None else None
        if (min(prices.values())<=0 or prices['high']<max(prices['open'],prices['close']) or
                prices['low']>min(prices['open'],prices['close']) or prices['high']<prices['low'] or (volume is not None and volume<0)):
            raise ValueError('Candle OHLC prices or volume are inconsistent')
        result.append({'time':stamp.isoformat(),'dt':stamp,**prices,'volume':volume,'closed':True})
    return result


def _aggregate(bars, minutes, base_minutes):
    if minutes % base_minutes: return []
    count, width = minutes//base_minutes, minutes*60
    groups = {}
    for bar in bars:
        micros = int(bar['dt'].timestamp()*1_000_000)
        end = ((micros-1)//(width*1_000_000)+1)*width
        groups.setdefault(end,[]).append(bar)
    result=[]
    for end, group in sorted(groups.items()):
        close = datetime.fromtimestamp(end,timezone.utc)
        expected = [close-timedelta(minutes=base_minutes*(count-index-1)) for index in range(count)]
        if len(group)!=count or [bar['dt'] for bar in group]!=expected: continue
        result.append({'time':close.isoformat(),'dt':close,'open':group[0]['open'],
            'high':max(bar['high'] for bar in group),'low':min(bar['low'] for bar in group),
            'close':group[-1]['close'],'volume':None if any(bar['volume'] is None for bar in group) else sum((bar['volume'] for bar in group),Decimal(0)),'closed':True})
    return result


def aggregate_candles(candles, minutes, timeframe_minutes=5):
    """Public aggregation helper; omit incomplete buckets rather than invent bars."""
    if type(minutes) is not int or type(timeframe_minutes) is not int or minutes<=0 or timeframe_minutes<=0:
        raise ValueError('Aggregation timeframes must be positive integer minutes')
    return [{key:(str(value) if isinstance(value,Decimal) else value) for key,value in bar.items() if key!='dt'}
            for bar in _aggregate(_candles(candles),minutes,timeframe_minutes)]


def _swings(bars, sensitivity, minutes=None):
    result=[]
    for index in range(2*sensitivity-1,len(bars)):
        left=bars[index-2*sensitivity+1:index-sensitivity+1]
        right=bars[index-sensitivity+1:index+1]
        group=left+right
        if minutes and any(group[offset]['dt']-group[offset-1]['dt']!=timedelta(minutes=minutes) for offset in range(1,len(group))): continue
        high = all(bar['close']>bar['open'] for bar in left) and all(bar['close']<bar['open'] for bar in right)
        low = all(bar['close']<bar['open'] for bar in left) and all(bar['close']>bar['open'] for bar in right)
        if high or low:
            extreme=max(range(len(group)),key=lambda offset:group[offset]['high']) if high else min(range(len(group)),key=lambda offset:group[offset]['low'])
            pivot_index=index-2*sensitivity+1+extreme
            result.append({'kind':'high' if high else 'low','price':group[extreme]['high' if high else 'low'],
                'index':index,'time':bars[index]['time'],'pivot_index':pivot_index,'pivot_time':bars[pivot_index]['time']})
    return result


def session_at(stamp, config):
    """Session windows use named market zones, including DST and overnight dates."""
    stamp=_time(stamp)
    for session in config['sessions']:
        local=stamp.astimezone(ZoneInfo(session['timezone']))
        hour,minute=map(int,session['open'].split(':'))
        for offset in (-1,0,1):
            day=local.date()+timedelta(days=offset)
            opening=datetime(day.year,day.month,day.day,hour,minute,tzinfo=ZoneInfo(session['timezone']))
            start=opening.astimezone(timezone.utc)-timedelta(minutes=config['pre_open_minutes'])
            end=opening.astimezone(timezone.utc)+timedelta(minutes=config['post_open_minutes'])
            if day.weekday() in session['weekdays'] and start<=stamp<=end:
                return {'name':session['name'],'timezone':session['timezone'],'open':opening.isoformat(),
                        'window_start':start.isoformat(),'window_end':end.isoformat()}
    return None


def _session_liquidity_levels(bars,config):
    levels=[]
    by_time={bar['dt']:bar for bar in bars}
    for session in config['sessions']:
        zone=ZoneInfo(session['timezone']);hour,minute=map(int,session['open'].split(':'))
        dates={bar['dt'].astimezone(zone).date() for bar in bars}
        for date in dates:
            if date.weekday() not in session['weekdays'] or session['duration_minutes']%config['timeframe_minutes']: continue
            start=datetime(date.year,date.month,date.day,hour,minute,tzinfo=zone).astimezone(timezone.utc)
            count=session['duration_minutes']//config['timeframe_minutes']
            times=[start+timedelta(minutes=config['timeframe_minutes']*(index+1)) for index in range(count)]
            if not all(stamp in by_time for stamp in times): continue
            block=[by_time[stamp] for stamp in times];end=times[-1]
            for kind in ('high','low'):
                levels.append({'price':max(bar['high'] for bar in block) if kind=='high' else min(bar['low'] for bar in block),
                    'kind':kind,'timeframe':'session','source':session['name'],'time':end.isoformat(),'dt':end})
    return levels


def _liquidity(bars, moment, config, higher, session_levels):
    levels=[]
    for label in ('1h','4h'):
        completed=[bar for bar in higher[label] if bar['dt']<=moment]
        if not completed: continue
        last=completed[-1]
        for kind in ('high','low'):
            levels.append({'price':last[kind],'kind':kind,'timeframe':label,'source':'previous_completed_bar','time':last['time']})
        for pivot in _swings(completed[-config['sweep_lookback']:],config['swing_sensitivity'],60 if label=='1h' else 240)[-4:]:
            levels.append({'price':pivot['price'],'kind':pivot['kind'],'timeframe':label,'source':'confirmed_swing','time':pivot['time']})
    current={}
    for level in session_levels:
        key=(level['source'],level['kind'])
        if level['dt']<=moment and (key not in current or current[key]['dt']<level['dt']): current[key]=level
    levels.extend(current.values())
    unique={}
    for level in levels: unique[(level['kind'],level['price'],level['timeframe'])]=level
    return list(unique.values())


def _gaps(bars, tick, minimum, minutes):
    gaps=[]
    for index in range(2,len(bars)):
        first,last=bars[index-2],bars[index]
        if any(bars[cursor]['dt']-bars[cursor-1]['dt']!=timedelta(minutes=minutes) for cursor in (index-1,index)): continue
        if first['high']<last['low']:
            direction,lower,upper='long',first['high'],last['low']
        elif first['low']>last['high']:
            direction,lower,upper='short',last['high'],first['low']
        else: continue
        if upper-lower<tick*minimum: continue
        gaps.append({'direction':direction,'lower':lower,'upper':upper,'index':index,
                     'formed_at':last['time'],'gap_size':upper-lower})
    return gaps


def _round(value,tick,mode=ROUND_HALF_UP):
    return (value/tick).to_integral_value(rounding=mode)*tick


def _confluences(bars,sweep_index,bos_index,direction,config):
    latest=bars[-1];tick=decimal(config['tick_size']);fraction=decimal(config['fvg_entry_fraction'])
    result={'fvg':None,'ifvg':None,'equilibrium':None}
    for gap in reversed(_gaps(bars,tick,decimal(config['min_fvg_ticks']),config['timeframe_minutes'])):
        if gap['index']>=len(bars)-1: continue  # A formation candle cannot retest its own gap.
        lower,upper=gap['lower'],gap['upper']
        entry=_round(lower+(upper-lower)*fraction,tick)
        after=bars[gap['index']+1:]
        if gap['direction']==direction and gap['index']>=bos_index and result['fvg'] is None:
            deepest=min(bar['low'] for bar in after) if direction=='long' else max(bar['high'] for bar in after)
            mitigation=max(Decimal(0),min(Decimal(100),((upper-deepest) if direction=='long' else (deepest-lower))/(upper-lower)*100))
            invalid=any(bar['low']<=lower for bar in after) if direction=='long' else any(bar['high']>=upper for bar in after)
            if not invalid and latest['low']<=entry<=latest['high']:
                result['fvg']={**gap,'entry':entry,'mitigation_pct':mitigation,'valid':True}
        if gap['direction']!=direction and result['ifvg'] is None:
            inversions=[index for index in range(max(gap['index']+1,bos_index),len(bars)-1)
                        if (bars[index]['close']>upper if direction=='long' else bars[index]['close']<lower)]
            if not inversions: continue
            inverted=inversions[0]
            failed=any(bar['close']<lower if direction=='long' else bar['close']>upper for bar in bars[inverted+1:])
            continuation=latest['close']>=entry if direction=='long' else latest['close']<=entry
            if not failed and continuation and latest['low']<=entry<=latest['high']:
                result['ifvg']={**gap,'original_direction':gap['direction'],'direction':direction,'entry':entry,
                    'inverted_at':bars[inverted]['time'],'retested_at':latest['time'],'valid':True}
    if len(bars)-1>bos_index:
        leg=bars[sweep_index:bos_index+1]
        low=min(bar['low'] for bar in leg);high=max(bar['high'] for bar in leg)
        entry=_round((low+high)/2,tick)
        reaction=latest['close']>=entry-tick*decimal(config['equilibrium_tolerance_ticks']) if direction=='long' else latest['close']<=entry+tick*decimal(config['equilibrium_tolerance_ticks'])
        if low<high and latest['low']<=entry<=latest['high'] and reaction:
            result['equilibrium']={'low':low,'high':high,'entry':entry,'formed_at':bars[bos_index]['time'],
                'levels':{str(part):low+(high-low)*part for part in (Decimal(0),Decimal('.25'),Decimal('.5'),Decimal('.75'),Decimal(1))},
                'retraced_at':latest['time'],'valid':True}
    return result


def _rsi_values(bars,period):
    values=[None]*len(bars)
    if len(bars)<=period: return values
    changes=[bars[index]['close']-bars[index-1]['close'] for index in range(1,len(bars))]
    gain=sum((max(change,Decimal(0)) for change in changes[:period]),Decimal(0))/period
    loss=sum((max(-change,Decimal(0)) for change in changes[:period]),Decimal(0))/period
    for index in range(period,len(bars)):
        if index>period:
            change=changes[index-1]
            gain=(gain*(period-1)+max(change,Decimal(0)))/period
            loss=(loss*(period-1)+max(-change,Decimal(0)))/period
        values[index]=Decimal(50) if not gain and not loss else Decimal(100) if not loss else Decimal(100)-Decimal(100)/(1+gain/loss)
    return values


def _rsi_divergence(bars,direction,config):
    values=_rsi_values(bars,config['rsi_period'])
    kind='low' if direction=='long' else 'high'
    pivots=[pivot for pivot in _swings(bars,config['swing_sensitivity'],config['timeframe_minutes']) if pivot['kind']==kind and values[pivot['pivot_index']] is not None]
    if len(pivots)<2: return {'confirmed':False,'reason':'Two completed price swings with RSI values are required'}
    earlier,later=pivots[-2:]
    price= later['price']<earlier['price'] if direction=='long' else later['price']>earlier['price']
    oscillator=values[later['pivot_index']]>values[earlier['pivot_index']] if direction=='long' else values[later['pivot_index']]<values[earlier['pivot_index']]
    fresh=len(bars)-1-later['index']<=config['rsi_divergence_lookback']
    return {'confirmed':bool(price and oscillator and fresh),'earlier_swing':earlier,'later_swing':later,
            'earlier_rsi':values[earlier['pivot_index']],'later_rsi':values[later['pivot_index']],
            'reason':'Price and Wilder RSI must diverge across the latest two confirmed swings'}


def _smt(bars,correlated,sweep_index,direction,config):
    if isinstance(correlated,dict): correlated=correlated.get('ES',correlated.get('candles',[]))
    other=_candles(correlated or [])
    by_time={bar['dt']:bar for bar in other}
    window=bars[max(0,sweep_index-config['smt_lookback']):sweep_index+1]
    if len(window)<3 or bars[-1]['dt'] not in by_time or any(bar['dt'] not in by_time for bar in window):
        return {'confirmed':False,'symbol':'ES','reason':'SMT needs synchronized ES bars through the current NQ close'}
    aligned=[by_time[bar['dt']] for bar in window]
    nq=window[-1];es=aligned[-1]
    confirmed=(nq['low']<min(bar['low'] for bar in window[:-1]) and es['low']>=min(bar['low'] for bar in aligned[:-1]) if direction=='long' else
               nq['high']>max(bar['high'] for bar in window[:-1]) and es['high']<=max(bar['high'] for bar in aligned[:-1]))
    return {'confirmed':bool(confirmed),'symbol':'ES','time':nq['time'],'nq_extreme':nq['low' if direction=='long' else 'high'],
            'es_extreme':es['low' if direction=='long' else 'high'],'reason':'NQ sweeps its prior extreme while synchronized ES does not'}


def _json(value):
    if isinstance(value,Decimal): return str(value)
    if isinstance(value,dict): return {key:_json(item) for key,item in value.items() if key!='dt'}
    if isinstance(value,list): return [_json(item) for item in value]
    return value


def required_history(config):
    """Bounded causal history needed by evaluate, shared by replay services."""
    return max(config['sweep_lookback']+config['bos_lookback']+config['max_setup_age_bars']+5,
               3*1440//config['timeframe_minutes'],config['rsi_period']+2*config['rsi_divergence_lookback'],2*config['smt_lookback'])


def evaluate(candles,config,*,higher_timeframes=None,correlated=None):
    """Evaluate one completed prefix. No order, account access or model overrides."""
    config=validate_config(config)
    needed=required_history(config)
    if not isinstance(candles,(list,tuple)): raise ValueError('Candles must be an ordered array')
    bars=_candles(candles[-needed:]);latest=bars[-1] if bars else None
    session=session_at(latest['dt'],config) if latest else None
    higher={}
    aliases={'1h':('1h','h1','60'),'4h':('4h','h4','240')}
    if higher_timeframes is not None and not isinstance(higher_timeframes,dict): raise ValueError('Higher timeframes must be an object')
    for label,keys in aliases.items():
        supplied=next((higher_timeframes[key] for key in keys if key in (higher_timeframes or {})),None)
        higher[label]=_candles(supplied) if supplied is not None else _aggregate(bars,60 if label=='1h' else 240,config['timeframe_minutes'])
    conditions={name:{'passed':False,'reason':reason} for name,reason in (
        ('data','At least six completed candles are required'),('liquidity_sweep','No strict sweep of a prior completed 1h/4h or session level'),
        ('bos','A later completed candle must close beyond the prior structural swing'),
        ('continuation','No currently retraced enabled FVG, IFVG or equilibrium level'),
        ('session','The latest completed candle is outside configured entry windows'),
        ('stop_loss','A tick-aligned stop beyond the sweep invalidation is required'),
        ('take_profit','A positive tick-aligned R-multiple target is required'))}
    conditions.update(rsi={'passed':not config['require_rsi'],'reason':'Optional RSI confirmation is disabled' if not config['require_rsi'] else 'Required RSI divergence is missing'},
                      smt={'passed':not config['require_smt'],'reason':'Optional ES SMT confirmation is disabled' if not config['require_smt'] else 'Required synchronized ES divergence is missing'})
    conditions['data']={'passed':len(bars)>=6,'reason':f'{len(bars)} completed candles available; incomplete candles excluded'}
    conditions['session']={'passed':session is not None,'reason':f'Within {session["name"]} entry window' if session else 'Outside configured entry windows'}
    result={'setup_id':None,'symbol':'NQ','time':latest['time'] if latest else None,'direction':None,
            'entry':None,'stop_loss':None,'take_profit':None,'order_type':'limit','passed':False,'rating':0,
            'conditions':conditions,'evidence':{'session':session,'data_candles':len(bars)}}
    if len(bars)>=6:
        attempts=[]
        session_levels=_session_liquidity_levels(bars,config)
        first=max(1,len(bars)-config['sweep_lookback'],len(bars)-1-config['max_setup_age_bars'])
        for index in range(first,len(bars)-2):
            bar=bars[index];moment=bar['dt']-timedelta(minutes=config['timeframe_minutes'])
            levels=_liquidity(bars,moment,config,higher,session_levels)
            for level in levels:
                if not (bar['high']>level['price'] if level['kind']=='high' else bar['low']<level['price']): continue
                direction='short' if level['kind']=='high' else 'long'
                swings=[pivot for pivot in _swings(bars[max(0,index-config['bos_lookback']):index],config['swing_sensitivity'],config['timeframe_minutes'])
                        if pivot['kind']==('low' if direction=='short' else 'high')]
                structural=swings[-1] if swings else None
                bos=next((cursor for cursor in range(index+1,len(bars)-1)
                          if structural and (bars[cursor]['close']<structural['price'] if direction=='short' else bars[cursor]['close']>structural['price'])),None)
                attempts.append({'index':index,'level':level,'direction':direction,'structure':structural,'bos':bos})
        # Prefer a complete setup, then the newest sweep; never use future bars.
        evaluated=[]
        for attempt in attempts:
            index,direction,bos=attempt['index'],attempt['direction'],attempt['bos']
            choices=_confluences(bars,index,bos,direction,config) if bos is not None else {}
            choice=next((kind for kind in config['entry_types'] if choices.get(kind)),None)
            invalidated=any(bar['low']<bars[index]['low'] if direction=='long' else bar['high']>bars[index]['high'] for bar in bars[index+1:])
            evaluated.append((bool(bos is not None and choice and not invalidated),index,attempt,choices,choice,invalidated))
        if evaluated:
            _,index,attempt,choices,choice,invalidated=max(evaluated,key=lambda item:(item[0],item[1]))
            direction,bos=attempt['direction'],attempt['bos'];sweep=bars[index];level=attempt['level']
            conditions['liquidity_sweep']={'passed':not invalidated,'reason':'A strict sweep was observed'+('; a later bar invalidated its extreme' if invalidated else '')}
            conditions['bos']={'passed':bos is not None,'reason':f'Completed close crossed structure at {bars[bos]["time"]}' if bos is not None else 'No subsequent completed close crossed the prior structural swing'}
            conditions['continuation']={'passed':choice is not None,'reason':f'Current candle actually retraced the {choice} entry' if choice else 'No enabled continuation level was retraced on the current completed candle'}
            result.update(direction=direction)
            result['evidence'].update(liquidity_sweep={**level,'sweep_at':sweep['time'],'sweep_direction':'above' if direction=='short' else 'below',
                'distance':sweep['high']-level['price'] if direction=='short' else level['price']-sweep['low'],
                'reaction_close':latest['close'],'extreme':sweep['high'] if direction=='short' else sweep['low']},
                bos={'confirmed':bos is not None,'structure':attempt['structure'],'time':bars[bos]['time'] if bos is not None else None},
                continuation=choices,entry_type=choice)
            if choice:
                tick=decimal(config['tick_size']);entry=choices[choice]['entry']
                stop=_round(sweep['low']-tick*decimal(config['stop_buffer_ticks']),tick,ROUND_FLOOR) if direction=='long' else _round(sweep['high']+tick*decimal(config['stop_buffer_ticks']),tick,ROUND_CEILING)
                valid=stop>0 and (stop<entry if direction=='long' else stop>entry)
                conditions['stop_loss']={'passed':valid,'reason':'Stop is beyond the observed sweep extreme and aligned to the NQ tick' if valid else 'The proposed entry and invalidation do not define a valid stop'}
                risk=abs(entry-stop);target=_round(entry+(risk*decimal(config['rr']) if direction=='long' else -risk*decimal(config['rr'])),tick,ROUND_CEILING if direction=='long' else ROUND_FLOOR)
                target_valid=target>0 and (target>entry if direction=='long' else target<entry)
                conditions['take_profit']={'passed':target_valid,'reason':'Target meets the configured R multiple on valid NQ ticks' if target_valid else 'The configured R target is outside valid positive NQ prices'}
                result.update(entry=entry,stop_loss=stop,take_profit=target)
            rsi=_rsi_divergence(bars,direction,config);smt=_smt(bars,correlated,index,direction,config)
            result['evidence'].update(rsi_divergence=rsi,smt_divergence=smt)
            for name,signal,required in (('rsi',rsi,config['require_rsi']),('smt',smt,config['require_smt'])):
                conditions[name]={'passed':bool(signal['confirmed'] or not required),'reason':signal['reason']+(' (optional)' if not required else ' (required)')}
    weights={'liquidity_sweep':25,'bos':25,'continuation':25,'session':10,'stop_loss':5}
    result['rating']=sum(weight for name,weight in weights.items() if conditions[name]['passed'])
    result['rating']+=5*sum(bool(result['evidence'].get(key,{}).get('confirmed')) for key in ('rsi_divergence','smt_divergence'))
    result['passed']=all(condition['passed'] for condition in conditions.values())
    sweep=result['evidence'].get('liquidity_sweep')
    identity={'symbol':'NQ','direction':result['direction'],'config':config}
    if sweep:
        identity.update(sweep={key:sweep[key] for key in ('kind','price','timeframe','source','time','sweep_at','sweep_direction','extreme')},
                        bos_time=result['evidence']['bos']['time'])
    else: identity['evaluation_time']=result['time']
    result['setup_id']=sha256(json.dumps(_json(identity),sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return _json(result)
