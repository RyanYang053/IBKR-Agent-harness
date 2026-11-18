import streamlit as st
from decimal import Decimal
from personal_agent.decision import attach_market_price, counterfactual_attribution, dcf, evaluate_trade_gate, exposure, factor_stress_test, filing_red_flag_audit, improvement_loop, multi_stage_dcf, portfolio_factor_risk, price_shock, reverse_dcf, risk, tax_positions, trading_habits
from personal_agent.finance import history, performance, portfolio, transactions
from personal_agent.knowledge import resolve_memory, save_journal, save_lesson
from personal_agent.market import quote_board, sync_quotes, system_health
from personal_agent.ops import approve_workflow, list_eval_runs, list_workflow_runs, list_workflows, reject_workflow, run_eval_benchmark, run_workflow, telemetry_summary
from personal_agent.report import daily_brief

st.set_page_config(page_title='Investment Agent', layout='wide')
st.title('Investment Agent')
try: snap=portfolio()
except Exception as exc:
    st.error(f'Portfolio unavailable: {type(exc).__name__}'); st.stop()
if 'nav' not in snap:
    st.warning('Portfolio unavailable: '+snap.get('status','unavailable')); st.json(snap); st.stop()
account=st.sidebar.selectbox('Account', ['All']+[row['account'] for row in snap['accounts']])
selected=None if account=='All' else account
with st.sidebar.expander('System & Provider Readiness', expanded=False):
    health=system_health()
    st.caption('Boolean status only; secret values are never displayed.')
    st.json({'services':health['services'],'data_providers':health['data_providers'],'cloud_llm_keys':health['external_cloud_llm_keys']})
overview, holdings, perf_tab, activity, risk_tab, habits_tab, research, ops_tab, reports=st.tabs(['Overview','Holdings','Performance','Transactions','Risk','Habits & Memory','Research','Workflows & Ops','Reports'])

with overview:
    st.caption(f"Statement date: {snap['as_of']} | {snap['currency']} | Not live | Advice allowed: {snap.get('advice_allowed')}")
    st.caption('NAV source: EquitySummaryByReportDateInBase. Benchmark prices are not configured.')
    if snap['status']!='ok': st.warning('Stale or incomplete snapshot. Do not treat the figures as current advice.')
    rows=[row for row in snap['accounts'] if selected in (None,row['account'])]
    st.metric('Combined NAV' if selected is None else 'Shown accounts', f"{sum((Decimal(row['nav']) for row in rows),Decimal(0)):,.2f} {snap['currency']}")
    brief=daily_brief()
    st.subheader('Daily Executive Brief & Coaching Focus')
    st.caption('Pre-computed session summary shared with the local LLM (get_daily_brief) to minimize prompt tokens.')
    if brief.get('recursive_coaching',{}).get('next_focus'):
        st.dataframe(brief['recursive_coaching']['next_focus'], hide_index=True)
    col_a, col_b=st.columns(2)
    with col_a:
        st.caption('Top weights & superficial-loss watch')
        if brief.get('top_holdings'): st.dataframe(brief['top_holdings'], hide_index=True)
    with col_b:
        st.caption('Tax-loss harvest watchlist & 14-day earnings radar')
        if brief.get('tax_watchlist',{}).get('top_loss_harvest_candidates'):
            st.dataframe(brief['tax_watchlist']['top_loss_harvest_candidates'], hide_index=True)
        if brief.get('market_radar',{}).get('upcoming_earnings_14d'):
            st.dataframe(brief['market_radar']['upcoming_earnings_14d'], hide_index=True)
    st.subheader('Market quotes')
    st.caption('Live means a fresh Alpaca IEX quote. Delayed and historical prices are shown separately and are not used as live.')
    if st.button('Refresh US quotes'): st.session_state['quotes']=sync_quotes()['quotes']
    st.dataframe(st.session_state.get('quotes') or quote_board(), hide_index=True)
    st.dataframe(rows, hide_index=True)
    series=history(None)
    if series:
        import pandas as pd
        frame=pd.DataFrame(series); frame['nav']=frame['nav'].astype(float)
        st.subheader('NAV history - not investment return')
        st.line_chart(frame.set_index('date')['nav'])

with holdings:
    positions=[row for row in snap['positions'] if selected in (None,row['account'])]
    st.caption(f"SUMMARY positions: {len(positions)} of {snap['position_count']}. Identity is conid, not symbol.")
    st.dataframe(positions, hide_index=True)
    book=exposure(selected)
    st.subheader('Exposure')
    st.caption('Gross and net are position market values. They are not delta, leverage, or ETF look-through.')
    st.json({key:book[key] for key in book if key!='by_underlying'})
    if book.get('by_underlying'): st.dataframe(book['by_underlying'], hide_index=True)
    tax=tax_positions(selected)
    st.subheader('Tax-weighted stock summary')
    st.caption('Weights of NAV, taxable vs TFSA location, statement-FX unrealized P/L, superficial-loss watch, and dividend withholding.')
    if tax.get('positions'): st.dataframe(tax['positions'], hide_index=True)

with perf_tab:
    result=performance()
    st.caption('NAV change is not return. Exact TWR is not shown. Benchmark: unavailable.')
    if result.get('twr') is None: st.warning('Return withheld: '+result.get('status','unavailable'))
    else:
        st.metric('Daily-approximation TWR', result['twr'])
        st.write('XIRR', result.get('xirr'), result.get('xirr_method'))
    for note in result.get('limitations') or []: st.caption(note)
    st.divider()
    st.subheader('Counterfactual Decision Attribution (Active Trading vs Zero-Trade Buy-and-Hold)')
    cf_days=st.selectbox('Attribution lookback window (days)', [30, 60, 90, 180, 365], index=2)
    attrib=counterfactual_attribution(int(cf_days), selected)
    if 'wealth_comparison' in attrib:
        st.write({'verdict':attrib.get('verdict'), **attrib['wealth_comparison']})
        st.json(attrib.get('attribution_breakdown') or {})
        ca1, ca2=st.columns(2)
        with ca1:
            st.caption('Top value-adding active decisions')
            if attrib.get('best_decisions'): st.dataframe(attrib['best_decisions'], hide_index=True)
        with ca2:
            st.caption('Costliest decisions (including premature winner exits)')
            if attrib.get('costliest_decisions'): st.dataframe(attrib['costliest_decisions'], hide_index=True)

with activity:
    category=st.selectbox('Category', ['trade','cash','corporate_action'])
    st.dataframe(transactions(category,selected), hide_index=True)

with risk_tab:
    measured=risk()
    st.caption('Drawdown uses cumulative daily-approximation return, not the raw NAV curve.')
    st.json({key:measured[key] for key in ('status','max_drawdown','drawdown_method','var_95','var_method','observations','sharpe_status','sharpe_source','beta','benchmark','qqq_bars') if key in measured})
    st.caption('FRED and Bank of Canada values are cached historical series. They are not live prices.')
    st.json({'macro':measured.get('sharpe_source'),'fx':measured.get('fx')})
    st.subheader('Cross-Asset Covariance, MCTR & Correlation Clusters')
    factor=portfolio_factor_risk('QQQ', 90, selected)
    if factor.get('status')=='ok':
        st.caption(f"90d sample covariance ({factor['overlapping_trading_days']} common trading days, covered equity weight {factor['covered_equity_weight_of_nav']}). Benchmark proxy is USD close price return, not CAD total return.")
        st.json(factor['portfolio_metrics'])
        st.dataframe(factor['risk_attribution'], hide_index=True)
        if factor.get('high_correlation_clusters'):
            st.caption('High-correlation pairs (rho >= 0.65)')
            st.dataframe(factor['high_correlation_clusters'], hide_index=True)
    else:
        st.info(factor.get('status','unavailable'))
    st.subheader('Beta-Weighted Equity + FX Stress Scenario')
    sc1, sc2=st.columns(2)
    with sc1: b_shock_in=st.text_input('QQQ benchmark shock (e.g. -0.15)', '-0.15')
    with sc2: fx_shock_in=st.text_input('USD/CAD FX shock (e.g. 0.02)', '0.02')
    try:
        f_stress=factor_stress_test(b_shock_in, fx_shock_in, 'QQQ', selected)
        st.write({'portfolio_pnl_impact_base':f_stress.get('portfolio_pnl_impact_base'),'portfolio_nav_impact_pct':f_stress.get('portfolio_nav_impact_pct')})
        if f_stress.get('positions'): st.dataframe(f_stress['positions'], hide_index=True)
    except Exception as exc:
        st.error(str(exc))
    shocked=price_shock('-0.10', selected)
    st.subheader('Uniform baseline scenario: minus 10 percent position value')
    st.caption('Uniform shock across all positions for baseline comparison.')
    if shocked.get('rows'): st.dataframe(shocked['rows'], hide_index=True)
    else: st.warning(shocked.get('status','unavailable'))

with habits_tab:
    habits=trading_habits(selected)
    loop=improvement_loop()
    st.subheader('Trading Habit Telemetry')
    st.caption('Deterministic behavioral audit computed directly from your IBKR execution history, tax lots, and decision journal.')
    c1, c2=st.columns(2)
    with c1:
        st.json({'sizing_and_concentration':habits.get('sizing_and_concentration'),'execution_and_turnover':habits.get('execution_and_turnover')})
    with c2:
        st.json({'gain_loss_asymmetry':habits.get('gain_loss_asymmetry'),'tax_and_journal_discipline':habits.get('tax_and_journal_discipline')})
    if habits.get('findings'):
        st.subheader('Automated Improvement Prompts')
        st.dataframe(habits['findings'], hide_index=True)
    st.subheader('Recursive Memory: Goals, Mistake Post-Mortems, Case Lessons & Thesis Audit')
    if loop.get('thesis_calibration'):
        st.caption('Thesis Calibration & Falsifier Discipline')
        st.json(loop['thesis_calibration'])
    if loop.get('thesis_audit'):
        st.caption('Open & past decision theses joined against live portfolio holdings')
        st.dataframe(loop['thesis_audit'], hide_index=True)
    for label, key in (('Active Improvement Goals','active_goals'),('Past Mistake Guardrails','past_mistakes'),('Learned Case Studies','case_lessons')):
        if loop.get(key):
            st.caption(label)
            st.dataframe(loop[key], hide_index=True)
    st.divider()
    f1, f2=st.columns(2)
    with f1:
        st.subheader('Log Mistake / Case Lesson / Improvement Goal')
        l_kind=st.selectbox('Record type', ['mistake','goal','lesson'])
        l_sym=st.text_input('Related symbol (optional)', '', key='l_sym')
        l_title=st.text_input('Short title', '', key='l_title')
        l_detail=st.text_area('What happened / evidence / context', '', key='l_detail')
        l_rule=st.text_input('Actionable guardrail rule for future decisions', '', key='l_rule')
        if st.button('Save learning record'):
            try:
                mid=save_lesson(l_kind, l_title, l_detail, 'dashboard_operator', symbol=l_sym, action_rule=l_rule)
                st.success(f'Saved {l_kind} #{mid}'); st.rerun()
            except Exception as exc: st.error(str(exc))
    with f2:
        st.subheader('Log Stock Thesis & Falsifier')
        j_sym=st.text_input('Instrument symbol', '', key='j_sym')
        j_thesis=st.text_area('Investment thesis', '', key='j_thesis')
        j_risk=st.text_input('Falsifier / exit condition', '', key='j_risk')
        j_conf=st.text_input('Subjective confidence (0.0 to 1.0, optional)', '0.70', key='j_conf')
        j_hit=st.selectbox('Realized outcome (optional, for Brier Score)', ['','1','0'], format_func=lambda x: 'Pending / Open' if x=='' else ('1 - Validated' if x=='1' else '0 - Falsified'))
        j_alt=st.text_input('Alternative considered', '', key='j_alt')
        if st.button('Save decision thesis'):
            try:
                jid=save_journal(j_sym, j_thesis, 'dashboard_operator', alternatives=j_alt, risk=j_risk, confidence=j_conf, outcome_hit=j_hit)
                st.success(f'Saved thesis #{jid}'); st.rerun()
            except Exception as exc: st.error(str(exc))
        r_id=st.text_input('Resolve memory ID (mark completed/fixed)', '', key='r_id')
        r_note=st.text_input('Resolution note (optional)', '', key='r_note')
        if st.button('Mark resolved') and r_id.strip():
            try:
                resolve_memory(int(r_id.strip()), r_note, 'dashboard_operator')
                st.success(f'Resolved #{r_id.strip()}'); st.rerun()
            except Exception as exc: st.error(str(exc))

with research:
    st.subheader('Multi-Stage Fade DCF & Reverse DCF (Expectations Investing)')
    st.caption('Explicit assumptions only. Live upside requires an Alpaca IEX quote with freshness live.')
    rc1, rc2, rc3=st.columns(3)
    with rc1:
        symbol=st.text_input('Symbol (optional)', '')
        fcff=st.text_input('Base FCFF (t0)', '100')
        shares=st.text_input('Diluted shares', '10')
    with rc2:
        stage1_g=st.text_input('Stage-1 growth (e.g. 0.18)', '0.18')
        growth=st.text_input('Terminal growth (e.g. 0.025)', '0.025')
        years_in=st.number_input('Explicit runway years', min_value=1, max_value=15, value=5)
    with rc3:
        wacc=st.text_input('WACC (e.g. 0.10)', '0.10')
        debt=st.text_input('Net debt', '0')
        ref_px=st.text_input('Reference price for Reverse DCF (optional)', '')
    b1, b2=st.columns(2)
    with b1:
        if st.button('Run Multi-Stage Fade DCF'):
            try:
                res=multi_stage_dcf(fcff, wacc, stage1_g, growth, debt, shares, years=int(years_in), fade=True, symbol=symbol, fetch_live=True)
                st.json({k:v for k,v in res.items() if k not in {'schedule','sensitivity_3x3'}})
                st.caption('Explicit cash-flow schedule')
                st.dataframe(res['schedule'], hide_index=True)
                st.caption('3x3 WACC x Terminal Growth sensitivity (per share)')
                st.dataframe(res['sensitivity_3x3'], hide_index=True)
            except Exception as exc: st.error(str(exc))
    with b2:
        if st.button('Solve Reverse DCF (Implied Growth)'):
            try:
                st.json(reverse_dcf(fcff, wacc, growth, debt, shares, reference_price=ref_px, symbol=symbol, years=int(years_in), fade=True))
            except Exception as exc: st.error(str(exc))
    st.divider()
    st.subheader('6-Point Institutional Pre-Trade Gatekeeper & Red-Team Check')
    g1, g2, g3, g4=st.columns(4)
    with g1: g_sym=st.text_input('Ticker', '', key='g_sym')
    with g2: g_act=st.selectbox('Action', ['BUY','ADD','TRIM','SELL'])
    with g3: g_amt=st.text_input('Notional CAD', '2500')
    with g4: g_acc=st.selectbox('Target account', [row['account'] for row in snap['accounts']])
    if st.button('Evaluate Pre-Trade Gate') and g_sym.strip():
        try: st.json(evaluate_trade_gate(g_sym, g_act, g_amt, g_acc))
        except Exception as exc: st.error(str(exc))
    st.divider()
    st.subheader('SEC 10-K/20-F Forensic Accrual & Risk-Factor Delta Audit')
    st.caption('Strips >=86% unchanged boilerplate across consecutive filings, flags modal risk escalations (may/could -> has/have experienced), and checks OCF/Net Income accrual quality.')
    fd_sym=st.text_input('Ticker for SEC Filing Delta Audit', '', key='fd_sym')
    if st.button('Run SEC Filing Delta Audit') and fd_sym.strip():
        try: st.json(filing_red_flag_audit(fd_sym, include_private=True))
        except Exception as exc: st.error(str(exc))

with ops_tab:
    st.subheader('Stateful Investment Workflows & Human-in-the-Loop Approvals')
    st.caption('LLM agents can execute workflows and stage proposals, while only the human operator here or via CLI can approve and commit to memory.')
    pending_runs=list_workflow_runs('awaiting_approval', 20)
    if pending_runs:
        st.warning(f'{len(pending_runs)} workflow run(s) awaiting human approval')
        st.dataframe(pending_runs, hide_index=True)
        ap1, ap2, ap3=st.columns(3)
        with ap1: sel_run=st.selectbox('Pending Run ID', [r['run_id'] for r in pending_runs])
        with ap2: op_note=st.text_input('Operator approval/rejection note', '')
        with ap3:
            if st.button('Approve & Commit'):
                try: st.json(approve_workflow(sel_run, op_note)); st.rerun()
                except Exception as exc: st.error(str(exc))
            if st.button('Reject Proposal'):
                try: st.json(reject_workflow(sel_run, op_note or 'rejected_in_dashboard')); st.rerun()
                except Exception as exc: st.error(str(exc))
    else:
        st.caption('No pending workflow approvals right now.')
    wc1, wc2=st.columns(2)
    with wc1:
        wf_choice=st.selectbox('Declarative Workflow', [w['workflow_id'] for w in list_workflows()])
        wf_params=st.text_input('Workflow JSON params (optional)', '{}')
        if st.button('Execute Stateful Workflow'):
            try: st.json(run_workflow(wf_choice, wf_params))
            except Exception as exc: st.error(str(exc))
    with wc2:
        st.caption('Recent durable workflow checkpoints')
        recent_wf=list_workflow_runs(None, 10)
        if recent_wf: st.dataframe(recent_wf, hide_index=True)
    st.divider()
    st.subheader('Runtime Observability & DLP-Safe Token Budget Telemetry')
    telem=telemetry_summary(15)
    st.json(telem.get('summary') or {})
    if telem.get('by_tool'): st.dataframe(telem['by_tool'], hide_index=True)
    st.divider()
    st.subheader('Deterministic Evaluation & Regression Benchmark Harness')
    if st.button('Run Golden Evaluation Suite'):
        st.json(run_eval_benchmark())
    eval_hist=list_eval_runs(5)
    if eval_hist: st.dataframe([{'eval_id':e['eval_id'],'passed':e['passed'],'total':e['total'],'score_pct':e['score_pct'],'created_at':e['created_at']} for e in eval_hist], hide_index=True)

with reports:
    board=quote_board()
    st.caption('A Flex snapshot is not a market update. Orders are disabled. Non-live quotes stay labeled.')
    st.write({'as_of':snap['as_of'],'status':snap['status'],'benchmark':'unavailable','orders':'disabled','live_quotes':sum(1 for row in board if row.get('freshness')=='live'),'other_quotes':sum(1 for row in board if row.get('freshness')!='live')})
