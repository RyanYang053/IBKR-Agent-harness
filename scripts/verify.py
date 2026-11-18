"""Essential checks. Uses synthetic files and a temporary data directory."""
import asyncio, json, os, sys, tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIXTURES = ROOT / 'tests' / 'fixtures'

def check(name, condition, detail=''):
    if not condition:
        raise SystemExit(f'FAIL {name} {detail}')
    print('PASS', name)

def finance_checks(home):
    os.environ['PERSONAL_AGENT_HOME'] = str(home)
    cfg = home / 'config.json'
    cfg.write_text(json.dumps({
        'knowledge_roots': [],
        'finance': {'accounts': {'U0000001': 'margin', 'U0000002': 'tfsa'}, 'base_currency': 'CAD', 'flex_queries': [], 'max_snapshot_age_days': 4000},
        'policy': {'max_file_bytes': 10000000, 'max_tool_rows': 50},
    }), encoding='utf-8')
    os.environ['PERSONAL_AGENT_CONFIG'] = str(cfg)
    from personal_agent import finance
    first = finance.import_xml(FIXTURES / 'two_accounts.xml')
    again = finance.import_xml(FIXTURES / 'two_accounts.xml')
    snap = finance.portfolio()
    perf = finance.performance()
    check('two-account sum', snap['positions_complete'] and Decimal(snap['nav']) == Decimal('2000.00') and snap['status'] in {'ok', 'partial_or_stale'})
    check('reimport', again['status'] == 'already_imported')
    check('transfer excluded', Decimal(perf['contributions']) == 0 and Decimal(perf['twr']) == 0, perf)
    from personal_agent import decision
    valued = decision.dcf('100', '0.10', '0.02', '0', '10')
    check('dcf', Decimal(valued['enterprise_value']) == Decimal('1250') and Decimal(valued['per_share']) == Decimal('125') and valued['price'] == 'unavailable')
    ms = decision.multi_stage_dcf('100', '0.10', '0.20', '0.025', '50', '10', years=5, fade=True)
    rev = decision.reverse_dcf('100', '0.10', '0.025', '50', '10', reference_price=ms['per_share'], years=5, fade=True)
    check('multi-stage & reverse dcf', len(ms['schedule']) == 5 and len(ms['sensitivity_3x3']) == 9 and abs(Decimal(rev['implied_stage1_fcff_growth']) - Decimal('0.20')) < Decimal('0.0005'), rev)
    gate = decision.evaluate_trade_gate('SYNTH', 'BUY', '400', 'taxable')
    check('pre-trade gatekeeper', gate['verdict'] in {'caution_review_flags', 'blocked_stale_ledger'} and gate['orders'] == 'disabled' and any(f['gate'] == 'concentration' for f in gate['gate_flags']))
    shock = decision.price_shock('-0.1')
    check('scenario', shock.get('method') == 'uniform_position_value_shock' and {Decimal(row['shocked_value_base']) for row in shock['rows']} == {Decimal('990'), Decimal('810')})
    tax = decision.tax_positions()
    check('tax positions weighted', tax.get('count', 0) >= 1 and sum(Decimal(r['weight_of_nav']) for r in tax['positions']) == Decimal('1'))
    habits = decision.trading_habits()
    from personal_agent import ops, report
    brief = report.daily_brief()
    wf_cf = ops.run_workflow('counterfactual_decision_attribution', {'days': 90})
    check('counterfactual attribution workflow', wf_cf['status'] == 'completed' and 'wealth_comparison' in wf_cf['result'])
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / 'dashboard.py'), default_timeout=30)
    at.run()
    check('dashboard 9-tab headless render', len(at.exception) == 0 and len(at.tabs) == 9, at.exception)
    check('trading habits telemetry', 'sizing_and_concentration' in habits and 'execution_and_turnover' in habits)
    check('compact daily brief', brief['status'] in {'ok', 'partial_or_stale'} and len(json.dumps(brief)) < 5000)
    rate = finance.xirr([(date(2023, 1, 1), Decimal('-1000')), (date(2024, 1, 1), Decimal('1100'))])
    check('xirr', rate is not None and abs(rate - Decimal('0.1')) < Decimal('0.0001'), rate)
    try:
        finance.import_xml(FIXTURES / 'wrong_currency.xml')
        check('mixed currency', False)
    except ValueError as exc:
        check('mixed currency', 'currency' in str(exc), exc)
    finance.import_xml(FIXTURES / 'one_account_later.xml')
    mismatched = finance.portfolio()
    check('date mismatch', mismatched['status'] == 'date_mismatch', mismatched)
    withheld = finance.performance()
    check('mismatch withholds return', withheld.get('twr') is None, withheld)

def market_checks(home):
    from datetime import datetime, timedelta, timezone
    cfg = json.loads((home / 'config.json').read_text(encoding='utf-8'))
    cfg['market'] = {'watchlist': ['QQQ'], 'alpaca_key_env': 'ALPACA_API_KEY', 'alpaca_secret_env': 'ALPACA_API_SECRET', 'finnhub_key_env': 'FINNHUB_API_KEY', 'fred_key_env': 'FRED_API_KEY', 'edgar_contact': ''}
    (home / 'config.json').write_text(json.dumps(cfg), encoding='utf-8')
    from importlib import reload
    from personal_agent import market
    reload(market)
    regular = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    closed = datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc)
    check('quote universe', market.universe() == ['QQQ'])
    check('yfinance not live', market.quote_freshness('yfinance', 'iex', regular, regular) == 'historical')
    check('fresh iex live', market.quote_freshness('alpaca', 'iex', regular, regular) == 'live')
    check('stale iex not live', market.quote_freshness('alpaca', 'iex', regular - timedelta(minutes=10), regular) == 'delayed')
    check('closed session not live', market.quote_freshness('alpaca', 'iex', closed, closed) == 'delayed')
    if not market.alpaca_configured():
        missing = market.latest_quote('QQQ')
        check('no delayed substitute', missing['freshness'] != 'live' and missing.get('provider') != 'yfinance')
    market._save_quote('QQQ', '1', '2026-11-16T14:00:00+00:00', 'iex', 'alpaca')
    shown = market._present('QQQ')
    check('cached stale stays delayed', shown['freshness'] == 'delayed' and shown['price'] == '1')
    market._cache_bars('QQQ', [{'ts': '2026-11-13', 'close': '1'}], 'yfinance')
    cached = market.bars('QQQ', 5)
    check('cached bars historical', cached[-1]['freshness'] == 'historical' and cached[-1]['provider'] == 'yfinance')
    from personal_agent import decision
    brief = decision.stock_brief('QQQ', '100', '0.10', '0.02', '0', '10')
    check('stock brief withholds stale upside', brief['symbol'] == 'QQQ' and brief['dcf']['upside'] == 'withheld' and brief['historical_bars_60d']['rows'] == 1)
    try:
        market._assert_public_http_url('http://127.0.0.1:8080/v1')
        check('ssrf loopback blocked', False)
    except ValueError:
        check('ssrf loopback blocked', True)

def knowledge_checks(home):
    os.environ['PERSONAL_AGENT_HOME'] = str(home)
    corpus = home / 'corpus'
    corpus.mkdir()
    note = corpus / 'note.md'
    note.write_text('The local marker is blue paper.\n投资笔记', encoding='utf-8')
    (corpus / 'flex-statement.txt').write_text('SHOULDNOTFIND secret statement', encoding='utf-8')
    cfg = json.loads((home / 'config.json').read_text(encoding='utf-8'))
    cfg['knowledge_roots'] = [str(corpus)]
    (home / 'config.json').write_text(json.dumps(cfg), encoding='utf-8')
    from importlib import reload
    from personal_agent import core, knowledge
    reload(core); reload(knowledge)
    indexed = knowledge.index()
    found = knowledge.search('blue paper')
    chinese = knowledge.search('投资')
    check('knowledge search', indexed['updated'] == 1 and found and found[0]['method'] == 'sqlite_fts5_trigram' and found[0]['untrusted'] is True and 'blue paper' in found[0]['content'])
    check('chinese substring', chinese and chinese[0]['method'] == 'substring' and '投资笔记' in chinese[0]['content'])
    check('secret file excluded', knowledge.search('SHOULDNOTFIND') == [])
    note.unlink()
    knowledge.index()
    check('deleted source', knowledge.search('blue paper') == [])
    memory_id = knowledge.save('fact', 'Owner prefers CAD reporting.', 'operator')
    replacement = knowledge.save('fact', 'Owner requires CAD reporting.', 'operator', supersedes=memory_id)
    recalled = knowledge.recall('CAD')
    check('memory supersede', replacement and len(recalled) == 1 and 'requires' in recalled[0]['content'], recalled)
    journal = knowledge.save_journal('CASH', 'Synthetic holding only.', 'operator', risk='Exit if synthetic test ends')
    listed = knowledge.list_memory('decision')
    check('decision journal', journal and listed and 'Synthetic holding only.' in listed[0]['content'])
    mistake_id = knowledge.save_lesson('mistake', 'Chased gap-up entry', 'Entered without margin of safety.', 'operator', symbol='QQQ', action_rule='Require DCF upside check before buying')
    goal_id = knowledge.save_lesson('goal', 'Write falsifier first', 'Every position needs an exit trigger.', 'operator', action_rule='Log falsifier before sizing >3%')
    guards = knowledge.memory_guardrails('QQQ')
    check('recursive guardrails', len(guards) >= 2 and any(g['id'] == mistake_id for g in guards))
    knowledge.resolve_memory(goal_id, 'Rule adopted across all new entries')
    from personal_agent import decision
    loop = decision.improvement_loop('QQQ')
    check('improvement loop', any(m['id'] == mistake_id for m in loop['past_mistakes']) and not any(g['id'] == goal_id for g in loop['active_goals']))
    from personal_agent import ops
    reload(ops)
    staged = ops.run_workflow('equity_valuation_and_expectations', {'symbol': 'QQQ', 'fcff': '100', 'shares': '10', 'propose_thesis': 'Index core holding', 'falsifier': 'Break of secular cash-flow trend', 'confidence': '0.75'})
    check('workflow halts awaiting approval', staged['status'] == 'awaiting_approval' and staged['pending_approval'] is True)
    resumed = ops.approve_workflow(staged['run_id'], 'Approved in synthetic test')
    check('workflow resume after approval', resumed['status'] == 'completed' and resumed['committed_memory_id'] is not None)
    from personal_agent import market
    market.save_filing_section('QQQ', '10-K', '2025-02-01', 'acc-1', 'risk_factors', 'Item 1A. Risk Factors\nSupply chain constraints could result in delayed shipments across regions.')
    market.save_filing_section('QQQ', '10-K', '2026-02-01', 'acc-2', 'risk_factors', 'Item 1A. Risk Factors\nSupply chain constraints have experienced severe disruption and resulted in order cancellations.\nNew antitrust investigation and subpoena disclosed this year.')
    wf_sec = ops.run_workflow('sec_filing_delta_red_flag', {'symbol': 'QQQ', 'stage_alert': True})
    check('sec filing delta workflow', wf_sec['status'] == 'awaiting_approval' and len(wf_sec['result']['red_flags']) >= 2)
    from personal_agent import quant
    th = quant.upsert_living_thesis('QQQ', 'Core index compounding;Secular tech margins', 'FCF margin;Cloud growth', 'Quarterly rebalance', 'margin collapse below 15%')
    triage = quant.triage_catalyst_event('QQQ', 'Company reports margin collapse below 15% and guidance cut')
    check('living thesis and L4 triage', th['version'] == 1 and triage['materiality_level'] == 'L4_decision_alert')
    exp_gap = quant.record_earnings_expectation('QQQ', '2026Q4', 'management_guidance', exp_revenue=100, exp_eps=2.0, act_revenue=108, act_eps=2.25)
    check('earnings expectation gap', exp_gap['verdict'] == 'positive_beat_and_raise' and exp_gap['revenue_surprise_pct'] == 0.08)
    ic_wf = ops.run_workflow('investment_committee_memo', {'symbol': 'QQQ', 'amount_base': '250', 'revenue_t0': '1000', 'shares': '100'})
    check('investment committee 8-step workflow', ic_wf['status'] == 'awaiting_approval' and len(ic_wf['completed_steps']) == 7)

    # v0.3.1 Hardening checks
    # 1. Approval cannot bypass blocked gate & idempotent re-approve
    blocked_wf = ops.run_workflow('tax_aware_pre_trade_approval', {'symbol': 'SYNTH', 'action': 'SELL', 'amount_base': '999999', 'account': 'U0000001'})
    bypass_blocked = False
    try:
        ops.approve_workflow(blocked_wf['run_id'], 'Try bypass')
    except ValueError:
        bypass_blocked = True
    check('approval cannot bypass blocked gate', bypass_blocked and blocked_wf['proposed_approval']['gate_verdict'] not in {'clear', 'caution_review_flags'})
    idem = ops.approve_workflow(staged['run_id'], 'Second approve call')
    check('approval idempotent', idem.get('idempotent') is True and idem['committed_memory_id'] == resumed['committed_memory_id'])

    # 2. Unified evaluate_policy checks oversell & multi-asset allocator in clean two_accounts ledger
    with tempfile.TemporaryDirectory() as clean_tmp:
        from personal_agent import core, finance, decision
        from unittest.mock import patch
        prev_h = os.environ.get('PERSONAL_AGENT_HOME')
        os.environ['PERSONAL_AGENT_HOME'] = clean_tmp
        reload(core); reload(finance); reload(decision); reload(quant)
        finance.import_xml(ROOT / 'tests' / 'fixtures' / 'two_accounts.xml')
        pol_sell = quant.evaluate_policy(proposed_trade={'symbol': 'SYNTH', 'action': 'SELL', 'amount_base': '999999', 'account': 'margin'})
        check('unified evaluate_policy oversell block', any(b['rule'] == 'insufficient_account_position' for b in pol_sell.get('hard_blocks', [])))
        alloc_stale = quant.allocate_incremental_capital('500', ['QQQ', 'SYNTH'])
        check('allocator blocks stale ledger', alloc_stale.get('status') == 'blocked_stale_ledger')
        fresh_snap = {**finance.portfolio(), 'status': 'ok', 'stale': False, 'advice_allowed': True, 'as_of': date.today().isoformat()}
        with patch.object(quant, 'portfolio', return_value=fresh_snap):
            alloc_res = quant.allocate_incremental_capital('500', ['QQQ', 'SYNTH'])
        synth_cand = next(c for c in alloc_res['candidates'] if c['candidate'] == 'SYNTH')
        check('multi-asset allocator capm prior', 'optimal_multi_asset_allocation' in alloc_res and synth_cand['expected_return_source'] == 'capm_erp_prior' and synth_cand['assumed_expected_return'] != 0.15)
        os.environ['PERSONAL_AGENT_HOME'] = prev_h
        reload(core); reload(finance); reload(decision); reload(quant)

    # 3. Driver DCF with D&A, dilution, terminal ROIC
    drv_adv = quant.driver_based_valuation('1000', '0.15', '0.25', shares='100', da_pct_rev='0.04', annual_dilution_rate='0.02', terminal_roic='0.20', op_margin_t0='0.20')
    check('driver dcf da dilution roic', drv_adv['scenarios']['base']['diluted_shares'] > 100 and drv_adv['scenarios']['base']['terminal_reinvestment_rate'] == 0.125)

    # 4. Living thesis immutable history
    th2 = quant.upsert_living_thesis('QQQ', 'Updated pillars', 'FCF margin', 'New catalyst', 'margin collapse below 15%', change_reason='Quarterly review', evidence_ids=['sec_10k_2026'])
    th_hist = quant.get_living_thesis('QQQ', include_history=True)
    check('living thesis immutable history', th2['version'] == 2 and th2['previous_version'] == 1 and len(th_hist) == 2 and th_hist[1]['version'] == 1)

    # 5. Earnings expectations append-only lock
    exp_locked = False
    try:
        quant.record_earnings_expectation('QQQ', '2026Q4', 'management_guidance', exp_revenue=150)
    except ValueError:
        exp_locked = True
    check('earnings expectation pre_earnings locked', exp_locked)

    # 6. Multi-asset capital allocator checked in clean ledger above

    # 7. Options exact N(d2) and no-arbitrage bounds
    opt_itm = quant.analyze_option_strategy('covered_call', '100', '90', 60, volatility='0.30')
    opt_arb = quant.analyze_option_strategy('covered_call', '100', '90', 60, premium='2.0')
    check('options exact nd2 and arb bounds', opt_itm['greeks']['nd2_itm_prob'] != opt_itm['greeks']['delta'] and opt_arb['iv_solver_status'] == 'outside_no_arbitrage_bounds' and opt_arb['solved_iv_from_premium'] is None)

    # 8. Durable workflow resume, immutable params, option attribution unit consistency, & WAL-safe SQLite backup
    from personal_agent import report
    wf_partial_id = 'wf_resume_test'
    ops._save_run(wf_partial_id, 'morning_portfolio_review', 'running', 'freshness_and_reconciliation_gate', ['intake_validate', 'freshness_and_reconciliation_gate'], False, {'workflow': 'morning_portfolio_review', 'immutable_params': {}, 'input_hash': ops._param_hash({})}, {'brief': report.daily_brief()}, None, False)
    resume_mismatch = False
    try:
        ops.run_workflow('morning_portfolio_review', {'extra': 'tampered'}, resume_run_id=wf_partial_id)
    except ValueError:
        resume_mismatch = True
    wf_resumed = ops.resume_workflow(wf_partial_id)
    wf_state = ops.get_workflow_run(wf_partial_id)
    bk_res = core.backup_databases(home / 'test_backup')
    check('durable workflow resume and wal backup', resume_mismatch and wf_resumed['status'] == 'completed' and wf_state['retry_count'] == 1 and bk_res['status'] == 'ok' and len(bk_res['databases']) >= 3)

    # 9. Canonical PortfolioSnapshot, FX fail-closed, option FX delta-notional, crash-recoverable approval_intents, & DB migration
    with tempfile.TemporaryDirectory() as v_tmp:
        from personal_agent import core, finance, market, decision, knowledge
        prev_h2 = os.environ.get('PERSONAL_AGENT_HOME')
        os.environ['PERSONAL_AGENT_HOME'] = v_tmp
        reload(core); reload(finance); reload(market); reload(decision); reload(knowledge); reload(quant); reload(ops)
        # Missing FX fails closed
        with core.db('finance') as c:
            finance.schema(c)
            for acct in ('margin', 'tfsa'):
                for d, v in [('2026-11-15', '500'), ('2026-11-17', '570')]:
                    c.execute('INSERT INTO nav VALUES(?,?,?,?,?,?)', (acct, d, 'CAD', v, v, 'syn'))
            c.execute('INSERT INTO records VALUES(?,?,?,?,?)', ('margin', 'cash', 'fx1', json.dumps({'type': 'deposit', 'amount': '100', 'currency': 'USD', 'reportDate': '20261116'}), 'syn'))
        with patch.object(finance, 'portfolio', return_value={'status': 'ok', 'nav': '1140', 'as_of': '2026-11-17'}):
            fx_perf = finance.performance()
        check('foreign cash flow missing fx fails closed', fx_perf['status'] == 'missing_fx_rate' and fx_perf['twr'] is None)

        # Canonical PortfolioSnapshot gross exposure & FX option delta-notional & unknown account / missing buying power
        snap_multi = {
            'status': 'ok', 'advice_allowed': True, 'as_of': date.today().isoformat(), 'nav': '10000', 'currency': 'CAD',
            'accounts': [{'account': 'taxable', 'nav': '5000', 'cash': '1000'}, {'account': 'tfsa', 'nav': '5000', 'cash': None}],
            'positions': [
                {'account': 'taxable', 'conid': '1', 'symbol': 'SYNTH', 'asset': 'STK', 'currency': 'CAD', 'quantity': '100', 'value_base': '8000'},
                {'account': 'tfsa', 'conid': '1', 'symbol': 'SYNTH', 'asset': 'STK', 'currency': 'CAD', 'quantity': '-100', 'value_base': '-8000'},
                {'account': 'taxable', 'conid': '2', 'symbol': 'OPTUSD', 'asset': 'OPT', 'currency': 'USD', 'quantity': '1', 'multiplier': '100', 'value_base': '700', 'underlying_price': '100', 'underlying_currency': 'USD', 'fx_rate_to_base': '1.4', 'delta': '0.5'},
            ]
        }
        pol_cfg = {'core_symbols': ['QQQ'], 'min_cash_reserve': 0, 'core_min_weight': 0, 'tactical_max_weight': 5, 'max_single_stock_weight': 5, 'max_gross_economic_leverage': 10, 'max_economic_leverage': 10, 'max_drawdown_budget': -0.25}
        p_res = quant.evaluate_policy(snap_multi, investment_policy=pol_cfg)
        p_unk = quant.evaluate_policy(snap_multi, {'symbol': 'QQQ', 'action': 'BUY', 'amount_base': '100', 'account': 'ghost_acct'}, investment_policy=pol_cfg)
        p_nobp = quant.evaluate_policy(snap_multi, {'symbol': 'QQQ', 'action': 'BUY', 'amount_base': '100', 'account': 'tfsa'}, investment_policy=pol_cfg)
        check('canonical snapshot gross and option fx delta', abs(p_res['exposure_summary']['gross_economic_exposure'] - 2.3) < 1e-6 and abs(p_res['exposure_summary']['options_delta_equiv_weight'] - 0.7) < 1e-6)
        check('unknown account and missing bp fail closed', any(b['rule'] == 'unknown_account' for b in p_unk['hard_blocks']) and any(b['rule'] == 'missing_account_buying_power' for b in p_nobp['hard_blocks']))

        # Crash recovery of approval_intents (approving with memory_id set) + reject race
        with core.db('ops') as c:
            ops.schema(c)
        wf_cr = ops.run_workflow('equity_valuation_and_expectations', {'symbol': 'QQQ', 'propose_thesis': 'Crash test', 'falsifier': 'Break'})
        with core.db('ops') as c:
            c.execute("UPDATE workflow_runs SET status='approving' WHERE run_id=?", (wf_cr['run_id'],))
            c.execute("INSERT INTO approval_intents(run_id,decision,operator_note,memory_id,created_at,updated_at) VALUES(?,?,?,?,?,?)", (wf_cr['run_id'], 'approving', 'crash', 777, core.now(), core.now()))
        rec_res = ops.approve_workflow(wf_cr['run_id'])
        rej_after = False
        try:
            ops.reject_workflow(wf_cr['run_id'], 'late reject')
        except ValueError:
            rej_after = True
        check('approval_intents crash recovery and reject race', rec_res.get('recovered') is True and rec_res['committed_memory_id'] == 777 and rej_after)

        # v0.3.3 F01-F10 invariant tests: 55-position untruncated snapshot, transaction conservation, plan mode hard gate, concurrent approval, restore rehearsal
        with core.db('finance') as c:
            finance.schema(c)
            c.execute('DELETE FROM nav'); c.execute('DELETE FROM positions'); c.execute('DELETE FROM snapshots')
            today_s = date.today().isoformat()
            c.execute('INSERT INTO nav VALUES(?,?,?,?,?,?)', ('margin', today_s, 'CAD', '1000', '0', 's55'))
            c.execute('INSERT INTO nav VALUES(?,?,?,?,?,?)', ('tfsa', today_s, 'CAD', '1000', '0', 's55'))
            c.execute('INSERT INTO snapshots VALUES(?,?,?,1)', ('margin', today_s, 's55'))
            c.execute('INSERT INTO snapshots VALUES(?,?,?,1)', ('tfsa', today_s, 's55'))
            for idx_p in range(55):
                c.execute('INSERT INTO positions VALUES(?,?,?,?,?,?,?,?,?,?,?)', ('margin', today_s, f'C{idx_p:03d}', f'SYM{idx_p}', 'STK', 'CAD', '1', '10', f'C{idx_p:03d}', '10', '1'))
        full55 = finance.load_full_portfolio_snapshot()
        paged50 = finance.portfolio()
        check('55-position untruncated snapshot vs bounded portfolio', len(full55.positions) == 55 and len(paged50['positions']) == 50 and paged50['has_more'] is True)

        sim_res = quant.simulate_allocation(full55, cash_contribution=500, proposed_trades=[{'symbol': 'SYM0', 'action': 'BUY', 'amount_base': '500', 'account': 'margin'}], contribution_account='margin')
        tfsa_after = next(a for a in sim_res.accounts if a.account == 'tfsa')
        check('transaction simulation cash and nav conservation', sim_res.nav == Decimal('2500') and sim_res.cash == Decimal('0') and tfsa_after.nav == Decimal('1000'))

        # Plan mode hard blocks 160% gross even on pre-simulated snapshot
        gross160 = quant.evaluate_policy(snap_multi, investment_policy={**pol_cfg, 'max_gross_economic_leverage': 1.35}, mode='PROPOSED_PLAN')
        check('plan mode hard blocks gross 160', gross160['status'] == 'blocked_policy_violation' and any(b['rule'] == 'max_gross_economic_leverage' for b in gross160['hard_blocks']))

        # Concurrent 2-thread approve_workflow -> single journal record, loser blocked or idempotent
        import concurrent.futures
        wf_conc = ops.run_workflow('equity_valuation_and_expectations', {'symbol': 'QQQ', 'propose_thesis': 'Concurrent test', 'falsifier': 'Break'})
        def _try_app(n):
            try: return ('ok', ops.approve_workflow(wf_conc['run_id'], n))
            except ValueError as e: return ('err', str(e))
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            res_conc = list(pool.map(_try_app, ['t1', 't2']))
        ok_cnt = sum(1 for k, _ in res_conc if k == 'ok')
        with core.db('knowledge') as kc:
            cnt_j = kc.execute('SELECT count(*) FROM memory WHERE approval_run_id=?', (wf_conc['run_id'],)).fetchone()[0]
        check('concurrent approve single journal commit', ok_cnt >= 1 and cnt_j == 1)

        # Backup & restore rehearsal
        bk_v33 = core.backup_databases(Path(v_tmp) / 'bk_v33')
        rst_v33 = core.restore_databases(bk_v33['backup_dir'], Path(v_tmp) / 'rst_v33')
        check('backup and restore rehearsal', rst_v33['status'] == 'ok' and len(rst_v33['databases']) >= 2)

        # v0.4.0 BrokerReadOnlyAdapter offline replay & live-to-flex reconciliation
        from personal_agent import broker
        brk_fixture = {
            'as_of': date.today().isoformat() + 'T14:30:00+00:00',
            'entitlements': {'equities_live': True, 'options_greeks_live': False},
            'accounts': {
                'U0000001': {'NetLiquidation': '1050', 'TotalCashValue': '50', 'AvailableFunds': '50', 'BuyingPower': '100', 'ExcessLiquidity': '50', 'currency': 'CAD'},
                'U0000002': {'NetLiquidation': '1000', 'TotalCashValue': '1000', 'AvailableFunds': '1000', 'BuyingPower': '1000', 'ExcessLiquidity': '1000', 'currency': 'CAD'},
            },
            'positions': [
                {'accountId': 'U0000001', 'conid': 'C000', 'symbol': 'SYM0', 'asset': 'STK', 'position': '2'},
                {'accountId': 'U0000001', 'conid': 'OPT1', 'symbol': 'SYM0 CALL', 'asset': 'OPT', 'position': '1'},
            ],
            'greeks': {'OPT1': {'delta': 0.55}},
        }
        sync_b = broker.sync_broker_replay(brk_fixture)
        latest_b = broker.latest_broker_snapshot()
        rec_b = broker.reconcile_live_to_flex()
        check('broker readonly replay and flex reconciliation', sync_b['status'] == 'ok' and latest_b['greeks']['OPT1']['status'] == 'entitlement_required' and rec_b['status'] == 'timing_or_position_differences' and len(rec_b['position_differences']) >= 2 and not any(hasattr(broker.ReplayBrokerAdapter, m) for m in broker.FORBIDDEN_BROKER_METHODS))

        # v0.4.1 R01/R03/R04: ETF look-through, option chain & roll, Canadian ACB
        lt = quant.etf_lookthrough_exposure({'SYM0': {'AAPL': 0.5, 'MSFT': 0.5}}, portfolio_snapshot=full55)
        ch = quant.analyze_option_chain('QQQ', [
            {'right': 'call', 'strike': 110, 'days_to_expiry': 45, 'bid': 1.6, 'ask': 1.8, 'open_interest': 50},
            {'right': 'call', 'strike': 110, 'days_to_expiry': 45, 'bid': 2.0, 'ask': 1.5, 'open_interest': 50},
        ], spot=100)
        roll = quant.analyze_option_roll(100, 105, 10, 1.2, 115, 45, 2.0, contracts=1)
        with core.db('finance') as c:
            c.execute('INSERT INTO records VALUES(?,?,?,?,?)', ('margin', 'trade', 'acb1', json.dumps({'symbol': 'TESTACB', 'assetCategory': 'STK', 'buySell': 'BUY', 'quantity': '10', 'tradePrice': '100', 'currency': 'CAD', 'ibCommission': '5'}), 'syn'))
            c.execute('INSERT INTO records VALUES(?,?,?,?,?)', ('margin', 'trade', 'acb2', json.dumps({'symbol': 'TESTACB', 'assetCategory': 'STK', 'buySell': 'SELL', 'quantity': '4', 'tradePrice': '120', 'currency': 'CAD', 'ibCommission': '2'}), 'syn'))
        acb = decision.compute_canadian_acb('TESTACB', 'margin')
        check('v0.4.1 etf lookthrough, option chain/roll, and acb', len(ch['eligible_contracts']) == 1 and len(ch['rejected_contracts']) == 1 and roll['net_roll_cashflow'] == 80.0 and Decimal(acb['realized_dispositions'][0]['realized_gain_base']) == Decimal('76.00'))

        # v0.5.0 E01/E02 & v0.5.1 V02: PIT evidence & financial statements restatement + sector valuation
        ev_doc = market.record_evidence('PITCO', '10-Q', 'sec_edgar', 'Q1 10-Q', 'Revenue 100', filed_at='2026-02-01', available_at='2026-02-01')
        for q_i, q_per in enumerate(('2025Q2', '2025Q3', '2025Q4', '2026Q1')):
            av = f'202{5 if q_i < 3 else 6}-0{(q_i*3)%9 + 1}-15'
            market.record_financial_fact('PITCO', q_per, 'IS', 'Revenue', 100 + q_i*10, filed_at=av, available_at=av, evidence_id=ev_doc['evidence_id'])
            market.record_financial_fact('PITCO', q_per, 'IS', 'OperatingIncome', 20 + q_i*2, filed_at=av, available_at=av)
            market.record_financial_fact('PITCO', q_per, 'CF', 'OperatingCashFlow', 25, filed_at=av, available_at=av)
            market.record_financial_fact('PITCO', q_per, 'CF', 'CapEx', 5, filed_at=av, available_at=av)
            market.record_financial_fact('PITCO', q_per, 'BS', 'TotalDebt', 20, filed_at=av, available_at=av)
            market.record_financial_fact('PITCO', q_per, 'BS', 'ShareholdersEquity', 100, filed_at=av, available_at=av)
            market.record_financial_fact('PITCO', q_per, 'BS', 'Cash', 10, filed_at=av, available_at=av)
        market.record_financial_fact('PITCO', '2026Q1', 'IS', 'Revenue', 95, filed_at='2026-05-01', available_at='2026-05-01')
        pit_before = market.get_facts_pit('PITCO', as_of='2026-03-01', period='2026Q1')
        pit_after = market.get_facts_pit('PITCO', as_of='2026-06-01', period='2026Q1')
        ttm_res = market.compute_ttm_and_ratios('PITCO', as_of='2026-06-01')
        ri = quant.residual_income_valuation('50', '0.15', '0.10')
        sotp = quant.sotp_valuation([{'name': 'Cloud', 'ev': 800}, {'name': 'Ads', 'ev': 400}], net_debt='200', shares='100')
        rev_b = next(f['value'] for f in pit_before['facts'] if f['concept'] == 'Revenue')
        rev_a = next(f['value'] for f in pit_after['facts'] if f['concept'] == 'Revenue')
        check('v0.5.0-v0.5.1 pit restatement, ttm, and sector valuation', rev_b == 130.0 and rev_a == 95.0 and ttm_res['status'] == 'ok' and quant.select_valuation_method('financials')['primary'] == 'residual_income' and ri['fair_value_per_share'] > 50 and sotp['per_share'] == 10.0)

        # v0.6.0 A01/A03, v0.7.0 Q01-Q04, v1.0 P02: discovery, alert dedup, cashflow sim, forecast calibration, shadow check
        disc_none = decision.discover_opportunities(['QQQ'], {})
        disc_hit = decision.discover_opportunities(['QQQ', 'FAIL'], {'QQQ': {'roic': 0.22, 'fcf_growth': 0.15, 'valuation_discount_pct': 0.25, 'dilution_rate': 0.01}, 'FAIL': {'roic': 0.02, 'fcf_growth': -0.1, 'valuation_discount_pct': 0.30, 'dilution_rate': 0.08}})
        al1 = ops.emit_alert('k1', 'QQQ', 'medium', 'Test alert', 'Detail')
        al2 = ops.emit_alert('k1', 'QQQ', 'medium', 'Test alert', 'Detail')
        p1 = knowledge.record_forecast_prediction('QQQ', 'Beat Q4 rev', '2026-12-31', 0.80)
        p2 = knowledge.record_forecast_prediction('SPY', 'Rate cut', '2026-12-31', 0.40)
        knowledge.resolve_forecast_prediction(p1['id'], 1)
        brier = knowledge.brier_calibration_report()
        sh_chk = ops.shadow_mode_check()
        check('v0.6-v1.0 discovery, alerts, calibration, and shadow check', disc_none['status'] == 'insufficient_data' and disc_hit['candidates'][0]['symbol'] == 'QQQ' and al2['deduplicated'] is True and al2['hit_count'] == 2 and brier['resolved_count'] == 1 and brier['unresolved_count'] == 1 and sh_chk['orders'] == 'disabled' and sh_chk['status'] == 'blocked')

        # Audit-83da731 counterexample regressions:
        # A. Adding 1 more option contract doubles delta-notional exposure from 50% to 100%
        opt_snap = {
            'status': 'ok', 'advice_allowed': True, 'as_of': date.today().isoformat(), 'nav': '10000', 'currency': 'CAD',
            'accounts': [{'account': 'margin', 'nav': '10000', 'cash': '5000', 'allow_margin': True}],
            'positions': [{'account': 'margin', 'conid': 'OPT_X', 'symbol': 'OPT_X', 'asset': 'OPT', 'currency': 'CAD', 'quantity': '1', 'multiplier': '100', 'value_base': '500', 'underlying_price': '100', 'underlying_currency': 'CAD', 'fx_rate_to_base': '1', 'delta': '0.5'}]
        }
        opt_add = quant.evaluate_policy(opt_snap, {'symbol': 'OPT_X', 'conid': 'OPT_X', 'asset': 'OPT', 'action': 'BUY', 'amount_base': '500', 'quantity': '1', 'account': 'margin'}, investment_policy=pol_cfg, mode='PROPOSED_PLAN')
        check('option buy doubles delta exposure to 100pct', abs(opt_add['exposure_summary']['options_delta_equiv_weight'] - 1.0) < 1e-6)

        # B. Non-margin TFSA negative cash rejected in simulate_allocation
        tfsa_zero = {'status': 'ok', 'advice_allowed': True, 'as_of': date.today().isoformat(), 'nav': '1000', 'currency': 'CAD', 'accounts': [{'account': 'tfsa', 'nav': '1000', 'cash': '0', 'allow_margin': False}], 'positions': []}
        tfsa_blocked = False
        try:
            quant.simulate_allocation(tfsa_zero, proposed_trades=[{'symbol': 'QQQ', 'action': 'BUY', 'amount_base': '500', 'account': 'tfsa'}])
        except ValueError:
            tfsa_blocked = True
        check('tfsa negative cash rejected by reducer', tfsa_blocked)

        # C. Recoverable approving intent with NULL memory_id
        wf_null = ops.run_workflow('equity_valuation_and_expectations', {'symbol': 'QQQ', 'propose_thesis': 'Null crash recovery', 'falsifier': 'Break'})
        with core.db('ops') as c:
            c.execute("UPDATE workflow_runs SET status='approving' WHERE run_id=?", (wf_null['run_id'],))
            c.execute("INSERT INTO approval_intents(run_id,decision,operator_note,memory_id,created_at,updated_at) VALUES(?,?,?,?,?,?)", (wf_null['run_id'], 'approving', 'null_crash', None, core.now(), core.now()))
        rec_null = ops.approve_workflow(wf_null['run_id'], 'recovered')
        check('approving NULL memory_id crash recovery', rec_null['status'] == 'completed' and rec_null['committed_memory_id'] is not None)

        # D. Concrete IBKRTWSAdapter fails closed when TWS unreachable + PIT auto discovery + ACB oversell warning
        tws_res = broker.sync_broker_live(port=59999)
        pit_disc = decision.discover_opportunities(['PITCO'])
        with core.db('finance') as c:
            c.execute('INSERT INTO records VALUES(?,?,?,?,?)', ('margin', 'trade', 'acb_over', json.dumps({'symbol': 'TESTACB', 'assetCategory': 'STK', 'buySell': 'SELL', 'quantity': '20', 'tradePrice': '130', 'currency': 'CAD'}), 'syn'))
        acb_over = decision.compute_canadian_acb('TESTACB', 'margin')
        # E. Realistic SEC XBRL CompanyFacts YTD->Quarter differencing, restatement vintages, & Stage B auto-DCF discovery
        xbrl_sample = {'facts': {'us-gaap': {
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
            'LongTermDebt': {'units': {'USD': [{'form': '10-K', 'fp': 'FY', 'fy': 2026, 'end': '2026-09-30', 'filed': '2026-11-10', 'val': 50.0}, {'form': '10-K', 'fp': 'FY', 'fy': 2025, 'end': '2025-09-30', 'filed': '2025-11-10', 'val': 50.0}]}},
            'CashAndCashEquivalentsAtCarryingValue': {'units': {'USD': [{'form': '10-K', 'fp': 'FY', 'fy': 2026, 'end': '2026-09-30', 'filed': '2026-11-10', 'val': 100.0}, {'form': '10-K', 'fp': 'FY', 'fy': 2025, 'end': '2025-09-30', 'filed': '2025-11-10', 'val': 100.0}]}},
            'WeightedAverageNumberOfDilutedSharesOutstanding': {'units': {'shares': [{'form': '10-K', 'fp': 'FY', 'fy': 2026, 'end': '2026-09-30', 'filed': '2026-11-10', 'val': 10.0}, {'form': '10-K', 'fp': 'FY', 'fy': 2025, 'end': '2025-09-30', 'filed': '2025-11-10', 'val': 10.0}]}},
        }}}
        market._normalize_sec_xbrl_facts('XBRLCO', '0000999999', xbrl_sample)
        ttm_x = market.compute_ttm_and_ratios('XBRLCO', as_of='2026-11-15')
        market._cache_bars('XBRLCO', [{'ts': '2026-11-15', 'open': '50', 'close': '50', 'currency': 'USD'}], 'yfinance', 'split_and_dividend_adj')
        with core.db('market') as mc:
            mc.execute("INSERT OR IGNORE INTO financial_statements_pit VALUES('XBRLCO','FY2026','BS','ShareholdersEquity',300.0,'USD','USD','US-GAAP','2026-11-10','2026-11-10',1,'ev_x')")
        disc_auto = decision.discover_opportunities(['XBRLCO'])
        disc_nodata = decision.discover_opportunities(['NODATA_TICKER'])
        check('sec ytd differencing and auto stage-b discovery', ttm_x['ttm_metrics']['operating_cash_flow'] == 400.0 and ttm_x['ttm_metrics']['capex'] == 40.0 and disc_auto['status'] == 'ok' and disc_auto['candidates'][0]['symbol'] == 'XBRLCO' and disc_nodata['status'] == 'insufficient_data')

        os.environ['PERSONAL_AGENT_HOME'] = prev_h2
        reload(core); reload(finance); reload(market); reload(decision); reload(knowledge); reload(quant); reload(ops)

    ev = ops.run_eval_benchmark()
    check('evaluation harness 100pct', ev['passed'] == ev['total'] and ev['score_pct'] == 100.0, ev)

async def mcp_checks():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    python = Path(sys.executable)
    for domain, tool, arguments in (
        ('knowledge', 'list_memory', {}),
        ('investment', 'value_dcf', {'fcff': '100', 'wacc': '0.1', 'growth': '0.02', 'net_debt': '0', 'shares': '10'}),
        ('cfo', 'get_portfolio', {}),
    ):
        params = StdioServerParameters(command=str(python), args=['-m', 'personal_agent.server', domain], cwd=str(ROOT), env=os.environ.copy())
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                ping = await session.call_tool('ping', {})
                called = await session.call_tool(tool, arguments)
                check(f'mcp {domain} ping', not ping.isError, ping)
                check(f'mcp {domain} tool', not called.isError, called)
    pub_params = StdioServerParameters(command=str(python), args=['-m', 'personal_agent.server', 'investment', '--public-only'], cwd=str(ROOT), env=os.environ.copy())
    async with stdio_client(pub_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = {t.name for t in listed.tools}
            private_names = {'get_portfolio', 'get_transactions', 'get_tax_positions', 'get_daily_brief', 'get_records'}
            check('mcp public-only excludes private ledger', 'solve_reverse_dcf' in names and 'search_public_web' in names and not (names & private_names), names)

def main():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        finance_checks(home)
        market_checks(home)
        knowledge_checks(home)
        asyncio.run(mcp_checks())
    print('ALL ESSENTIAL CHECKS PASSED')

if __name__ == '__main__':
    main()
