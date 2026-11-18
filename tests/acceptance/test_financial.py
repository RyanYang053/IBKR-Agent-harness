from datetime import date, timedelta
from decimal import Decimal
import json, sqlite3, threading
from pathlib import Path
import pytest
from personal_agent import core, finance, decision, quant, market, ops

def _seed_two_accounts(today_s=None, cash_m='1000', cash_t='1000', n_positions=2):
    d = today_s or date.today().isoformat()
    with core.db('finance') as c:
        finance.schema(c)
        c.execute('INSERT OR REPLACE INTO sources VALUES(?,?)', ('src1', d + 'T20:00:00'))
        c.execute('INSERT INTO nav VALUES(?,?,?,?,?,?)', ('margin', d, 'CAD', '5000', cash_m, 'src1'))
        c.execute('INSERT INTO nav VALUES(?,?,?,?,?,?)', ('tfsa', d, 'CAD', '5000', cash_t, 'src1'))
        c.execute('INSERT INTO snapshots VALUES(?,?,?,1)', ('margin', d, 'src1'))
        c.execute('INSERT INTO snapshots VALUES(?,?,?,1)', ('tfsa', d, 'src1'))
        for i in range(n_positions):
            acct = 'margin' if i % 2 == 0 else 'tfsa'
            c.execute('INSERT INTO positions VALUES(?,?,?,?,?,?,?,?,?,?,?)', (acct, d, f'C{i:03d}', f'SYM{i}', 'STK', 'CAD', '10', '100', f'C{i:03d}', '90', '1'))

def test_F01_untruncated_analytics_invariant_to_max_tool_rows(isolated_env, monkeypatch):
    _seed_two_accounts(n_positions=55)
    cfg_path = Path(isolated_env) / 'config.json'
    cfg = json.loads(cfg_path.read_text(encoding='utf-8'))

    cfg['policy']['max_tool_rows'] = 1
    cfg_path.write_text(json.dumps(cfg), encoding='utf-8')
    exp1 = decision.exposure()
    tax1 = decision.tax_positions(limit=None)
    pol1 = quant.evaluate_policy(mode='AUDIT')
    snap1 = finance.load_full_portfolio_snapshot()
    page1 = finance.portfolio()

    cfg['policy']['max_tool_rows'] = 50
    cfg_path.write_text(json.dumps(cfg), encoding='utf-8')
    exp50 = decision.exposure()
    tax50 = decision.tax_positions(limit=None)
    pol50 = quant.evaluate_policy(mode='AUDIT')
    snap50 = finance.load_full_portfolio_snapshot()
    page50 = finance.portfolio()

    assert len(page1['positions']) == 1 and page1['has_more'] is True
    assert len(page50['positions']) == 50 and page50['has_more'] is True
    assert len(snap1.positions) == 55 and snap1.snapshot_id == snap50.snapshot_id
    assert exp1['gross_value_base'] == exp50['gross_value_base'] == '5500'
    assert tax1['count'] == tax50['count'] == 55
    assert pol1['exposure_summary'] == pol50['exposure_summary']

def test_F02_consistent_snapshot_read_and_non_overwriting_historical_import(isolated_env):
    _seed_two_accounts(today_s='2026-11-16', n_positions=2)
    # Concurrent writer commits replacement NAV while reader holds BEGIN snapshot
    with core.db('finance') as r_conn:
        finance.schema(r_conn)
        r_conn.execute('BEGIN')
        nav_before = r_conn.execute("SELECT total FROM nav WHERE account='margin' AND date='2026-11-16'").fetchone()[0]
        w_conn = sqlite3.connect(Path(isolated_env) / 'finance.sqlite3', timeout=15)
        w_conn.execute("UPDATE nav SET total='9999' WHERE account='margin' AND date='2026-11-16'")
        w_conn.commit(); w_conn.close()
        nav_after = r_conn.execute("SELECT total FROM nav WHERE account='margin' AND date='2026-11-16'").fetchone()[0]
        assert nav_before == nav_after == '5000'
    # Historical older statement does not overwrite newer source NAV
    xml_older = '''<FlexQueryResponse><FlexStatements>
    <FlexStatement accountId="U0000001" toDate="2026-11-16" whenGenerated="2026-11-16T10:00:00">
      <EquitySummaryByReportDateInBase><EquitySummaryInBase currency="CAD" reportDate="2026-11-16" total="1111" cash="111"/></EquitySummaryByReportDateInBase>
    </FlexStatement></FlexStatements></FlexQueryResponse>'''
    p_xml = Path(isolated_env) / 'older.xml'
    p_xml.write_text(xml_older, encoding='utf-8')
    res = finance.import_xml(p_xml)
    assert res['status'] == 'kept_newer_snapshot'
    snap = finance.load_full_portfolio_snapshot()
    assert snap.nav == Decimal('14999')  # 9999 + 5000 preserved

def test_F03_freshness_future_statement_missing_cash_and_empty_cash_only(isolated_env):
    fut = (date.today() + timedelta(days=2)).isoformat()
    _seed_two_accounts(today_s=fut, n_positions=1)
    s_fut = finance.load_full_portfolio_snapshot()
    assert s_fut.status == 'future_statement' and s_fut.advice_allowed is False

    # Missing cash blocks advice_allowed
    with core.db('finance') as c:
        c.execute('DELETE FROM nav'); c.execute('DELETE FROM positions'); c.execute('DELETE FROM snapshots')
    _seed_two_accounts(today_s=date.today().isoformat(), cash_m=None, n_positions=0)
    s_nocash = finance.load_full_portfolio_snapshot()
    assert s_nocash.status == 'missing_cash' and s_nocash.advice_allowed is False

    # Valid empty positions with cash-only NAV succeeds
    with core.db('finance') as c:
        c.execute('DELETE FROM nav'); c.execute('DELETE FROM positions'); c.execute('DELETE FROM snapshots')
    _seed_two_accounts(today_s=date.today().isoformat(), cash_m='5000', cash_t='5000', n_positions=0)
    s_empty_ok = finance.load_full_portfolio_snapshot()
    assert s_empty_ok.status == 'ok' and s_empty_ok.advice_allowed is True and len(s_empty_ok.positions) == 0

def test_F04_multicurrency_cash_flows_intermediate_dates_and_transfers(isolated_env):
    with core.db('finance') as c:
        finance.schema(c)
        for acct in ('margin', 'tfsa'):
            for d, v in [('2026-11-15', '500'), ('2026-11-17', '570')]:
                c.execute('INSERT INTO nav VALUES(?,?,?,?,?,?)', (acct, d, 'CAD', v, v, 'syn'))
            c.execute('INSERT INTO snapshots VALUES(?,?,?,1)', (acct, '2026-11-17', 'syn'))
        c.execute('INSERT INTO records VALUES(?,?,?,?,?)', ('margin', 'cash', 'c1', json.dumps({'type': 'deposit', 'amount': '100', 'currency': 'USD', 'fxRateToBase': '1.4', 'reportDate': '20261116'}), 'syn'))
        c.execute('INSERT INTO records VALUES(?,?,?,?,?)', ('margin', 'cash', 't1', json.dumps({'type': 'transfer', 'amount': '100.00', 'currency': 'CAD', 'reportDate': '20261116'}), 'syn'))
        c.execute('INSERT INTO records VALUES(?,?,?,?,?)', ('tfsa', 'cash', 't2', json.dumps({'type': 'transfer', 'amount': '-100', 'currency': 'CAD', 'reportDate': '20261116'}), 'syn'))
    perf = finance.performance()
    assert Decimal(perf['twr']) == Decimal('0')
    assert Decimal(perf['contributions']) == Decimal('140')

    # Missing FX fails closed
    with core.db('finance') as c:
        c.execute('INSERT INTO records VALUES(?,?,?,?,?)', ('margin', 'cash', 'c_nofx', json.dumps({'type': 'deposit', 'amount': '50', 'currency': 'USD', 'reportDate': '20261116'}), 'syn'))
    assert finance.performance()['status'] == 'missing_fx_rate'

def test_F05_xirr_same_day_multiple_roots_and_known_answer(isolated_env):
    d0 = date(2025, 1, 1); d1 = date(2026, 1, 1); d2 = date(2027, 1, 1)
    assert finance.xirr([(d0, Decimal('-100')), (d0, Decimal('100'))]) is None
    assert finance.xirr([(d0, Decimal('-100')), (d1, Decimal('230')), (d2, Decimal('-132'))]) is None
    r = finance.xirr([(d0, Decimal('-100')), (d1, Decimal('110'))])
    assert r is not None and abs(r - Decimal('0.10')) < Decimal('1e-5')

def test_F06_transaction_reducer_strict_validation(isolated_env):
    _seed_two_accounts(n_positions=1)
    snap = finance.load_full_portfolio_snapshot()
    for bad_trade in (
        {'symbol': 'SYM0', 'action': 'BUY', 'quantity': '10', 'price_per_unit': '100', 'amount_base': '500', 'account': 'margin'},
        {'symbol': 'SYM0', 'action': 'UNKNOWN', 'amount_base': '100', 'account': 'margin'},
        {'symbol': 'SYM0', 'action': 'BUY', 'amount_base': '100', 'estimated_fees_base': '-5', 'account': 'margin'},
        {'symbol': 'SYM0', 'action': 'SELL', 'amount_base': '500', 'account': 'margin'},  # only 100 held
    ):
        with pytest.raises(ValueError):
            quant.simulate_allocation(snap, proposed_trades=[bad_trade])

def test_F07_account_buying_power_and_non_margin_tfsa(isolated_env):
    _seed_two_accounts(cash_m='250', cash_t='100', n_positions=0)
    snap = finance.load_full_portfolio_snapshot()
    with pytest.raises(ValueError, match='insufficient_account_buying_power'):
        quant.simulate_allocation(snap, proposed_trades=[{'symbol': 'QQQ', 'action': 'BUY', 'amount_base': '100', 'estimated_fees_base': '1', 'account': 'tfsa'}])
    pol_res = quant.evaluate_policy(snap, {'symbol': 'QQQ', 'action': 'BUY', 'amount_base': '100', 'estimated_fees_base': '1', 'account': 'tfsa'})
    assert any(b['rule'] == 'insufficient_account_buying_power' for b in pol_res['hard_blocks'])

def test_F08_option_delta_notional_zero_premium_and_invalid_greeks(isolated_env):
    snap = {
        'status': 'ok', 'advice_allowed': True, 'as_of': date.today().isoformat(), 'nav': '10000', 'currency': 'CAD',
        'accounts': [{'account': 'margin', 'nav': '10000', 'cash': '5000', 'allow_margin': True}],
        'positions': [{'account': 'margin', 'conid': 'OPT1', 'symbol': 'AAPL CALL', 'asset': 'OPT', 'currency': 'USD', 'quantity': '1', 'multiplier': '100', 'value_base': '0', 'underlying_price': '100', 'underlying_currency': 'USD', 'fx_rate_to_base': '1.4', 'delta': '0.5'}]
    }
    pol = {'core_symbols': ['QQQ'], 'min_cash_reserve': 0, 'core_min_weight': 0, 'tactical_max_weight': 5, 'max_single_stock_weight': 5, 'max_gross_economic_leverage': 5, 'max_economic_leverage': 5, 'max_drawdown_budget': -0.25}
    res0 = quant.evaluate_policy(snap, investment_policy=pol)
    assert abs(res0['exposure_summary']['options_delta_equiv_weight'] - 0.70) < 1e-6
    res_add = quant.evaluate_policy(snap, {'symbol': 'AAPL CALL', 'conid': 'OPT1', 'asset': 'OPT', 'action': 'BUY', 'amount_base': '700', 'quantity': '1', 'account': 'margin'}, investment_policy=pol)
    assert abs(res_add['exposure_summary']['options_delta_equiv_weight'] - 1.40) < 1e-6

    # Out-of-range delta blocked
    snap_bad = {**snap, 'positions': [{**snap['positions'][0], 'delta': '1.5'}]}
    res_bad = quant.evaluate_policy(snap_bad, investment_policy=pol)
    assert res_bad['risk_coverage_complete'] is False and any(b['rule'] == 'option_delta_notional_unavailable' for b in res_bad['hard_blocks'])

def test_F09_gross_leverage_hard_gate_across_accounts(isolated_env):
    snap = {
        'status': 'ok', 'advice_allowed': True, 'as_of': date.today().isoformat(), 'nav': '10000', 'currency': 'CAD',
        'accounts': [{'account': 'margin', 'nav': '5000', 'cash': '1000'}, {'account': 'tfsa', 'nav': '5000', 'cash': '1000'}],
        'positions': [
            {'account': 'margin', 'conid': '1', 'symbol': 'SYN', 'asset': 'STK', 'currency': 'CAD', 'quantity': '100', 'value_base': '8000'},
            {'account': 'tfsa', 'conid': '1', 'symbol': 'SYN', 'asset': 'STK', 'currency': 'CAD', 'quantity': '-100', 'value_base': '-8000'},
        ]
    }
    res = quant.evaluate_policy(snap, mode='PROPOSED_PLAN')
    assert abs(res['exposure_summary']['gross_economic_exposure'] - 1.6) < 1e-6
    assert abs(res['exposure_summary']['economic_equity_exposure']) < 1e-6
    assert res['status'] == 'blocked_policy_violation' and any(b['rule'] == 'max_gross_economic_leverage' for b in res['hard_blocks'])

def test_F10_allocator_existing_cash_vs_new_contribution(isolated_env):
    _seed_two_accounts(cash_m='1000', cash_t='0', n_positions=0)
    d0 = date(2026, 1, 2)
    market._cache_bars('QQQ', [{'ts': (d0 + timedelta(days=i)).isoformat(), 'open': str(400 + i*0.2), 'close': str(400.1 + i*0.2), 'currency': 'USD'} for i in range(40)], 'yfinance', 'split_and_dividend_adj')
    res_exist = quant.allocate_incremental_capital('500', ['QQQ'], use_existing_cash=True)
    res_fresh = quant.allocate_incremental_capital('500', ['QQQ'], use_existing_cash=False)
    assert res_exist['status'] == 'ok' and res_fresh['status'] == 'ok'
    assert res_exist['snapshot_id'] != res_exist['post_snapshot_id']
    assert res_exist['post_snapshot_id'] != res_fresh['post_snapshot_id']

def test_F11_canadian_acb_pooled_split_dedup_and_fx(isolated_env):
    with core.db('finance') as c:
        finance.schema(c)
        c.execute('INSERT INTO records VALUES(?,?,?,?,?)', ('margin', 'trade', 'b1', json.dumps({'tradeDate': '20260105', 'symbol': 'ACBT', 'assetCategory': 'STK', 'buySell': 'BUY', 'quantity': '10', 'tradePrice': '10', 'currency': 'CAD'}), 's'))
        c.execute('INSERT INTO records VALUES(?,?,?,?,?)', ('taxable2', 'trade', 'b2', json.dumps({'tradeDate': '20260106', 'symbol': 'ACBT', 'assetCategory': 'STK', 'buySell': 'BUY', 'quantity': '10', 'tradePrice': '20', 'currency': 'CAD'}), 's'))
        c.execute('INSERT INTO records VALUES(?,?,?,?,?)', ('margin', 'corporate_action', 'sp1', json.dumps({'reportDate': '20260201', 'symbol': 'ACBT', 'assetCategory': 'STK', 'splitRatio': '2'}), 's'))
        c.execute('INSERT INTO records VALUES(?,?,?,?,?)', ('taxable2', 'corporate_action', 'sp2', json.dumps({'reportDate': '20260201', 'symbol': 'ACBT', 'assetCategory': 'STK', 'splitRatio': '2'}), 's'))
        c.execute('INSERT INTO records VALUES(?,?,?,?,?)', ('margin', 'trade', 's1', json.dumps({'tradeDate': '20260301', 'symbol': 'ACBT', 'assetCategory': 'STK', 'buySell': 'SELL', 'quantity': '10', 'tradePrice': '12', 'currency': 'CAD'}), 's'))
    acb = decision.compute_canadian_acb('ACBT')
    assert acb['acb_pools'][0]['shares_held'] == '30'
    assert Decimal(acb['realized_dispositions'][0]['realized_gain_base']) == Decimal('45.00')

def test_F12_pnl_bridge_and_option_attribution_units(isolated_env):
    with core.db('finance') as c:
        finance.schema(c)
        for acct, v0, v1 in [('margin', '500', '568.5'), ('tfsa', '500', '568.5')]:
            c.execute('INSERT INTO nav VALUES(?,?,?,?,?,?)', (acct, '2026-11-15', 'CAD', v0, v0, 's'))
            c.execute('INSERT INTO nav VALUES(?,?,?,?,?,?)', (acct, '2026-11-17', 'CAD', v1, v1, 's'))
            c.execute('INSERT INTO snapshots VALUES(?,?,?,1)', (acct, '2026-11-17', 's'))
        c.execute('INSERT INTO records VALUES(?,?,?,?,?)', ('margin', 'cash', 'd1', json.dumps({'type': 'deposit', 'amount': '100', 'currency': 'CAD', 'reportDate': '20261116'}), 's'))
        c.execute('INSERT INTO records VALUES(?,?,?,?,?)', ('margin', 'cash', 'dv1', json.dumps({'type': 'dividends', 'amount': '14', 'currency': 'CAD', 'reportDate': '20261116'}), 's'))
        c.execute('INSERT INTO records VALUES(?,?,?,?,?)', ('margin', 'trade', 'tr1', json.dumps({'tradeDate': '20261116', 'symbol': 'OPTU', 'assetCategory': 'OPT', 'buySell': 'BUY', 'quantity': '1', 'multiplier': '100', 'tradePrice': '5', 'currency': 'USD', 'fxRateToBase': '1', 'ibCommission': '-7', 'ibCommissionCurrency': 'CAD'}), 's'))
    br = decision.daily_pnl_bridge(days=5)
    assert br['reconciles_to_nav'] is True
    assert Decimal(br['starting_nav_base']) + Decimal(br['external_flows_base']) + Decimal(br['dividends_and_interest_base']) - Decimal(br['commissions_base']) + Decimal(br['market_and_fx_pnl_base']) == Decimal(br['ending_nav_base'])
