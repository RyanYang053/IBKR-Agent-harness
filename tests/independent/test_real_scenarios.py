"""Independent, executable oracle tests against Personal Investment OS PRODUCTION functions.

These are intentionally adversarial. Some are expected to FAIL on the audited
4563cf72 branch; their purpose is to expose defects, not manufacture a green CI.
No real IBKR credentials, live orders, paid APIs, or network traffic are used.

Run from the target repository root:
    python -m pytest tests/independent/test_real_scenarios.py -v -ra
"""
from __future__ import annotations

import importlib
import json
import math
import urllib.request
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def osys(tmp_path, monkeypatch):
    """Real production modules, isolated real SQLite, no production account data."""
    cfg = {
        'timezone': 'America/Toronto',
        'knowledge_roots': [],
        'finance': {
            'accounts': {'U_TEST_MARGIN': 'margin', 'U_TEST_TFSA': 'tfsa'},
            'account_tax_types': {'margin': 'taxable', 'tfsa': 'tfsa'},
            'account_constraints': {'margin': {'allow_margin': True}, 'tfsa': {'allow_margin': False}},
            'base_currency': 'CAD', 'flex_queries': [], 'max_snapshot_age_days': 100,
        },
        'market': {
            'watchlist': ['QQQ'], 'alpaca_key_env': 'DO_NOT_SET_ALPACA_KEY',
            'alpaca_secret_env': 'DO_NOT_SET_ALPACA_SECRET',
            'finnhub_key_env': 'DO_NOT_SET_FINNHUB_KEY',
            'fred_key_env': 'DO_NOT_SET_FRED_KEY',
            'edgar_contact': 'qa@example.com',
        },
        'policy': {'max_tool_rows': 50, 'max_file_bytes': 10000000},
    }
    p = tmp_path / 'qa_config.json'
    p.write_text(json.dumps(cfg), encoding='utf-8')
    monkeypatch.setenv('PERSONAL_AGENT_HOME', str(tmp_path / 'db'))
    monkeypatch.setenv('PERSONAL_AGENT_CONFIG', str(p))
    monkeypatch.delenv('INVESTMENT_OS_PUBLIC_ONLY', raising=False)
    for k in ('IBKR_FLEX_TOKEN', 'DO_NOT_SET_ALPACA_KEY', 'DO_NOT_SET_ALPACA_SECRET',
              'DO_NOT_SET_FINNHUB_KEY', 'DO_NOT_SET_FRED_KEY'):
        monkeypatch.delenv(k, raising=False)
    import personal_agent.core as core
    import personal_agent.finance as finance
    import personal_agent.market as market
    import personal_agent.knowledge as knowledge
    import personal_agent.decision as decision
    import personal_agent.quant as quant
    import personal_agent.broker as broker
    import personal_agent.ops as ops
    # core.HOME / core.CONFIG are resolved at import time.
    for mod in (core, finance, market, knowledge, decision, quant, broker, ops):
        importlib.reload(mod)
    local_policy = tmp_path / 'config'
    local_policy.mkdir()
    (local_policy / 'investment_policy.example.json').write_text(json.dumps({
        'core_symbols': ['QQQ'], 'core_benchmark': 'QQQ', 'core_min_weight': 0,
        'tactical_max_weight': 10, 'min_cash_reserve': 0,
        'max_single_stock_weight': 10, 'max_correlated_cluster_weight': 10,
        'max_economic_leverage': 10, 'max_gross_economic_leverage': 10,
        'max_drawdown_budget': -0.9, 'approval_ttl_hours': 48,
        'account_constraints': {'tfsa': {'allow_margin': False}, 'margin': {'allow_margin': True}}
    }), encoding='utf-8')
    monkeypatch.setattr(quant, 'ROOT', tmp_path)
    # Strict no-network tests: patch only network *boundary*, not code under test.
    with patch.object(urllib.request, 'urlopen', side_effect=AssertionError('Offline test attempted outbound HTTP')):
        yield {'root': tmp_path, 'core': core, 'finance': finance, 'market': market,
               'knowledge': knowledge, 'decision': decision, 'quant': quant,
               'broker': broker, 'ops': ops}


def seed_ledger(s, *, date_m=None, date_t=None, nav_m='10000', nav_t='5000',
                cash_m='9000', cash_t='5000', n_positions=1, position_value='1000'):
    today = date.today().isoformat()
    dm, dt = date_m or today, date_t or today
    core, finance = s['core'], s['finance']
    with core.db('finance') as c:
        finance.schema(c)
        c.execute('INSERT OR IGNORE INTO sources VALUES(?,?)', ('seed', datetime.now(timezone.utc).isoformat()))
        for a, d, n, cash in [('margin', dm, nav_m, cash_m), ('tfsa', dt, nav_t, cash_t)]:
            c.execute('INSERT INTO nav VALUES(?,?,?,?,?,?)', (a, d, 'CAD', n, cash, 'seed'))
            c.execute('INSERT INTO snapshots VALUES(?,?,?,1)', (a, d, 'seed'))
        for i in range(n_positions):
            account = 'margin' if i % 2 == 0 else 'tfsa'
            c.execute('''INSERT INTO positions(account,date,conid,symbol,asset,currency,quantity,
                       value_base,underlying_conid,cost_basis,fx_rate)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                      (account, dm if account == 'margin' else dt, f'C{i:03}',
                       f'QA{i:03}', 'STK', 'CAD', '10', str(position_value), f'U{i:03}', str(D(position_value)*D('0.8')), '1'))
    return today


def sample_snapshot(s, *, cash='1000', buying_power='3000', qty='10',
                    mv='1000', base_nav='10000', contract_id='C100', cost_basis='800'):
    f = s['finance']
    acct = f.AccountRecord('margin', 'taxable', D(base_nav), D(cash), True,
                           D(buying_power) if buying_power is not None else None, 'qa')
    pos = f.PositionRecord('margin', contract_id, 'XYZ', 'STK', 'CAD', D(qty),
                           D(1), D(mv), underlying_conid='UNDERLYING_C100',
                           cost_basis=D(cost_basis), fx_rate_to_base=D(1), source='qa')
    sid = f.compute_snapshot_id(date.today().isoformat(), 'CAD', (acct,), (pos,))
    return f.PortfolioSnapshot(sid, date.today().isoformat(), 'ok', False, True, True,
                               'CAD', D(base_nav), D(cash), (acct,), (pos,))


# ------------------- Portfolio ledger and returns -------------------

def test_R01_full_120_position_snapshot_not_ui_truncated(osys):
    seed_ledger(osys, n_positions=120, position_value='50', cash_m='7000', cash_t='2000')
    f, core = osys['finance'], osys['core']
    cfgp = osys['root'] / 'qa_config.json'
    cfg = json.loads(cfgp.read_text()); cfg['policy']['max_tool_rows'] = 1
    cfgp.write_text(json.dumps(cfg))
    full = f.load_full_portfolio_snapshot()
    page = f.portfolio()
    assert full.positions_complete and len(full.positions) == 120
    assert len(page['positions']) == 1
    assert full.nav == D('15000')


def test_R02_account_statement_date_mismatch_blocks_advice(osys):
    today = date.today()
    seed_ledger(osys, date_m=today.isoformat(), date_t=(today - timedelta(days=1)).isoformat())
    snap = osys['finance'].load_full_portfolio_snapshot()
    assert snap.status == 'date_mismatch' and not snap.advice_allowed


def test_R03_unknown_foreign_cash_flow_requires_explicit_fx(osys):
    with pytest.raises(osys['finance'].MissingFxRateError):
        osys['finance']._flow_amount({'currency': 'USD', 'amount': '100'}, base_currency='CAD')


def test_R04_cash_contribution_is_not_investment_profit(osys):
    core, f = osys['core'], osys['finance']
    a, b = (date.today() - timedelta(days=1)).isoformat(), date.today().isoformat()
    with core.db('finance') as c:
        f.schema(c)
        c.execute('INSERT INTO sources VALUES(?,?)', ('syn', datetime.now(timezone.utc).isoformat()))
        for account in ('margin', 'tfsa'):
            for d, nav in ((a, '500'), (b, '570')):
                c.execute('INSERT INTO nav VALUES(?,?,?,?,?,?)', (account, d, 'CAD', nav, nav, 'syn'))
            c.execute('INSERT INTO snapshots VALUES(?,?,?,1)', (account, b, 'syn'))
        c.execute('INSERT INTO records VALUES(?,?,?,?,?)', ('margin', 'cash', 'in1', json.dumps({
            'type': 'deposit', 'amount': '100', 'currency': 'USD', 'fxRateToBase': '1.4',
            'reportDate': b.replace('-', ''),
        }), 'syn'))
    perf = f.performance()
    assert perf['twr'] is not None, perf
    assert abs(D(perf['twr'])) < D('1e-10')
    assert D(perf['contributions']) == D('140')
    assert perf['twr_exact'] is False


def test_R05_unmatched_transfer_must_block_combined_returns(osys):
    seed_ledger(osys)
    f, core = osys['finance'], osys['core']
    with core.db('finance') as c:
        c.execute('INSERT INTO records VALUES(?,?,?,?,?)', ('margin', 'cash', 'tr1', json.dumps({
            'type': 'transfer', 'amount': '50', 'currency': 'CAD', 'reportDate': date.today().strftime('%Y%m%d')
        }), 'seed'))
    perf = f.performance()
    assert perf['status'] == 'unmatched_transfers', perf
    assert perf['twr'] is None


# ------------------- Trade simulation and risk invariants -------------------

def test_R06_buy_preserves_account_cash_nav_and_lot_identity(osys):
    q = osys['quant']; base = sample_snapshot(osys)
    out = q.simulate_allocation(base, proposed_trades=[{
        'symbol': 'XYZ', 'conid': 'C100', 'account': 'margin', 'action': 'BUY',
        'asset': 'STK', 'currency': 'CAD', 'quantity': '2',
        'price_per_unit': '100', 'amount_base': '200', 'estimated_fees_base': '1',
    }])
    assert out.nav == D('9999')
    assert out.accounts[0].cash == D('799')
    assert out.accounts[0].buying_power == D('2799')
    assert out.positions[0].quantity == D('12')
    assert out.positions[0].cost_basis == D('1001'), ('After BUY 2 at CAD100 with CAD1 fees, aggregate cost basis should be 800+201, or explicitly be marked unavailable—not silently stay 800')
    assert out.positions[0].underlying_conid == 'UNDERLYING_C100'


def test_R07_unknown_margin_buying_power_blocks_new_borrowing(osys):
    q = osys['quant']; snap = sample_snapshot(osys, cash='100', buying_power=None)
    with pytest.raises(ValueError, match='buying_power'):
        q.simulate_allocation(snap, proposed_trades=[{
            'symbol': 'XYZ', 'conid': 'C100', 'account': 'margin', 'action': 'BUY',
            'quantity': '5', 'price_per_unit': '100', 'amount_base': '500'
        }])


def test_R08_oversell_is_rejected_by_quantity_not_just_market_value(osys):
    q = osys['quant']; snap = sample_snapshot(osys)
    with pytest.raises(ValueError, match='insufficient_account_position'):
        q.simulate_allocation(snap, proposed_trades=[{
            'symbol': 'XYZ', 'conid': 'C100', 'account': 'margin', 'action': 'SELL',
            'quantity': '11', 'amount_base': '100', 'price_per_unit': str(100 / 11)
        }])


def test_R09_quantity_amount_without_price_is_not_executable_trade(osys):
    """A held position cannot change both quantity and value by unrelated amounts."""
    q = osys['quant']; snap = sample_snapshot(osys)
    with pytest.raises(ValueError, match='(price|inconsistent|executable|missing)'):
        q.simulate_allocation(snap, proposed_trades=[{
            'symbol': 'XYZ', 'conid': 'C100', 'account': 'margin', 'action': 'SELL',
            'quantity': '2', 'amount_base': '10'
        }])


def test_R10_same_symbol_different_contract_without_conid_must_not_guess(osys):
    q = osys['quant']; f = osys['finance']; base = sample_snapshot(osys)
    another = f.PositionRecord('margin', 'C200', 'XYZ', 'STK', 'CAD', D(4), D(1), D(400),
                               underlying_conid='U200', fx_rate_to_base=D(1))
    snap = f.PortfolioSnapshot(base.snapshot_id, base.as_of, 'ok', False, True, True,
                               base.currency, base.nav, base.cash, base.accounts,
                               base.positions + (another,))
    with pytest.raises(ValueError, match='(ambiguous|conid|contract)'):
        q.simulate_allocation(snap, proposed_trades=[{
            'symbol': 'XYZ', 'account': 'margin', 'action': 'SELL',
            'quantity': '1', 'price_per_unit': '100', 'amount_base': '100'
        }])


def test_R11_value_with_zero_quantity_is_not_a_valid_new_stock_position(osys):
    q = osys['quant']; base = sample_snapshot(osys)
    with pytest.raises(ValueError, match='(quantity|price|executable)'):
        q.simulate_allocation(base, proposed_trades=[{
            'symbol': 'NEWQA', 'conid': 'N001', 'account': 'margin',
            'action': 'BUY', 'amount_base': '100', 'asset': 'STK'
        }])


def test_R12_fx_and_contract_multiplier_are_applied_to_actual_trade(osys):
    q = osys['quant']; base = sample_snapshot(osys, cash='1000')
    out = q.simulate_allocation(base, proposed_trades=[{
        'symbol': 'USDQ', 'conid': 'USDQ_1', 'account': 'margin', 'action': 'BUY',
        'asset': 'STK', 'currency': 'USD', 'quantity': '1', 'price_per_unit': '100',
        'fx_rate_to_base': '1.4', 'amount_base': '140'
    }])
    assert out.accounts[0].cash == D('860')
    new = next(p for p in out.positions if p.conid == 'USDQ_1')
    assert new.quantity == D('1') and new.currency == 'USD'
    assert new.fx_rate_to_base == D('1.4')


def test_R13_snapshot_id_changes_when_risk_relevant_lot_metadata_changes(osys):
    from dataclasses import replace
    f = osys['finance']; base = sample_snapshot(osys)
    a, p = base.accounts, base.positions[0]
    ids = [f.compute_snapshot_id(base.as_of, 'CAD', a, (x,)) for x in (
        p, replace(p, cost_basis=D('999')), replace(p, underlying_conid='DIFFERENT_CONID')
    )]
    assert len(set(ids)) == 3, f'Approval hash incorrectly aliases different cost/underlying states: {ids}'


def test_R14_option_delta_exposure_matches_signed_notional_oracle(osys):
    q = osys['quant']
    snap = {'status': 'ok', 'advice_allowed': True, 'positions_complete': True,
            'as_of': date.today().isoformat(), 'nav': '10000', 'currency': 'CAD',
            'accounts': [{'account': 'margin', 'nav': '10000', 'cash': '1000',
                          'allow_margin': True, 'buying_power': '5000'}],
            'positions': [{'account': 'margin', 'conid': 'OPT1', 'symbol': 'OPT1',
                           'asset': 'OPT', 'currency': 'USD', 'quantity': '1',
                           'multiplier': '100', 'value_base': '0', 'underlying_price': '100',
                           'underlying_currency': 'USD', 'fx_rate_to_base': '1.4', 'delta': '0.5'}]}
    res = q.evaluate_policy(snap, mode='AUDIT', risk_context={'twr': None, 'max_drawdown': '0'})
    assert abs(res['exposure_summary']['options_delta_equiv_weight'] - 0.7) < 1e-9
    assert res['risk_coverage_complete'] is True


# ------------------- Broker cache and freshness -------------------

def test_R15_partial_or_replay_capture_cannot_enrich_investment_state(osys):
    broker = osys['broker']; snap = sample_snapshot(osys)
    base = snap.snapshot_id
    broker._persist_broker_snapshot('ibkr_tws_readonly', datetime.now(timezone.utc).isoformat(),
        {'U_TEST_MARGIN': {'BuyingPower': '9999', 'currency': 'CAD'}}, [], {}, {},
        capture_status='partial', live_verified=False)
    assert broker.enrich_snapshot_with_broker(snap).snapshot_id == base
    broker.sync_broker_replay({'as_of': datetime.now(timezone.utc).isoformat(),
        'accounts': {'U_TEST_MARGIN': {'BuyingPower': '9999', 'currency': 'CAD'}}, 'positions': []})
    assert broker.enrich_snapshot_with_broker(snap).snapshot_id == base


def test_R16_stale_live_broker_snapshot_must_not_enrich(osys):
    broker = osys['broker']; snap = sample_snapshot(osys)
    yesterday = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    broker._persist_broker_snapshot('ibkr_tws_readonly', yesterday,
        {'U_TEST_MARGIN': {'BuyingPower': '50000', 'currency': 'CAD'}}, [], {}, {},
        capture_status='ok', live_verified=True)
    assert broker.enrich_snapshot_with_broker(snap, max_age_seconds=300).snapshot_id == snap.snapshot_id


def test_R17_broker_buying_power_usd_must_be_converted_or_marked_unavailable(osys):
    broker = osys['broker']; snap = sample_snapshot(osys, buying_power='300')
    broker._persist_broker_snapshot('ibkr_tws_readonly', datetime.now(timezone.utc).isoformat(),
        {'U_TEST_MARGIN': {'BuyingPower': '500', 'currency': 'USD', 'fx_rate_to_base': '1.4'}},
        [], {}, {}, capture_status='ok', live_verified=True)
    enriched = broker.enrich_snapshot_with_broker(snap)
    assert enriched.accounts[0].buying_power == D('700'), (
        'BuyingPower is USD 500 at FX 1.4 CAD/USD and must become CAD 700, not 500'
    )


def test_R18_old_live_capture_must_not_reconcile_as_current_state(osys):
    seed_ledger(osys, n_positions=0, nav_m='1000', nav_t='1000', cash_m='1000', cash_t='1000')
    broker = osys['broker']
    broker._persist_broker_snapshot('ibkr_tws_readonly', '2020-01-01T12:00:00+00:00',
        {'U_TEST_MARGIN': {'NetLiquidation': '1000', 'currency': 'CAD'},
         'U_TEST_TFSA': {'NetLiquidation': '1000', 'currency': 'CAD'}},
        [], {}, {}, capture_status='ok', live_verified=True)
    got = broker.reconcile_live_to_flex()
    assert got['status'] != 'reconciled', 'A 2020 broker capture is not a present-day live reconciliation'


# ------------------- SEC fundamentals and investment decisions -------------------

def test_R19_as_of_restatement_never_uses_future_publication(osys):
    m = osys['market']
    m.record_financial_fact('QAFACT', '2026Q2', 'IS', 'Revenue', 100, '2026-05-01')
    m.record_financial_fact('QAFACT', '2026Q2', 'IS', 'Revenue', 120, '2026-08-01')
    june = m.get_facts_pit('QAFACT', as_of='2026-06-30', period='2026Q2')['facts']
    sept = m.get_facts_pit('QAFACT', as_of='2026-09-01', period='2026Q2')['facts']
    assert june[0]['value'] == 100 and sept[0]['value'] == 120


def test_R20_incomplete_quarters_cannot_be_called_ttm(osys):
    m = osys['market']
    for p in ('2026Q1', '2026Q2', '2026Q4'):
        m.record_financial_fact('QAGAP', p, 'IS', 'Revenue', 100, '2026-11-01')
    got = m.compute_ttm_and_ratios('QAGAP', as_of='2026-11-15')
    assert got['status'] != 'ok'


def test_R21_missing_fcf_growth_and_dilution_are_not_zero_return_forecasts(osys):
    m = osys['market']
    for fy, rev, ebit in [('FY2025', 100, 20), ('FY2026', 110, 30)]:
        for concept, value in [('Revenue', rev), ('OperatingIncome', ebit),
                               ('TotalDebt', 10), ('ShareholdersEquity', 100), ('Cash', 10)]:
            m.record_financial_fact('QAMISS', fy, 'IS' if concept in {'Revenue','OperatingIncome'} else 'BS',
                                    concept, value, '2026-11-01')
    f = m.generate_quality_features('QAMISS', as_of='2026-11-15')
    assert f is not None, 'Fixture has enough inputs to compute rough ROIC'
    assert f.get('fcf_growth') is None and f.get('dilution_rate') is None, (
        'Missing FCF and share history is UNKNOWN, not observed 0% growth/dilution'
    )


def test_R22_earnings_actuals_must_not_be_backdated_as_pre_earnings_forecast(osys):
    q = osys['quant']
    with pytest.raises(ValueError, match='(actual|expectation|retrospective|pre_earnings)'):
        q.record_earnings_expectation('QAEPS', '2026Q4', exp_revenue=100, act_revenue=108)


def test_R23_beat_without_guidance_raise_must_not_claim_beat_and_raise(osys):
    q = osys['quant']
    q.record_earnings_expectation('QABEAT', '2026Q4', exp_revenue=100, exp_eps=1)
    got = q.record_earnings_expectation('QABEAT', '2026Q4', act_revenue=108, act_eps=1.1)
    assert got['verdict'] != 'positive_beat_and_raise', got


def test_R24_unprofitable_bear_case_must_not_be_forced_positive_margin(osys):
    q = osys['quant']
    v = q.driver_based_valuation('1000', '0.10', '-0.08',
                                  op_margin_t0='-0.10', bear_margin_delta='-0.03')
    assert v['scenarios']['bear']['operating_margin_target'] < 0, v['scenarios']['bear']


def test_R25_dcf_oracle_perpetuity_100_fcf_10pct_wacc_2pct_g(osys):
    result = osys['decision'].dcf('100', '0.10', '0.02', '200', '10')
    assert D(result['enterprise_value']) == D('1250')
    assert D(result['per_share']) == D('105')


def test_R26_bsm_closed_form_atm_one_year_european_call(osys):
    res = osys['quant']._bsm(100, 100, 365, 0.0, 0.2, 'call')
    assert abs(res['theoretical_price'] - 7.9656) < 0.001
    assert 0 < res['nd2_itm_prob'] < res['delta'] < 1


def test_R27_missing_open_price_remains_missing_to_backtester(osys):
    m, q = osys['market'], osys['quant']
    begin = date(2026, 1, 5)
    bars = [{'ts': (begin + timedelta(days=i)).isoformat(),
             'open': None if i == 20 else '100', 'close': '100', 'currency': 'USD'}
            for i in range(60)]
    m._cache_bars('OPENQA', bars, 'synthetic', 'split_and_dividend_adj')
    hist = m.bars('OPENQA', 100)
    assert hist[20]['open'] is None
    got = q.simulate_cashflow_strategy('OPENQA', '10000', '0', 'buy_and_hold')
    assert got['status'] == 'missing_open_price', got


def test_R28_prediction_brier_is_computed_from_actual_outcomes(osys):
    k = osys['knowledge']
    p1 = k.record_forecast_prediction('QAPRED', 'Event A', '2027-01-01', 0.8)
    k.resolve_forecast_prediction(p1['id'], 1, actual_event_at='2027-01-02T00:00:00+00:00')
    got = k.brier_calibration_report()
    assert got['resolved_count'] == 1 and abs(got['brier_score'] - 0.04) < 1e-9


# ------------------- Privacy, restore, approval workflow -------------------

def test_R29_public_mode_cannot_open_local_private_sqlite(osys, monkeypatch):
    monkeypatch.setenv('INVESTMENT_OS_PUBLIC_ONLY', '1')
    with pytest.raises(PermissionError):
        with osys['core'].db('finance'):
            pass
    with pytest.raises(PermissionError):
        with osys['core'].db('knowledge'):
            pass


def test_R30_real_sqlite_backup_restore_preserves_rows(osys):
    core, f, k = osys['core'], osys['finance'], osys['knowledge']
    seed_ledger(osys, n_positions=2, cash_t='4000')
    k.save('fact', 'QA nonsecret fact', 'operator')
    bk = core.backup_databases(osys['root'] / 'safe_backup')
    out = core.restore_databases(bk['backup_dir'], osys['root'] / 'safe_restore')
    assert out['status'] == 'ok'
    restored = osys['root'] / 'safe_restore' / 'finance.sqlite3'
    import sqlite3
    with sqlite3.connect(restored) as c:
        assert c.execute('SELECT COUNT(*) FROM positions').fetchone()[0] == 2
        assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_R31_real_approval_journal_has_exactly_one_human_commit(osys):
    ops = osys['ops']
    wf = ops.run_workflow('equity_valuation_and_expectations', {
        'symbol': 'QQQ', 'propose_thesis': 'QA synthetic thesis, not a trading order',
        'falsifier': 'Revenue declines for two consecutive reports',
    })
    assert wf['status'] == 'awaiting_approval'
    r1 = ops.approve_workflow(wf['run_id'], 'QA approve')
    r2 = ops.approve_workflow(wf['run_id'], 'QA approve')
    assert r1['status'] == r2['status'] == 'completed'
    with osys['core'].db('knowledge') as c:
        assert c.execute('SELECT COUNT(*) FROM memory WHERE approval_run_id=?',
                         (wf['run_id'],)).fetchone()[0] == 1


def test_R32_offline_replay_cannot_certify_live_shadow_mode(osys):
    seed_ledger(osys, n_positions=0, nav_m='1000', nav_t='1000', cash_m='1000', cash_t='1000')
    b = osys['broker']
    b.sync_broker_replay({'as_of': datetime.now(timezone.utc).isoformat(),
        'accounts': {'U_TEST_MARGIN': {'NetLiquidation': '1000', 'currency': 'CAD'},
                     'U_TEST_TFSA': {'NetLiquidation': '1000', 'currency': 'CAD'}},
        'positions': []})
    res = osys['ops'].shadow_mode_check()
    assert res['production_accepted'] is False
    assert res['status'] != 'ok'
