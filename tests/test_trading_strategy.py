"""Independent confirmations use only completed candles and market-local time."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from jarvis.trading.strategy import (DEFAULT_CONFIG, aggregate_candles, evaluate,
    session_at, validate_config, _candles, _confluences, _rsi_values, _rsi_divergence, _swings)


def candle(index, o, h, l, c, start='2026-01-12T14:00:00+00:00', closed=True):
    return {'time':(datetime.fromisoformat(start)+timedelta(minutes=5*(index+1))).isoformat(),
            'open':str(o),'high':str(h),'low':str(l),'close':str(c),'volume':'10','closed':closed}


def long_bars():
    return [candle(index,*values) for index,values in enumerate([
        (102,104,101,103),(103,105,102,102),(102,103,101,102.5),
        (102.5,103,99,101),(101,106,100.5,105.5),(105.5,108,105.25,107),
        (107,108,104,105)])]


def higher():
    return {'1h':[{'time':'2026-01-12T13:00:00+00:00','open':'102','high':'106','low':'100','close':'104','closed':True}],
            '4h':[]}


def config(**changes):
    return validate_config(changes)


def test_liquidity_bos_and_fvg_retracement_form_a_tick_aligned_limit_setup():
    result=evaluate(long_bars(),config(entry_types=['fvg']),higher_timeframes=higher())
    assert result['passed'] and result['direction']=='long' and result['symbol']=='NQ'
    assert result['order_type']=='limit' and result['entry']=='104.25'
    assert result['stop_loss']=='98.75' and result['take_profit']=='118.00'
    assert all(item['passed'] for item in result['conditions'].values())
    assert result['evidence']['liquidity_sweep']['distance']=='1'
    fvg=result['evidence']['continuation']['fvg']
    assert fvg['lower']=='103' and fvg['upper']=='105.25' and fvg['valid']
    assert Decimal(fvg['mitigation_pct'])>50 and result['rating']==90


def test_bearish_rules_mirror_sweep_close_break_and_gap_retest():
    bars=long_bars()
    for bar in bars:
        o,h,l,c=[Decimal(bar[name]) for name in ('open','high','low','close')]
        bar.update(open=str(210-o),high=str(210-l),low=str(210-h),close=str(210-c))
    htf={'1h':[{'time':'2026-01-12T13:00:00+00:00','open':108,'high':110,'low':104,'close':106}], '4h':[]}
    result=evaluate(bars,config(entry_types=['fvg']),higher_timeframes=htf)
    assert result['passed'] and result['direction']=='short'
    assert Decimal(result['stop_loss'])>Decimal(result['entry'])>Decimal(result['take_profit'])
    assert result['evidence']['liquidity_sweep']['sweep_direction']=='above'


def test_price_merely_touching_liquidity_is_not_a_sweep():
    bars=long_bars();bars[3]['low']='100'
    result=evaluate(bars,config(),higher_timeframes=higher())
    assert not result['passed'] and not result['conditions']['liquidity_sweep']['passed']


def test_wick_beyond_structure_without_completed_close_is_not_bos():
    bars=long_bars();bars[4].update(close='104.75');bars[5].update(open='104.75',low='104.5',close='104.75')
    result=evaluate(bars,config(),higher_timeframes=higher())
    assert result['conditions']['liquidity_sweep']['passed']
    assert not result['conditions']['bos']['passed'] and not result['passed']


def test_incomplete_candle_cannot_supply_bos_or_future_information():
    bars=long_bars()
    closed=evaluate(bars,config(),higher_timeframes=higher())
    future=candle(7,105,200,50,199,closed=False)
    assert evaluate(bars+[future],config(),higher_timeframes=higher())==closed
    assert not evaluate(bars[:4]+[{**bars[4],'closed':False}],config(),higher_timeframes=higher())['passed']


def test_displacement_without_actual_retracement_has_no_entry():
    bars=long_bars();bars[-1].update(open='107',low='106',high='110',close='109')
    result=evaluate(bars,config(),higher_timeframes=higher())
    assert result['conditions']['bos']['passed'] and not result['conditions']['continuation']['passed']
    assert not result['passed'] and result['entry'] is None


def test_gap_formation_bar_cannot_count_as_its_own_retest():
    bars=_candles(long_bars()[:6])
    choices=_confluences(bars,3,4,'long',config(entry_types=['fvg']))
    assert choices['fvg'] is None


def test_full_fvg_mitigation_does_not_leave_a_valid_fvg_entry():
    bars=long_bars();bars[-1]['low']='103'
    result=evaluate(bars,config(entry_types=['fvg']),higher_timeframes=higher())
    assert not result['passed'] and result['evidence']['continuation']['fvg'] is None


def test_equilibrium_requires_actual_half_retracement_and_records_quarters():
    bars=long_bars();bars[-1].update(low='102.25',close='104')
    result=evaluate(bars,config(entry_types=['equilibrium']),higher_timeframes=higher())
    assert result['passed'] and Decimal(result['entry'])==Decimal('102.50')
    levels=result['evidence']['continuation']['equilibrium']['levels']
    assert set(levels)=={'0','0.25','0.5','0.75','1'}
    assert levels['0']=='99' and levels['1']=='106'


def test_opposite_gap_close_through_then_later_retest_can_form_ifvg():
    bars=[candle(index,*values) for index,values in enumerate([
        (106,107,105,105.5),(104.5,106,104,105),(104.25,104.5,102,102.5),
        (102.5,104,99,101),(101,108,100.5,107),(107,107.5,104.5,105.5)])]
    result=evaluate(bars,config(entry_types=['ifvg']),higher_timeframes=higher())
    assert result['passed'] and result['entry']=='104.75'
    inverted=result['evidence']['continuation']['ifvg']
    assert inverted['original_direction']=='short' and inverted['direction']=='long'
    assert inverted['inverted_at']==bars[4]['time'] and inverted['retested_at']==bars[5]['time']


def test_wick_through_opposite_fvg_does_not_invert_it():
    bars=_candles([candle(index,*values) for index,values in enumerate([
        (106,107,105,105.5),(104.5,106,104,105),(104.25,104.5,102,102.5),
        (102.5,104,99,101),(101,108,100.5,104.75),(104.75,105,104.5,104.75)])])
    assert _confluences(bars,3,4,'long',config())['ifvg'] is None


def test_sweep_extreme_invalidation_blocks_a_later_retest():
    bars=long_bars();bars[-1].update(low='98.5',close='105')
    result=evaluate(bars,config(entry_types=['equilibrium']),higher_timeframes=higher())
    assert not result['passed'] and not result['conditions']['liquidity_sweep']['passed']
    assert 'invalidated' in result['conditions']['liquidity_sweep']['reason']


def test_same_sweep_bos_has_stable_id_across_retests_to_prevent_duplicate_entries():
    bars=long_bars();first=evaluate(bars,config(),higher_timeframes=higher())
    later=candle(7,105,106,104,105)
    second=evaluate(bars+[later],config(),higher_timeframes=higher())
    assert first['passed'] and second['passed']
    assert first['time']!=second['time'] and first['setup_id']==second['setup_id']


def test_future_htf_bar_cannot_supply_a_liquidity_level():
    htf=higher();htf['1h'][0]['time']='2026-01-12T15:00:00+00:00'
    result=evaluate(long_bars(),config(),higher_timeframes=htf)
    assert not result['conditions']['liquidity_sweep']['passed']


def test_no_higher_timeframe_level_is_fabricated_from_an_incomplete_hour():
    result=evaluate(long_bars(),config())
    assert not result['passed'] and not result['conditions']['liquidity_sweep']['passed']


def test_previous_four_hour_extreme_can_supply_liquidity_independently():
    htf=higher();htf['4h']=[{**htf['1h'][0],'time':'2026-01-12T12:00:00+00:00'}];htf['1h']=[]
    result=evaluate(long_bars(),config(),higher_timeframes=htf)
    assert result['passed'] and result['evidence']['liquidity_sweep']['timeframe']=='4h'


def test_only_complete_finished_session_ranges_supply_session_liquidity():
    previous=[candle(index,102,106,100,104,start='2026-01-12T13:00:00+00:00') for index in range(12)]
    cfg=config(sessions=[{'name':'Prior session','timezone':'UTC','open':'13:00','duration_minutes':60,'weekdays':[0,1,2,3,4]}])
    result=evaluate(previous+long_bars(),cfg,higher_timeframes={'1h':[],'4h':[]})
    assert result['passed'] and result['evidence']['liquidity_sweep']['timeframe']=='session'
    incomplete=evaluate(previous[:6]+previous[7:]+long_bars(),cfg,higher_timeframes={'1h':[],'4h':[]})
    assert not incomplete['conditions']['liquidity_sweep']['passed']


def test_aggregation_uses_complete_aligned_buckets_and_real_ohlcv_only():
    bars=[candle(index,100,102,99,101,start='2026-01-12T12:00:00+00:00') for index in range(13)]
    hourly=aggregate_candles(bars,60)
    assert len(hourly)==1 and hourly[0]['time']=='2026-01-12T13:00:00+00:00'
    assert hourly[0]['open']=='100' and hourly[0]['close']=='101' and hourly[0]['volume']=='120'
    assert aggregate_candles(bars[:11],60)==[]
    assert aggregate_candles(bars[:6]+bars[7:12],60)==[]


def test_fvg_cannot_bridge_missing_candles():
    bars=long_bars();bars[5]['time']='2026-01-12T14:29:00+00:00'
    result=evaluate(bars,config(entry_types=['fvg']),higher_timeframes=higher())
    assert not result['passed'] and not result['conditions']['continuation']['passed']


def test_two_candle_colour_swings_and_configurable_sensitivity():
    bars=_candles([candle(0,100,104,99,103),candle(1,103,105,101,102),candle(2,102,103,98,99),candle(3,99,102,97,101)])
    pivots=_swings(bars,1,5)
    assert [(pivot['kind'],str(pivot['price'])) for pivot in pivots]==[('high','105'),('low','97')]
    assert _swings(bars,2,5)==[]


@pytest.mark.parametrize('stamp,expected',[
    ('2026-01-12T14:00:00+00:00',True),('2026-01-12T13:59:59+00:00',False),
    ('2026-01-12T16:30:00+00:00',True),('2026-01-12T16:30:01+00:00',False),
    ('2026-07-13T13:00:00+00:00',True),('2026-07-13T12:59:59+00:00',False),
    ('2026-01-11T14:30:00+00:00',False),
    ('2026-03-16T13:30:00+00:00',True), # US DST changes before London's.
])
def test_new_york_entry_window_uses_market_timezone_and_dst(stamp,expected):
    cfg=config(sessions=[{'name':'New York','timezone':'America/New_York','open':'09:30','weekdays':[0,1,2,3,4]}])
    assert bool(session_at(stamp,cfg)) is expected


def test_london_window_moves_with_british_summer_time():
    cfg=config(sessions=[{'name':'London','timezone':'Europe/London','open':'08:00','weekdays':[0,1,2,3,4]}])
    assert session_at('2026-01-12T07:30:00+00:00',cfg)
    assert session_at('2026-07-13T06:30:00+00:00',cfg)
    assert session_at('2026-03-16T07:30:00+00:00',cfg)
    assert not session_at('2026-07-13T06:29:59+00:00',cfg)


def test_pre_open_window_can_cross_the_previous_calendar_day():
    cfg=config(sessions=[{'name':'Night','timezone':'UTC','open':'00:15','weekdays':[0]}])
    assert session_at('2026-01-11T23:50:00+00:00',cfg)['name']=='Night'


def test_session_helper_rejects_naive_datetimes_instead_of_using_host_timezone():
    with pytest.raises(ValueError,match='timezone'):
        session_at(datetime(2026,1,12,9,30),config())


def test_valid_setup_outside_session_still_fails_mandatory_filter():
    bars=long_bars()
    for bar in bars: bar['time']=(datetime.fromisoformat(bar['time'])+timedelta(hours=7)).isoformat()
    htf=higher();htf['1h'][0]['time']='2026-01-12T20:00:00+00:00'
    result=evaluate(bars,config(),higher_timeframes=htf)
    assert result['conditions']['continuation']['passed'] and not result['conditions']['session']['passed']
    assert not result['passed']


def test_optional_rsi_does_not_block_but_mandatory_missing_rsi_does():
    assert evaluate(long_bars(),config(),higher_timeframes=higher())['passed']
    result=evaluate(long_bars(),config(require_rsi=True),higher_timeframes=higher())
    assert not result['passed'] and not result['conditions']['rsi']['passed']


def test_wilder_rsi_reproduces_known_initial_value():
    closes=['44.34','44.09','44.15','43.61','44.33','44.83','45.10','45.42','45.84','46.08','45.89','46.03','45.61','46.28','46.28']
    bars=_candles([candle(index,value,Decimal(value)+1,Decimal(value)-1,value) for index,value in enumerate(closes)])
    values=_rsi_values(bars,14)
    assert all(value is None for value in values[:14])
    assert abs(values[-1]-Decimal('70.464135021097'))<Decimal('.000000001')


def test_unknown_volume_stays_unknown_without_blocking_price_rules():
    bars=long_bars()
    for bar in bars: bar['volume']=None
    assert evaluate(bars,config(),higher_timeframes=higher())['passed']
    hourly=[candle(index,100,102,99,101,start='2026-01-12T12:00:00+00:00') for index in range(12)]
    hourly[3]['volume']=None
    assert aggregate_candles(hourly,60)[0]['volume'] is None


@pytest.mark.parametrize('direction',['long','short'])
def test_rsi_divergence_compares_the_actual_confirmed_swing_extremes(direction):
    closes=[110,109,108,107,106,105,104,103,104,105,106,107,106,105,104,104.5,105,106]
    bars=[]
    for index,close in enumerate(closes):
        opening=closes[index-1] if index else 111
        low=min(opening,close)-.25
        if index==14: low=100
        bars.append(candle(index,opening,max(opening,close)+.25,low,close))
    if direction=='short':
        for bar in bars:
            o,h,l,c=[Decimal(bar[name]) for name in ('open','high','low','close')]
            bar.update(open=str(220-o),high=str(220-l),low=str(220-h),close=str(220-c))
    result=_rsi_divergence(_candles(bars),direction,config(rsi_period=3))
    assert result['confirmed']
    assert result['later_swing']['pivot_time']==bars[14]['time']


def test_smt_uses_only_time_synchronized_es_and_never_changes_traded_symbol():
    bars=long_bars();es=[{**bar,'open':'200','high':'202','low':'199','close':'201'} for bar in bars]
    result=evaluate(bars,config(require_smt=True),higher_timeframes=higher(),correlated={'ES':es})
    assert result['passed'] and result['symbol']=='NQ'
    assert result['evidence']['smt_divergence']['symbol']=='ES'
    result=evaluate(bars,config(require_smt=True),higher_timeframes=higher(),correlated=es[:-1])
    assert not result['passed'] and not result['conditions']['smt']['passed']
    es[3]['low']='198'
    result=evaluate(bars,config(require_smt=True),higher_timeframes=higher(),correlated=es)
    assert not result['passed'] and not result['conditions']['smt']['passed']


@pytest.mark.parametrize('changes',[
    {'symbol':'ES'},{'smt_symbol':'YM'},{'quantity_step':.1},{'tick_size':.5},{'contract_multiplier':2},
    {'risk_pct':float('nan')},{'risk_pct':float('inf')},{'risk_pct':0},{'risk_pct':6},
    {'rr':0},{'timeframe_minutes':7},{'timeframe_minutes':True},{'require_smt':'yes'},
    {'entry_types':[]},{'entry_types':['fvg','fvg']},{'entry_types':[{}]},{'entry_types':['anything']},
    {'sessions':[]},{'sessions':[{'name':'London','timezone':'Invalid/Zone','open':'08:00','weekdays':[0]}]},
    {'sessions':[{'name':'London','timezone':'UTC','open':'8:00','weekdays':[0]}]},
    {'sessions':[{'name':'London','timezone':'UTC','open':'08:00','weekdays':[7]}]},
    {'partial_take_profit_r':3},{'unknown':1},
])
def test_configuration_is_strict_bounded_and_keeps_nq_contract_identity(changes):
    with pytest.raises(ValueError): validate_config(changes)


def test_config_and_inputs_are_not_mutated_and_base_merge_is_explicit():
    changes={'entry_types':['fvg']};before=deepcopy(changes)
    cfg=validate_config(changes,base={'rr':3})
    assert changes==before and cfg['rr']==3
    cfg['sessions'][0]['name']='Changed'
    assert DEFAULT_CONFIG['sessions'][0]['name']=='London'
    bars=long_bars();original=deepcopy(bars);evaluate(bars,config(),higher_timeframes=higher())
    assert bars==original


@pytest.mark.parametrize('mutate',[
    lambda bar:bar.update(time='2026-01-12T14:05:00'),
    lambda bar:bar.update(close='NaN'),lambda bar:bar.update(high='100'),
    lambda bar:bar.update(volume=-1),lambda bar:bar.update(closed='true'),
])
def test_bad_market_data_is_rejected_rather_than_repaired(mutate):
    bars=long_bars();mutate(bars[0])
    with pytest.raises(ValueError): evaluate(bars,config(),higher_timeframes=higher())


def test_unordered_or_duplicate_completed_bars_are_rejected():
    bars=long_bars();bars[1]['time']=bars[0]['time']
    with pytest.raises(ValueError,match='ascending'): evaluate(bars,config())


def test_no_data_is_a_measured_rejection_not_a_fabricated_setup():
    result=evaluate([],config())
    assert not result['passed'] and result['direction'] is None and result['entry'] is None
    assert result['rating']==0 and not result['conditions']['data']['passed']
