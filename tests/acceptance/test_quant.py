from datetime import date, timedelta
from decimal import Decimal
import math
import pytest
from personal_agent import core, decision, finance, knowledge, market, quant

def test_Q01_bars_open_close_provenance_and_atomic_backfill(isolated_env):
    rows = [{'ts': '2026-01-02', 'open': '100', 'close': '101', 'currency': 'USD'}]
    market._cache_bars('Q01T', rows, 'yfinance', 'split_and_dividend_adj')
    b = market.bars('Q01T', 10)
    assert len(b) == 1 and b[0]['open'] == '100' and b[0]['adj_basis'] == 'split_and_dividend_adj'

    # Actual provider sync_bars ingestion with missing 'o' preserves open=None and blocks next-bar-open backtest
    from unittest.mock import patch
    d0 = date(2026, 1, 2)
    fake_bars = {'bars': {'NOOPEN': [{'t': (d0 + timedelta(days=i)).isoformat() + 'T00:00:00Z', 'c': 100.0} for i in range(45)]}}
    with patch.object(market, 'alpaca_configured', return_value=True), patch.object(market, '_env', return_value='k'), patch.object(market, '_request', return_value=fake_bars):
        market.sync_bars('NOOPEN', start='2026-01-01')
    assert market.bars('NOOPEN', 5)[0]['open'] is None
    assert quant.backtest_strategies('NOOPEN')['status'] == 'missing_open_price'

def test_Q02_backtest_next_bar_open_and_financing_cost_monotonicity(isolated_env):
    d0 = date(2026, 1, 2)
    bars = [{'ts': (d0 + timedelta(days=i)).isoformat(), 'open': str(100.0 * (1.002 ** i)), 'close': str(100.1 * (1.002 ** i)), 'currency': 'USD'} for i in range(60)]
    market._cache_bars('Q02T', bars, 'yfinance', 'split_and_dividend_adj')
    r_low = quant.simulate_cashflow_strategy('Q02T', '10000', '0', '80_20_3x_sleeve', financing_rate='0.01')
    r_high = quant.simulate_cashflow_strategy('Q02T', '10000', '0', '80_20_3x_sleeve', financing_rate='0.08')
    assert r_low['terminal_wealth'] > r_high['terminal_wealth']
    assert r_low['backtest_spec_hash'] != r_high['backtest_spec_hash']

def test_Q03_covariance_psd_and_stress_fx_consistency(isolated_env):
    d = date.today().isoformat()
    with core.db('finance') as c:
        finance.schema(c)
        c.execute('INSERT INTO sources VALUES(?,?)', ('s', d + 'T10:00:00'))
        c.execute('INSERT INTO nav VALUES(?,?,?,?,?,?)', ('margin', d, 'CAD', '10000', '5000', 's'))
        c.execute('INSERT INTO nav VALUES(?,?,?,?,?,?)', ('tfsa', d, 'CAD', '0', '0', 's'))
        c.execute('INSERT INTO snapshots VALUES(?,?,?,1)', ('margin', d, 's'))
        c.execute('INSERT INTO snapshots VALUES(?,?,?,1)', ('tfsa', d, 's'))
        c.execute('INSERT INTO positions VALUES(?,?,?,?,?,?,?,?,?,?,?)', ('margin', d, 'C1', 'QQQ', 'STK', 'USD', '10', '5000', 'C1', '4500', '1.4'))
    st = decision.factor_stress_test('-0.20', '-0.10', 'QQQ')
    assert Decimal(st['portfolio_pnl_impact_base']) < 0

def test_Q04_etf_lookthrough_no_double_count_and_unallocated(isolated_env):
    snap = {
        'status': 'ok', 'advice_allowed': True, 'as_of': date.today().isoformat(), 'nav': '1000', 'currency': 'CAD',
        'accounts': [{'account': 'margin', 'nav': '1000', 'cash': '400'}],
        'positions': [
            {'account': 'margin', 'conid': '1', 'symbol': 'QQQ', 'asset': 'STK', 'currency': 'USD', 'quantity': '1', 'value_base': '500'},
            {'account': 'margin', 'conid': '2', 'symbol': 'AAPL', 'asset': 'STK', 'currency': 'USD', 'quantity': '1', 'value_base': '100'},
        ]
    }
    lt = quant.etf_lookthrough_exposure({'QQQ': {'AAPL': 0.20}}, portfolio_snapshot=snap)
    aapl_row = next(r for r in lt['lookthrough_exposures'] if r['symbol'] == 'AAPL')
    assert abs(aapl_row['direct_weight'] - 0.10) < 1e-6
    assert abs(aapl_row['etf_lookthrough_weight'] - 0.10) < 1e-6
    assert abs(aapl_row['total_lookthrough_weight'] - 0.20) < 1e-6
    assert abs(lt['unallocated_etf_weights']['QQQ'] - 0.40) < 1e-6

def test_Q05_bsm_iv_inversion_covered_call_and_roll_credit(isolated_env):
    c = quant.analyze_option_strategy('covered_call', '100', '110', 45, volatility='0.30', cost_basis='100')
    assert c['greeks']['nd2_itm_prob'] != c['greeks']['delta']
    inv = quant.analyze_option_strategy('covered_call', '100', '110', 45, premium=str(c['option_premium_per_share']))
    assert abs(inv['solved_iv_from_premium'] - 0.30) < 0.003
    bad = quant.analyze_option_strategy('covered_call', '100', '110', 45, premium='150')
    assert bad['iv_solver_status'] == 'outside_no_arbitrage_bounds' and 'covered_call_payoff' not in bad
    roll = quant.analyze_option_roll(100, 105, 10, 2.0, 115, 45, 2.8, contracts=1, multiplier=100)
    assert roll['net_roll_cashflow'] == 80.0 and roll['dte_change'] == 35

def test_Q06_cashflow_strategy_and_brier_learning_boundaries(isolated_env):
    d0 = date(2026, 1, 2)
    bars = [{'ts': (d0 + timedelta(days=i)).isoformat(), 'open': '100', 'close': '100', 'currency': 'USD'} for i in range(60)]
    market._cache_bars('FLAT', bars, 'yfinance', 'split_and_dividend_adj')
    sim = quant.simulate_cashflow_strategy('FLAT', '10000', '0', 'buy_and_hold', cost_bps='0', cash_yield='0.0')
    assert sim['terminal_wealth'] == 10000.0
    p1 = knowledge.record_forecast_prediction('FLAT', 'Event', '2026-12-31', 0.8)
    knowledge.resolve_forecast_prediction(p1['id'], 1, actual_event_at='2026-12-31T00:00:00+00:00')
    assert knowledge.brier_calibration_report()['brier_score'] == 0.04
