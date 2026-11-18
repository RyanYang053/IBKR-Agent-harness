from decimal import Decimal
import json
from unittest.mock import patch
import pytest
from personal_agent import core, decision, knowledge, market, quant

def _sample_companyfacts():
    return {'facts': {'us-gaap': {
        'Revenues': {'units': {'USD': [
            {'form': '10-Q', 'fp': 'Q1', 'fy': 2026, 'start': '2025-10-01', 'end': '2025-12-31', 'filed': '2026-02-01', 'val': 100.0},
            {'form': '10-Q', 'fp': 'Q2', 'fy': 2026, 'start': '2026-01-01', 'end': '2026-03-31', 'filed': '2026-05-01', 'val': 110.0},
            {'form': '10-Q', 'fp': 'Q3', 'fy': 2026, 'start': '2026-04-01', 'end': '2026-06-30', 'filed': '2026-08-01', 'val': 120.0},
            {'form': '10-K', 'fp': 'FY', 'fy': 2026, 'start': '2025-10-01', 'end': '2026-09-30', 'filed': '2026-11-10', 'val': 460.0},
            {'form': '10-K', 'fp': 'FY', 'fy': 2025, 'start': '2024-10-01', 'end': '2025-09-30', 'filed': '2025-11-10', 'val': 400.0},
        ]}},
        'OperatingIncomeLoss': {'units': {'USD': [
            {'form': '10-Q', 'fp': 'Q1', 'fy': 2026, 'start': '2025-10-01', 'end': '2025-12-31', 'filed': '2026-02-01', 'val': 30.0},
            {'form': '10-Q', 'fp': 'Q2', 'fy': 2026, 'start': '2026-01-01', 'end': '2026-03-31', 'filed': '2026-05-01', 'val': 35.0},
            {'form': '10-Q', 'fp': 'Q3', 'fy': 2026, 'start': '2026-04-01', 'end': '2026-06-30', 'filed': '2026-08-01', 'val': 35.0},
            {'form': '10-K', 'fp': 'FY', 'fy': 2026, 'start': '2025-10-01', 'end': '2026-09-30', 'filed': '2026-11-10', 'val': 140.0},
            {'form': '10-K', 'fp': 'FY', 'fy': 2025, 'start': '2024-10-01', 'end': '2025-09-30', 'filed': '2025-11-10', 'val': 110.0},
        ]}},
        'NetCashProvidedByUsedInOperatingActivities': {'units': {'USD': [
            {'form': '10-Q', 'fp': 'Q1', 'fy': 2026, 'start': '2025-10-01', 'end': '2025-12-31', 'filed': '2026-02-01', 'val': 100.0},
            {'form': '10-Q', 'fp': 'Q2', 'fy': 2026, 'start': '2025-10-01', 'end': '2026-03-31', 'filed': '2026-05-01', 'val': 200.0},
            {'form': '10-Q', 'fp': 'Q3', 'fy': 2026, 'start': '2025-10-01', 'end': '2026-06-30', 'filed': '2026-08-01', 'val': 300.0},
            {'form': '10-K', 'fp': 'FY', 'fy': 2026, 'start': '2025-10-01', 'end': '2026-09-30', 'filed': '2026-11-10', 'val': 400.0},
            {'form': '10-K', 'fp': 'FY', 'fy': 2025, 'start': '2024-10-01', 'end': '2025-09-30', 'filed': '2025-11-10', 'val': 350.0},
        ]}},
        'PaymentsToAcquirePropertyPlantAndEquipment': {'units': {'USD': [
            {'form': '10-Q', 'fp': 'Q1', 'fy': 2026, 'start': '2025-10-01', 'end': '2025-12-31', 'filed': '2026-02-01', 'val': 10.0},
            {'form': '10-Q', 'fp': 'Q2', 'fy': 2026, 'start': '2025-10-01', 'end': '2026-03-31', 'filed': '2026-05-01', 'val': 20.0},
            {'form': '10-Q', 'fp': 'Q3', 'fy': 2026, 'start': '2025-10-01', 'end': '2026-06-30', 'filed': '2026-08-01', 'val': 30.0},
            {'form': '10-K', 'fp': 'FY', 'fy': 2026, 'start': '2025-10-01', 'end': '2026-09-30', 'filed': '2026-11-10', 'val': 40.0},
            {'form': '10-K', 'fp': 'FY', 'fy': 2025, 'start': '2024-10-01', 'end': '2025-09-30', 'filed': '2025-11-10', 'val': 40.0},
        ]}},
        'StockholdersEquity': {'units': {'USD': [
            {'form': '10-K', 'fp': 'FY', 'fy': 2026, 'end': '2026-09-30', 'filed': '2026-11-10', 'val': 300.0},
            {'form': '10-K', 'fp': 'FY', 'fy': 2025, 'end': '2025-09-30', 'filed': '2025-11-10', 'val': 280.0},
        ]}},
        'LongTermDebt': {'units': {'USD': [
            {'form': '10-K', 'fp': 'FY', 'fy': 2026, 'end': '2026-09-30', 'filed': '2026-11-10', 'val': 50.0},
            {'form': '10-K', 'fp': 'FY', 'fy': 2025, 'end': '2025-09-30', 'filed': '2025-11-10', 'val': 50.0},
        ]}},
        'CashAndCashEquivalentsAtCarryingValue': {'units': {'USD': [
            {'form': '10-K', 'fp': 'FY', 'fy': 2026, 'end': '2026-09-30', 'filed': '2026-11-10', 'val': 100.0},
            {'form': '10-K', 'fp': 'FY', 'fy': 2025, 'end': '2025-09-30', 'filed': '2025-11-10', 'val': 100.0},
        ]}},
        'WeightedAverageNumberOfDilutedSharesOutstanding': {'units': {'shares': [
            {'form': '10-K', 'fp': 'FY', 'fy': 2026, 'end': '2026-09-30', 'filed': '2026-11-10', 'val': 10.0},
            {'form': '10-K', 'fp': 'FY', 'fy': 2025, 'end': '2025-09-30', 'filed': '2025-11-10', 'val': 10.0},
        ]}},
    }}}

def test_C01_sync_filings_transport_populates_equity_and_pit_without_manual_inserts(isolated_env):
    with patch.object(market, '_cik', return_value='0000123456'), patch.object(market, '_request', return_value=_sample_companyfacts()):
        res = market.sync_filings('TECH1')
    assert res['status'] == 'ok' and res['pit_facts'] > 0
    pit = market.get_facts_pit('TECH1', as_of='2026-11-15')
    concepts = {f['concept'] for f in pit['facts']}
    assert {'Revenue', 'OperatingIncome', 'OperatingCashFlow', 'CapEx', 'ShareholdersEquity', 'TotalDebt', 'Cash', 'DilutedShares'}.issubset(concepts)

def test_C02_comparative_prior_year_facts_same_fy_fp_retained(isolated_env):
    cf = {'facts': {'us-gaap': {'Revenues': {'units': {'USD': [
        {'form': '10-Q', 'fp': 'Q2', 'fy': 2026, 'start': '2026-04-01', 'end': '2026-06-30', 'filed': '2026-08-01', 'val': 120.0},
        {'form': '10-Q', 'fp': 'Q2', 'fy': 2026, 'start': '2025-04-01', 'end': '2025-06-30', 'filed': '2026-08-01', 'val': 90.0},
    ]}}}}}
    market._normalize_sec_xbrl_facts('CMPCO', '0000111111', cf)
    q26 = market.get_facts_pit('CMPCO', as_of='2026-09-01', period='2026Q2')['facts']
    q25 = market.get_facts_pit('CMPCO', as_of='2026-09-01', period='2025Q2')['facts']
    assert q26[0]['value'] == 120.0 and q25[0]['value'] == 90.0

def test_C03_pit_selects_by_publication_time_not_insertion_order(isolated_env):
    market.record_financial_fact('REVCO', '2026Q2', 'IS', 'Revenue', 120.0, filed_at='2026-08-01')
    market.record_financial_fact('REVCO', '2026Q2', 'IS', 'Revenue', 100.0, filed_at='2026-05-01')
    assert market.get_facts_pit('REVCO', as_of='2026-06-01', period='2026Q2')['facts'][0]['value'] == 100.0
    assert market.get_facts_pit('REVCO', as_of='2026-09-01', period='2026Q2')['facts'][0]['value'] == 120.0

def test_C04_ytd_differencing_and_component_availability(isolated_env):
    cf = {'facts': {'us-gaap': {'NetCashProvidedByUsedInOperatingActivities': {'units': {'USD': [
        {'form': '10-Q', 'fp': 'Q1', 'fy': 2026, 'start': '2026-01-01', 'end': '2026-03-31', 'filed': '2026-02-01', 'val': 100.0},
        {'form': '10-Q', 'fp': 'Q2', 'fy': 2026, 'start': '2026-01-01', 'end': '2026-06-30', 'filed': '2026-05-01', 'val': 210.0},
        {'form': '10-Q', 'fp': 'Q1', 'fy': 2026, 'start': '2026-01-01', 'end': '2026-03-31', 'filed': '2026-08-01', 'val': 110.0},
        {'form': '10-Q', 'fp': 'Q3', 'fy': 2026, 'start': '2026-01-01', 'end': '2026-09-30', 'filed': '2026-08-01', 'val': 330.0},
        {'form': '10-K', 'fp': 'FY', 'fy': 2026, 'start': '2026-01-01', 'end': '2026-12-31', 'filed': '2026-11-01', 'val': 460.0},
    ]}}}}}
    market._normalize_sec_xbrl_facts('YTDCO', '0000222222', cf)
    assert market.get_facts_pit('YTDCO', as_of='2026-06-01', period='2026Q2')['facts'][0]['value'] == 110.0
    assert market.get_facts_pit('YTDCO', as_of='2026-09-01', period='2026Q3')['facts'][0]['value'] == 120.0
    assert market.get_facts_pit('YTDCO', as_of='2026-11-15', period='2026Q4')['facts'][0]['value'] == 130.0

def test_C05_nonadditive_shares_and_bs_not_summed(isolated_env):
    with patch.object(market, '_cik', return_value='0000123456'), patch.object(market, '_request', return_value=_sample_companyfacts()):
        market.sync_filings('SHRCO')
    ttm = market.compute_ttm_and_ratios('SHRCO', as_of='2026-11-15')
    assert ttm['ttm_metrics']['revenue'] == 460.0
    assert ttm['ratios']['roic_pre_tax_approx'] is not None

def test_C06_non_contiguous_quarters_block_ttm(isolated_env):
    for q in ('2025Q4', '2026Q1', '2026Q2', '2026Q4'):
        market.record_financial_fact('GAPCO', q, 'IS', 'Revenue', 100.0, '2026-11-01')
    res = market.compute_ttm_and_ratios('GAPCO', as_of='2026-11-15')
    assert res['status'] == 'non_contiguous_quarters_for_ttm'

def test_C07_distinct_evidence_hashes_for_distinct_documents(isolated_env):
    e1 = market.record_evidence('EVCO', '10-K', 'sec_edgar', 'FY25 10-K', 'Content A', filed_at='2026-02-01')
    e2 = market.record_evidence('EVCO', '10-Q', 'sec_edgar', 'Q1 10-Q', 'Content B', filed_at='2026-05-01')
    assert e1['content_hash'] != e2['content_hash'] and e1['evidence_id'] != e2['evidence_id']

def test_C08_dcf_golden_model_and_invalid_input_rejection(isolated_env):
    d = decision.dcf('100', '0.10', '0.02', '200', '10')
    assert Decimal(d['enterprise_value']) == Decimal('1250') and Decimal(d['per_share']) == Decimal('105')
    for bad in ({'shares': '-5'}, {'wacc': '0.02', 'terminal_growth': '0.03'}, {'tax_rate': 'nan'}):
        with pytest.raises(ValueError):
            quant.driver_based_valuation('1000', '0.10', '0.20', **bad)

def test_C09_sector_valuation_methods_rim_and_sotp(isolated_env):
    assert quant.select_valuation_method('financials')['primary'] == 'residual_income'
    ri_neg = quant.residual_income_valuation('50', '0.05', '0.10')
    assert ri_neg['fair_value_per_share'] < 50.0
    sotp = quant.sotp_valuation([{'name': 'A', 'ev': 600}, {'name': 'B', 'ev': 400}], net_debt='200', shares='10')
    assert sotp['per_share'] == 80.0

def test_C10_e2e_sec_pipeline_to_auto_discovery(isolated_env):
    with patch.object(market, '_cik', return_value='0000123456'), patch.object(market, '_request', return_value=_sample_companyfacts()):
        market.sync_filings('AUTOCO')
    market._cache_bars('AUTOCO', [{'ts': '2026-11-15', 'open': '50', 'close': '50', 'currency': 'USD'}], 'yfinance', 'split_and_dividend_adj')
    disc = decision.discover_opportunities(['AUTOCO'])
    assert disc['status'] == 'ok' and disc['candidates'][0]['symbol'] == 'AUTOCO' and disc['candidates'][0]['valuation_discount_pct'] >= 0.15

def test_C11_expectations_partial_actuals_and_forecast_pit(isolated_env):
    quant.record_earnings_expectation('EXPC', '2026Q4', exp_revenue=100, exp_eps=2.0)
    quant.record_earnings_expectation('EXPC', '2026Q4', act_revenue=108)
    gap = quant.record_earnings_expectation('EXPC', '2026Q4', act_eps=2.25)
    assert gap['revenue_surprise_pct'] == 0.08 and gap['eps_surprise_pct'] == 0.125
    with pytest.raises(ValueError):
        quant.record_earnings_expectation('EXPC', '2026Q4', exp_revenue=120)

    p1 = knowledge.record_forecast_prediction('EXPC', 'Beat', '2026-12-31', 0.8)
    p2 = knowledge.record_forecast_prediction('EXPC', 'Miss', '2026-12-31', 0.2)
    with pytest.raises(ValueError):
        knowledge.resolve_forecast_prediction(p1['id'], 1, actual_event_at='2020-01-01')
    knowledge.resolve_forecast_prediction(p1['id'], 1, actual_event_at='2027-01-01T00:00:00+00:00')
    b = knowledge.brier_calibration_report()
    assert b['resolved_count'] == 1 and b['unresolved_count'] == 1 and abs(b['brier_score'] - 0.04) < 1e-6

def test_C12_research_sec_forensics_missing_vs_cached(isolated_env):
    market.save_filing_section('RSH1', '10-K', '2025-02-01', '001-25', 'risk_factors', 'General macroeconomic headwinds could adversely affect widget demand across regions.')
    market.save_filing_section('RSH1', '10-K', '2026-02-01', '001-26', 'risk_factors', 'General macroeconomic headwinds could adversely affect widget demand across regions.\nWe received an SEC subpoena and regulatory investigation notice regarding revenue recognition material weakness.')
    audit = decision.filing_red_flag_audit('RSH1', include_private=False)
    assert any(f['code'] == 'new_risk_factor_disclosures' for f in audit['red_flags'])
    missing = decision.filing_red_flag_audit('MISSING_CO', include_private=False)
    assert missing['status'] in {'no_filings_cached', 'insufficient_data'} or not missing.get('red_flags')
