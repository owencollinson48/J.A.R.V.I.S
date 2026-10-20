"""Causal OHLC replay, execution ambiguity and reproducible NQ costs."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from jarvis.trading import backtest
from jarvis.trading.strategy import DEFAULT_CONFIG


def config(**changes):
    return {**deepcopy(DEFAULT_CONFIG), 'risk_pct':1, 'max_exposure_pct':200,
            'sessions':[{'name':'Test','timezone':'UTC','open':'09:30','weekdays':[0,1,2,3,4]}],
            **changes}


def bars(*values, start='2026-10-20T10:00:00+00:00'):
    beginning = datetime.fromisoformat(start)
    return [{'time':(beginning+timedelta(minutes=5*index)).isoformat(),'open':str(o),'high':str(h),
             'low':str(l),'close':str(c),'volume':'100','closed':True}
            for index,(o,h,l,c) in enumerate(values)]


def setup(identity='setup-1', **changes):
    return {'setup_id':identity,'symbol':'NQ','direction':'long','entry':'100','stop_loss':'95',
            'take_profit':'112.5','order_type':'limit','passed':True,'rating':100,
            'conditions':{'mandatory':{'passed':True,'reason':'Observed fixture'}},'evidence':{},**changes}


def scripted(monkeypatch, callback=None):
    seen = []
    def evaluate(candles, config, *, correlated=None, **unused):
        seen.append((deepcopy(candles),deepcopy(correlated)))
        return callback(candles) if callback else (setup() if len(candles)==1 else {'passed':False,'conditions':{}})
    monkeypatch.setattr(backtest.strategy,'evaluate',evaluate)
    return seen


def test_signal_candle_cannot_fill_its_own_order(monkeypatch):
    scripted(monkeypatch)
    data=bars((100,120,90,100))
    result=backtest.run_backtest(data,config(),initial_equity='10000')
    assert result['trades']==[] and len(result['unfilled_orders'])==1


def test_later_limit_fill_and_target_have_consistent_cash_and_metrics(monkeypatch):
    scripted(monkeypatch)
    data=bars((100,101,99,100),(100,103,99,102),(102,113,101,112))
    result=backtest.run_backtest(data,config(),initial_equity='10000',strategy_version='V3')
    trade=result['trades'][0]
    assert trade['entry_time']==data[0]['time'] # Next candle open equals preceding close.
    assert trade['exit_time']==data[2]['time'] and trade['exit_reason']=='take_profit'
    assert Decimal(trade['net_pnl'])==250 and Decimal(result['ending_equity'])==10250
    assert trade['strategy_version']=='V3' and trade['dataset']=='backtest'
    assert result['metrics']['total_trades']==1 and Decimal(result['metrics']['net_profit'])==250


def test_unknown_same_bar_stop_and_target_uses_loss(monkeypatch):
    scripted(monkeypatch)
    result=backtest.run_backtest(bars((100,101,99,100),(100,113,94,101)),config(),initial_equity='10000')
    assert result['trades'][0]['exit_reason']=='stop_loss'
    assert Decimal(result['trades'][0]['exit_price'])==95
    assert Decimal(result['ending_equity'])==9900


def test_gap_through_stop_uses_worse_observed_open(monkeypatch):
    scripted(monkeypatch)
    result=backtest.run_backtest(bars((100,101,99,100),(100,103,99,102),(90,92,88,91)),config(),initial_equity='10000')
    assert Decimal(result['trades'][0]['exit_price'])==90
    assert Decimal(result['trades'][0]['net_pnl'])==-200


def test_target_gap_does_not_invent_better_fill(monkeypatch):
    scripted(monkeypatch)
    result=backtest.run_backtest(bars((100,101,99,100),(100,103,99,102),(120,122,119,121)),config(),initial_equity='10000')
    assert Decimal(result['trades'][0]['exit_price'])==Decimal('112.5')


def test_explicit_round_trip_friction_and_integer_contract_sizing(monkeypatch):
    scripted(monkeypatch)
    result=backtest.run_backtest(bars((100,101,99,100),(100,103,99,102),(102,113,101,112)),
        config(risk_pct=2,spread=.25,slippage=.25,commission_per_contract=2),initial_equity='10000')
    trade=result['trades'][0]
    assert trade['quantity']=='1'
    assert Decimal(trade['gross_pnl'])==250 and Decimal(trade['costs'])==19
    assert Decimal(trade['net_pnl'])==231
    assert Decimal(result['ending_equity'])==10231


def test_repeated_identical_setup_cannot_submit_twice(monkeypatch):
    scripted(monkeypatch,lambda candles:setup())
    result=backtest.run_backtest(bars((100,101,99,100),(100,113,99,110),(100,113,99,110)),config(),initial_equity='10000')
    assert len(result['trades'])==1 and not result['unfilled_orders']


def test_future_candles_and_unsynchronised_es_are_not_visible_to_strategy(monkeypatch):
    seen=scripted(monkeypatch)
    data=bars((100,101,99,100),(100,103,99,102),(102,113,101,112))
    paired=bars((50,51,49,50),(50,51,49,50),(50,51,49,50),(50,51,49,50))
    backtest.run_backtest(data,config(),correlated=paired,initial_equity='10000')
    assert [len(primary) for primary,_ in seen]==[1,2,3]
    assert [len(reference) for _,reference in seen]==[1,2,3]
    assert all(reference[-1]['time']<=primary[-1]['time'] for primary,reference in seen)


def test_out_of_session_fill_expires_instead_of_opening_position(monkeypatch):
    scripted(monkeypatch)
    data=bars((100,101,99,100),(100,113,99,110),start='2026-10-20T11:30:00+00:00')
    result=backtest.run_backtest(data,config(),initial_equity='10000')
    assert not result['trades']
    assert any('session expired' in item['reason'].lower() for item in result['rejected'])


def test_existing_position_exit_later_in_bar_cannot_release_earlier_entry_limit(monkeypatch):
    scripted(monkeypatch,lambda candles:setup('setup-'+str(len(candles)),take_profit='102'))
    result=backtest.run_backtest(bars((100,101,99,100),(100,101,99,100),(100,103,99,102)),
                                 config(max_positions=1),initial_equity='10000')
    assert len(result['trades'])==1
    assert any(item['reason']=='Risk check failed' for item in result['rejected'])


def test_final_observed_close_liquidation_is_explicit(monkeypatch):
    scripted(monkeypatch)
    result=backtest.run_backtest(bars((100,101,99,100),(100,103,99,102)),config(),initial_equity='10000')
    assert result['trades'][0]['exit_reason']=='end_of_data'
    assert Decimal(result['trades'][0]['exit_price'])==102
    assert any('liquidated' in assumption for assumption in result['assumptions'])


def test_partial_profit_preserves_integer_quantity_then_remainder_target(monkeypatch):
    scripted(monkeypatch)
    result=backtest.run_backtest(bars((100,101,99,100),(100,103,99,102),(102,113,101,112)),
        config(risk_pct=2,partial_take_profit_r=1,partial_fraction=.5),initial_equity='10000')
    trade=result['trades'][0]
    assert trade['quantity']=='2' and [item['quantity'] for item in trade['exits']]==['1','1']
    assert [item['reason'] for item in trade['exits']]==['partial_take_profit','take_profit']
    assert Decimal(trade['net_pnl'])==350


def test_partial_profit_does_not_turn_one_contract_into_fractional_contract(monkeypatch):
    scripted(monkeypatch)
    result=backtest.run_backtest(bars((100,101,99,100),(100,113,99,110)),
        config(partial_take_profit_r=1,partial_fraction=.5),initial_equity='10000')
    assert len(result['trades'][0]['exits'])==1
    assert result['trades'][0]['exits'][0]['quantity']=='1'


def test_break_even_rule_becomes_effective_on_next_candle(monkeypatch):
    scripted(monkeypatch)
    result=backtest.run_backtest(bars((100,101,99,100),(100,106,99,104),(103,104,99,100)),
        config(break_even_r=1),initial_equity='10000')
    trade=result['trades'][0]
    assert trade['exit_reason']=='break_even_stop' and Decimal(trade['exit_price'])==100


def test_trailing_rule_does_not_retroactively_stop_on_adjustment_candle(monkeypatch):
    scripted(monkeypatch)
    data=bars((100,101,99,100),(100,106,99,104),(104,105,101,102))
    result=backtest.run_backtest(data,config(trailing_points=3),initial_equity='10000')
    trade=result['trades'][0]
    assert trade['exit_time']==data[2]['time'] and trade['exit_reason']=='trailing_stop'
    assert Decimal(trade['exit_price'])==103


@pytest.mark.parametrize('order_type',['market','stop'])
def test_explicit_supported_order_types_still_wait_until_later_bar(monkeypatch,order_type):
    scripted(monkeypatch,lambda candles:setup(order_type=order_type) if len(candles)==1 else {'passed':False})
    result=backtest.run_backtest(bars((100,120,90,100),(101,103,99,102)),config(risk_pct=2),initial_equity='10000')
    assert result['trades'][0]['entry_price']=='101'


def test_requested_range_keeps_earlier_warmup_but_does_not_trade_it(monkeypatch):
    seen=scripted(monkeypatch,lambda candles:{'passed':False})
    data=bars((100,101,99,100),(100,101,99,100),(100,101,99,100))
    result=backtest.run_backtest(data,config(),start=data[1]['time'],end=data[2]['time'])
    assert [len(primary) for primary,_ in seen]==[2,3]
    assert result['coverage']['warmup_candles']==1 and result['coverage']['tested_candles']==2


def test_observed_gaps_are_reported_and_no_market_data_is_fabricated(monkeypatch):
    seen=scripted(monkeypatch,lambda candles:{'passed':False})
    data=bars((100,101,99,100),(100,101,99,100),(100,101,99,100))
    del data[1]
    result=backtest.run_backtest(data,config())
    assert result['coverage']['gaps']==1 and result['coverage']['available_candles']==2
    assert [len(primary) for primary,_ in seen]==[1,2]


def test_empty_and_incomplete_bars_produce_no_fake_trades():
    incomplete=bars((100,101,99,100));incomplete[0]['closed']=False
    result=backtest.run_backtest(incomplete,config())
    assert result['trades']==[] and result['coverage']['available_candles']==0
    json.dumps(result,allow_nan=False)


@pytest.mark.parametrize('change',[{'close':'NaN'},{'high':'98'},{'time':'2026-10-20T10:00:00'},{'open':True}])
def test_invalid_market_data_is_rejected(change):
    data=bars((100,101,99,100));data[0].update(change)
    with pytest.raises(ValueError):backtest.run_backtest(data,config())


def test_duplicate_timestamps_are_not_silently_resampled():
    data=bars((100,101,99,100));data.append(deepcopy(data[0]))
    with pytest.raises(ValueError,match='Duplicate'):backtest.run_backtest(data,config())


def test_es_cannot_be_configured_as_traded_instrument():
    with pytest.raises(ValueError):backtest.run_backtest([],config(symbol='ES'))


def test_es_price_history_cannot_be_silently_relabelled_as_nq():
    data=bars((100,101,99,100));data[0]['symbol']='ES'
    with pytest.raises(ValueError,match='Primary history'):backtest.run_backtest(data,config())


def test_long_histories_keep_required_past_context_bounded_without_future_bars(monkeypatch):
    observed=[]
    def evaluate(candles, config, **kwargs):
        observed.append((len(candles),candles[0]['time'],candles[-1]['time']))
        return {'passed':False}
    monkeypatch.setattr(backtest.strategy,'evaluate',evaluate)
    data=bars(*[(100,101,99,100)]*870)
    backtest.run_backtest(data,config())
    needed=backtest.strategy.required_history(config())
    assert observed[-1][0]==needed
    assert observed[-1][1]==data[-needed]['time']
    assert all(last==data[index]['time'] for index,(_,_,last) in enumerate(observed))
