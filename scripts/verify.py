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
