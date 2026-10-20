"""Risk boundaries are enforced without asking a language model for a decision."""
from decimal import Decimal

import pytest

from jarvis.trading.risk import size_position


def size(config=None, portfolio=None, equity='100000', entry='1000', stop='990'):
    return size_position(equity, entry, stop, config or {}, portfolio)


def test_integer_contracts_round_down_without_exceeding_risk():
    result = size()
    assert result['passed'] and result['quantity'] == '2'
    assert result['risk_budget'] == '500' and result['unit_risk'] == '200'
    assert result['risk_amount'] == '400'
    assert Decimal(result['risk_amount']) <= Decimal(result['risk_budget'])


def test_short_trade_uses_absolute_stop_distance():
    assert size(stop='1010') == size()


def test_costs_cover_spread_both_slippage_legs_and_roundtrip_commission():
    result = size({'spread': '.5', 'slippage': '.25', 'commission_per_contract': '2.5'})
    assert result['unit_risk'] == '225' and result['quantity'] == '2'
    assert result['risk_amount'] == '450'


def test_fractional_contract_is_rejected_instead_of_rounding_up():
    result = size(equity='10000')
    assert not result['passed'] and result['quantity'] == '0'
    assert any('integer NQ' in reason for reason in result['reasons'])


def test_exposure_can_limit_size_below_risk_budget():
    result = size({'max_exposure_pct': '20'})
    assert result['passed'] and result['quantity'] == '1'
    assert result['proposed_exposure'] == '20000'


def test_default_exposure_honestly_rejects_real_nq_notional_for_small_equity():
    result = size(entry='20000', stop='19990')
    assert not result['passed'] and result['quantity'] == '0'
    assert any('exposure allowance' in reason for reason in result['reasons'])


def test_existing_notional_uses_remaining_exposure():
    result = size({'max_exposure_pct': '50'}, {'positions': [
        {'symbol': 'NQ', 'quantity': '2', 'entry': '1000', 'unrealized_pnl': '50'}]})
    assert result['exposure_notional'] == '40000'
    assert not result['passed'] and result['quantity'] == '0'


def test_daily_remaining_budget_reduces_size_without_increasing_risk():
    result = size(portfolio={'daily_loss': '1750'})
    assert result['passed'] and result['quantity'] == '1'
    assert result['risk_budget'] == '500' and result['available_risk_budget'] == '250'


@pytest.mark.parametrize('portfolio, phrase', [
    ({'daily_loss': '2000'}, 'Daily loss limit'),
    ({'weekly_loss': '5000'}, 'Weekly loss limit'),
    ({'daily_loss': '2001'}, 'Daily loss limit'),
])
def test_loss_limits_suspend_new_entries_at_and_beyond_limit(portfolio, phrase):
    result = size(portfolio=portfolio)
    assert not result['passed'] and result['quantity'] == '0'
    assert any(phrase in reason for reason in result['reasons'])


def test_loss_limits_use_explicit_period_start_equity_when_supplied():
    result = size(portfolio={'daily_loss': '2000', 'day_start_equity': '200000'})
    assert result['passed']


def test_pending_entries_reserve_simultaneous_position_slots():
    result = size(portfolio={'pending_orders': [
        {'symbol': 'NQ', 'quantity': '1', 'entry': '1000', 'status': 'pending'},
        {'symbol': 'NQ', 'quantity': '1', 'entry': '990', 'status': 'submitted'}]})
    assert not result['passed']
    assert any('Maximum simultaneous' in reason for reason in result['reasons'])


def test_closed_positions_and_cancelled_orders_do_not_consume_capacity():
    result = size(portfolio={'positions': [{'status': 'closed'}],
                             'pending_orders': [{'status': 'cancelled'}]})
    assert result['passed'] and result['quantity'] == '2'


def test_no_averaging_into_losing_nq_position():
    result = size(portfolio={'positions': [
        {'symbol': 'NQ', 'quantity': '1', 'entry': '1000', 'unrealized_pnl': '-.01'}]})
    assert not result['passed'] and result['quantity'] == '0'
    assert any('losing NQ' in reason for reason in result['reasons'])


def test_missing_pnl_or_exposure_fails_closed():
    result = size(portfolio={'positions': [{'symbol': 'NQ'}]})
    assert not result['passed'] and result['quantity'] == '0'
    assert any('no verified P/L' in reason for reason in result['reasons'])
    assert any('exposure is unavailable' in reason for reason in result['reasons'])


def test_negative_net_pnl_is_not_hidden_by_positive_gross_pnl():
    result = size(portfolio={'positions': [
        {'symbol':'NQ','quantity':1,'entry':1000,'unrealized_pnl':10,'net_pnl':-1}]})
    assert not result['passed'] and any('losing NQ' in reason for reason in result['reasons'])


def test_other_instruments_need_explicit_contract_spec_for_exposure_accounting():
    position = {'symbol':'ES','quantity':1,'entry':1000,'unrealized_pnl':10}
    assert not size(portfolio={'positions':[position]})['passed']
    position['contract_multiplier'] = 50
    result = size(portfolio={'positions':[position]})
    assert result['passed'] and result['exposure_notional'] == '50000'


@pytest.mark.parametrize('field', ['spread', 'slippage'])
def test_maximum_cost_boundary_is_allowed_but_greater_value_is_rejected(field):
    assert size({field: '2'})['passed']
    result = size({field: '2.00001'})
    assert not result['passed'] and any('exceeds' in reason for reason in result['reasons'])


@pytest.mark.parametrize('entry,stop', [('1000.1', '990'), ('1000', '990.1'), ('1000', '1000')])
def test_invalid_stop_or_price_tick_never_creates_quantity(entry, stop):
    result = size(entry=entry, stop=stop)
    assert not result['passed'] and result['quantity'] == '0'


@pytest.mark.parametrize('config', [
    {'symbol': 'ES'}, {'contract_multiplier': 50}, {'quantity_step': '.1'}, {'tick_size': '.5'},
    {'risk_pct': 0}, {'risk_pct': 101}, {'max_positions': True}, {'max_positions': 0},
    {'spread': '-.1'}, {'slippage': 'NaN'}, {'max_exposure_pct': 'Infinity'},
])
def test_core_rejects_incompatible_specs_and_invalid_risk_configuration(config):
    with pytest.raises(ValueError):
        size(config)


@pytest.mark.parametrize('bad', ['NaN', 'Infinity', '-Infinity', '', 'one', True, None, [], -1, 0])
@pytest.mark.parametrize('field', ['equity', 'entry', 'stop'])
def test_untrusted_nonfinite_nonpositive_or_wrong_type_inputs_raise_value_error(field, bad):
    with pytest.raises(ValueError):
        size(**{field: bad})


@pytest.mark.parametrize('portfolio', [
    {'daily_loss': '-1'}, {'weekly_loss': 'NaN'}, {'positions': {}},
    {'positions': [None]}, {'exposure_notional': '-1'}, {'day_start_equity': 0},
])
def test_invalid_portfolio_measurements_are_never_silently_defaulted(portfolio):
    with pytest.raises(ValueError):
        size(portfolio=portfolio)


def test_exact_decimal_boundary_can_afford_one_contract_without_float_error():
    result = size({'risk_pct': '.3', 'spread': '.25', 'commission_per_contract': '.1'},
                  equity='17400', stop='997.75')
    assert result['unit_risk'] == '50.2' and result['risk_budget'] == '52.2'
    assert result['passed'] and result['quantity'] == '1'


def test_size_calculation_does_not_mutate_configuration_or_portfolio():
    config = {'risk_pct': '.5'}
    portfolio = {'positions': [], 'daily_loss': '0'}
    size(config, portfolio)
    assert config == {'risk_pct': '.5'} and portfolio == {'positions': [], 'daily_loss': '0'}
