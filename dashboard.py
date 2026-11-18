import streamlit as st
from decimal import Decimal
import pandas as pd
from personal_agent.decision import attach_market_price, counterfactual_attribution, evaluate_trade_gate, exposure, factor_stress_test, filing_red_flag_audit, improvement_loop, multi_stage_dcf, portfolio_factor_risk, price_shock, reverse_dcf, risk, tax_positions, trading_habits
from personal_agent.finance import history, performance, portfolio, transactions
from personal_agent.knowledge import resolve_memory, save_journal, save_lesson
from personal_agent.market import bars, quote_board, sync_quotes, system_health
from personal_agent.ops import approve_workflow, list_eval_runs, list_workflow_runs, list_workflows, reject_workflow, run_eval_benchmark, run_workflow, telemetry_summary
from personal_agent.quant import advanced_tail_and_regime_risk, allocate_incremental_capital, analyze_option_strategy, backtest_strategies, driver_based_valuation, evaluate_expectation_gap, get_living_thesis, policy_compliance, record_earnings_expectation, simulate_leveraged_etf, triage_catalyst_event, upsert_living_thesis
from personal_agent.report import daily_brief

st.set_page_config(page_title='Personal Investment Intelligence', layout='wide')
st.title('Personal Investment Intelligence')

try: snap=portfolio()
except Exception as exc:
    st.error(f'Portfolio unavailable: {type(exc).__name__}'); st.stop()
if 'nav' not in snap:
    st.warning('Portfolio ledger not initialized: '+snap.get('status','unavailable'))
    st.info('Copy config/personal.example.json to config/personal.json, map your IBKR account aliases, and run `ibkr-agent import-flex <statement.xml>` or `ibkr-agent sync-flex`. To run the standalone verification and 10-case benchmark suite without brokerage data, execute `python scripts/verify.py` and `ibkr-agent eval`.')
    st.json(snap); st.stop()

account=st.sidebar.selectbox('Account Scope', ['All']+[row['account'] for row in snap['accounts']])
selected=None if account=='All' else account
with st.sidebar.expander('System & Provider Readiness', expanded=False):
    health=system_health()
    st.caption('Boolean status only; secret values are never displayed.')
    st.json({'services':health['services'],'data_providers':health['data_providers'],'cloud_llm_keys':health['external_cloud_llm_keys']})

perf=performance()
pol=policy_compliance(selected)
tail=advanced_tail_and_regime_risk('QQQ', selected)
factor=portfolio_factor_risk('QQQ', 90, selected)

overview, opp_tab, cat_tab, risk_tab, journal_tab, opt_tab, strat_tab, ledger_tab, ops_tab=st.tabs([
    'Executive Overview',
    'Investment Opportunities',
    'Catalyst & Thesis Monitor',
    'Risk Center',
    'Decision Journal & Attribution',
    'Options Lab',
    'Strategy Lab',
    'Holdings & Tax Ledger',
    'Workflows & HITL Ops'
])

with overview:
    st.caption(f"Statement as of {snap['as_of']} | Base {snap['currency']} | Policy status: {pol.get('status')} | Advice allowed: {snap.get('advice_allowed')}")
    if snap['status']!='ok':
        st.warning('Stale or incomplete statement snapshot. Do not treat figures as live execution advice.')
    rows=[row for row in snap['accounts'] if selected in (None,row['account'])]
    shown_nav=sum((Decimal(row['nav']) for row in rows), Decimal(0))
    beta_attr=tail.get('beta_adjusted_attribution') or {}
    rb=pol.get('risk_budget') or {}
    exp_s=pol.get('exposure_summary') or {}

    kpi1, kpi2, kpi3=st.columns(3)
    with kpi1:
        st.metric('Net Asset Value', f"{shown_nav:,.2f} {snap['currency']}", f"Cash reserve {(exp_s.get('cash_weight') or 0)*100:.1f}%")
    with kpi2:
        act_str=f"{beta_attr['naive_excess_vs_benchmark']*100:+.2f}% vs QQQ USD" if beta_attr.get('naive_excess_vs_benchmark') is not None else (f"TWR {float(perf['twr'])*100:+.2f}%" if perf.get('twr') is not None else 'Withheld')
        sub_str=f"Beta-adj residual {beta_attr['beta_adjusted_selection_and_fx_residual']*100:+.2f}%" if beta_attr.get('beta_adjusted_selection_and_fx_residual') is not None else 'Daily-approx TWR'
        st.metric('Active Return (Approx)', act_str, sub_str)
    with kpi3:
        dd_used=rb.get('drawdown_budget_utilized_pct')
        st.metric('Risk Budget Used', f"{dd_used:.1f}%" if dd_used is not None else 'Unavailable', f"Max DD {(rb.get('max_drawdown_realized') or 0)*100:.2f}% / Limit {(rb.get('drawdown_budget') or -0.25)*100:.0f}%")

    mid_l, mid_r=st.columns([3, 2])
    with mid_l:
        st.subheader('Portfolio Performance vs Benchmark Proxy')
        intervals=perf.get('intervals') or []
        qqq_bars={b['date']:float(b['close']) for b in bars('QQQ', 400)}
        if intervals:
            lvl=100.0; curve_rows=[]; q0=None
            for item in intervals:
                dt=item['to']; lvl*=(1.0+float(item['interval_return']))
                row_pt={'date':dt,'Portfolio (CAD Daily-Approx = 100)':round(lvl,2)}
                if dt in qqq_bars:
                    if q0 is None: q0=qqq_bars[dt]
                    row_pt['QQQ Proxy (USD Price = 100)']=round(100.0*qqq_bars[dt]/q0, 2)
                curve_rows.append(row_pt)
            st.caption('Indexed to 100. Portfolio is CAD daily-approximation cumulative return; QQQ is USD close price return (not CAD total return).')
            st.line_chart(pd.DataFrame(curve_rows).set_index('date'))
        else:
            series=history(None)
            if series:
                frame=pd.DataFrame(series); frame['nav']=frame['nav'].astype(float)
                st.caption('Raw NAV history (includes external cash flows; not return).')
                st.line_chart(frame.set_index('date')['nav'])
    with mid_r:
        st.subheader('Risk & Policy Exposure')
        tac_w=min(1.0, max(0.0, float(exp_s.get('tactical_weight') or 0.0)))
        p_beta=float((factor.get('portfolio_metrics') or {}).get('portfolio_empirical_beta_nav_scaled') or 0.0)
        econ_lev=float(exp_s.get('economic_equity_exposure') or 0.0)
        opt_w=min(1.0, max(0.0, float(exp_s.get('options_nominal_weight') or 0.0)))
        st.caption(f"Tactical Growth Equity Weight: {tac_w*100:.1f}%")
        st.progress(tac_w)
        st.caption(f"Portfolio Empirical Market Beta: {p_beta:.2f} (bar scaled vs 1.50)")
        st.progress(min(1.0, max(0.0, p_beta/1.50)))
        st.caption(f"Daily Economic Equity Exposure: {econ_lev*100:.1f}% (IPS cap {(pol.get('policy_targets') or {}).get('max_economic_leverage',1.2)*100:.0f}%)")
        st.progress(min(1.0, max(0.0, abs(econ_lev)/1.50)))
        st.caption(f"Options Nominal Weight: {opt_w*100:.1f}%")
        st.progress(opt_w)
        if pol.get('breaches'):
            st.warning('Policy breaches detected')
            st.dataframe(pol['breaches'], hide_index=True)

    st.divider()
    brief=daily_brief()
    b_col1, b_col2=st.columns(2)
    with b_col1:
        st.subheader('Daily Coaching & Next Focus')
        if brief.get('recursive_coaching',{}).get('next_focus'):
            st.dataframe(brief['recursive_coaching']['next_focus'], hide_index=True)
    with b_col2:
        st.subheader('Live & Cached Quote Monitor')
        if st.button('Refresh US IEX Quotes'): st.session_state['quotes']=sync_quotes()['quotes']
        st.dataframe(st.session_state.get('quotes') or quote_board(), hide_index=True)

with opp_tab:
    st.subheader('Incremental Capital Allocation & Opportunity-Cost Optimizer')
    st.caption('Compare deploying new cash across candidates vs core benchmark and cash under IPS caps, Quarter-Kelly sizing, and Canadian TFSA/Taxable location.')
    ac1, ac2=st.columns(2)
    with ac1: add_cap=st.text_input('New capital to allocate (Base CAD)', '5000')
    with ac2: cand_in=st.text_input('Candidate tickers CSV (blank = top holdings + QQQ)', '')
    if st.button('Compare Capital Allocation Options'):
        try:
            c_list=[x.strip() for x in cand_in.split(',') if x.strip()] if cand_in.strip() else None
            alloc=allocate_incremental_capital(add_cap, c_list)
            st.dataframe(alloc['candidates'], hide_index=True)
        except Exception as exc: st.error(str(exc))
    st.divider()
    st.subheader('Driver-Based 3-Scenario Valuation & Reverse DCF Expectations')
    dc1, dc2, dc3=st.columns(3)
    with dc1:
        v_sym=st.text_input('Ticker (optional)', '', key='v_sym')
        rev0=st.text_input('Trailing Revenue (t0)', '1000')
        fcff0=st.text_input('Trailing FCFF (for Reverse DCF)', '180')
        sh_in=st.text_input('Diluted shares', '100')
    with dc2:
        rev_g=st.text_input('Base Stage-1 Revenue CAGR', '0.16')
        op_m=st.text_input('Target Operating Margin', '0.28')
        wacc_in=st.text_input('WACC', '0.10')
        gt_in=st.text_input('Terminal growth', '0.025')
    with dc3:
        debt_in=st.text_input('Net debt', '0')
        yrs_v=st.number_input('Explicit runway years', min_value=1, max_value=15, value=5)
        ref_px=st.text_input('Reference price for Reverse DCF', '')
    vb1, vb2, vb3=st.columns(3)
    with vb1:
        if st.button('Run Driver Bear/Base/Bull DCF'):
            try: st.json(driver_based_valuation(rev0, rev_g, op_m, wacc=wacc_in, terminal_growth=gt_in, net_debt=debt_in, shares=sh_in, years=int(yrs_v), symbol=v_sym))
            except Exception as exc: st.error(str(exc))
    with vb2:
        if st.button('Solve Market-Implied Reverse DCF'):
            try: st.json(reverse_dcf(fcff0, wacc_in, gt_in, debt_in, sh_in, reference_price=ref_px, symbol=v_sym, years=int(yrs_v), fade=True))
            except Exception as exc: st.error(str(exc))
    with vb3:
        if st.button('Run 6-Point Pre-Trade Gate') and v_sym.strip():
            try: st.json(evaluate_trade_gate(v_sym, 'BUY', add_cap, snap['accounts'][0]['account']))
            except Exception as exc: st.error(str(exc))

with cat_tab:
    st.subheader('Versioned Living Theses & L0-L4 Catalyst Triage')
    theses=get_living_thesis('')
    if theses: st.dataframe(theses, hide_index=True)
    tc1, tc2=st.columns(2)
    with tc1:
        st.caption('Create or Increment Living Thesis Version')
        t_sym=st.text_input('Symbol', '', key='t_sym')
        t_pil=st.text_input('Core pillars (semicolon-separated)', '')
        t_kpi=st.text_input('Key KPIs to track (semicolon-separated)', '')
        t_cat=st.text_input('Upcoming catalysts (semicolon-separated)', '')
        t_brk=st.text_input('Invalidation / Thesis Breakers (semicolon-separated)', '')
        if st.button('Save Living Thesis Version') and t_sym.strip() and t_pil.strip() and t_brk.strip():
            st.json(upsert_living_thesis(t_sym, t_pil, t_kpi, t_cat, t_brk)); st.rerun()
    with tc2:
        st.caption('Pre-Earnings Expectation Snapshot vs Actual Surprise & SEC Filing Delta')
        e_sym=st.text_input('Ticker', '', key='e_sym')
        e_per=st.text_input('Fiscal period (e.g. 2026Q4)', '2026Q4')
        e_basis=st.selectbox('Expectation basis (never fabricated)', ['management_guidance','internal_model','licensed_consensus'])
        ex1, ex2=st.columns(2)
        with ex1:
            er_in=st.text_input('Expected Revenue', ''); ee_in=st.text_input('Expected EPS', '')
        with ex2:
            ar_in=st.text_input('Actual Revenue (post-release)', ''); ae_in=st.text_input('Actual EPS (post-release)', '')
        if st.button('Save / Evaluate Earnings Expectation Gap') and e_sym.strip():
            st.json(record_earnings_expectation(e_sym, e_per, e_basis, er_in or None, ee_in or None, act_revenue=ar_in or None, act_eps=ae_in or None))
        ev_head=st.text_input('Headline for L0-L4 Materiality Triage', '')
        if st.button('Classify Event Impact Tier') and e_sym.strip() and ev_head.strip():
            st.json(triage_catalyst_event(e_sym, ev_head))
        if st.button('Run SEC 10-K/20-F Forensic Delta Audit') and e_sym.strip():
            st.json(filing_red_flag_audit(e_sym, include_private=True))

with risk_tab:
    st.subheader('Tail Risk (CVaR 95%), Economic Exposure & Covariance MCTR')
    st.json({'tail_risk':tail.get('tail_risk'),'economic_exposure':tail.get('economic_exposure'),'factor_headline':factor.get('portfolio_metrics')})
    if factor.get('status')=='ok':
        st.caption('Per-Stock Marginal Contribution to Risk (MCTR) & Variance Share')
        st.dataframe(factor['risk_attribution'], hide_index=True)
        if factor.get('high_correlation_clusters'):
            st.caption('High-Correlation Clusters (rho >= 0.65)')
            st.dataframe(factor['high_correlation_clusters'], hide_index=True)
    st.divider()
    rc_a, rc_b=st.columns(2)
    with rc_a:
        st.subheader('Joint Empirical Beta + FX Stress Test')
        b_sh=st.text_input('Benchmark shock (QQQ)', '-0.15')
        fx_sh=st.text_input('USD/CAD FX shock', '0.02')
        f_st=factor_stress_test(b_sh, fx_sh, 'QQQ', selected)
        st.write({'pnl_impact_base':f_st.get('portfolio_pnl_impact_base'),'nav_impact_pct':f_st.get('portfolio_nav_impact_pct')})
        if f_st.get('positions'): st.dataframe(f_st['positions'][:15], hide_index=True)
    with rc_b:
        st.subheader('Daily-Reset Leveraged ETF Decay Simulator (e.g. TQQQ 3x)')
        lv_m=st.text_input('Daily reset leverage multiple', '3.0')
        lv_mu=st.text_input('Assumed underlying annual return', '0.12')
        lv_vol=st.text_input('Assumed underlying annual volatility', '0.28')
        st.json(simulate_leveraged_etf(lv_mu, lv_vol, lv_m, years=3, benchmark='QQQ'))

with journal_tab:
    st.subheader('Counterfactual Buy-and-Hold Attribution & Beta-Adjusted Alpha')
    if beta_attr: st.json(beta_attr)
    cf_days=st.selectbox('Counterfactual replay window (days)', [30, 60, 90, 180, 365], index=2)
    attrib=counterfactual_attribution(int(cf_days), selected)
    if 'wealth_comparison' in attrib:
        st.write({'verdict':attrib.get('verdict'), **attrib['wealth_comparison']})
        st.json(attrib.get('attribution_breakdown') or {})
        ca1, ca2=st.columns(2)
        with ca1:
            st.caption('Best Active Decisions')
            if attrib.get('best_decisions'): st.dataframe(attrib['best_decisions'], hide_index=True)
        with ca2:
            st.caption('Costliest Decisions & Premature Winner Exits')
            if attrib.get('costliest_decisions'): st.dataframe(attrib['costliest_decisions'], hide_index=True)
    st.divider()
    habits=trading_habits(selected); loop=improvement_loop()
    st.subheader('Behavioral Telemetry, Brier Calibration & Mistake Guardrails')
    st.json({'thesis_calibration':loop.get('thesis_calibration'),'execution_and_turnover':habits.get('execution_and_turnover')})
    if habits.get('findings'): st.dataframe(habits['findings'], hide_index=True)
    if loop.get('thesis_audit'): st.dataframe(loop['thesis_audit'], hide_index=True)
    jf1, jf2=st.columns(2)
    with jf1:
        l_kind=st.selectbox('Learning record type', ['mistake','goal','lesson'])
        l_sym=st.text_input('Related symbol (optional)', '', key='l_sym')
        l_title=st.text_input('Short title', '', key='l_title')
        l_detail=st.text_area('Evidence / post-mortem context', '', key='l_detail')
        l_rule=st.text_input('Actionable guardrail rule', '', key='l_rule')
        if st.button('Save Learning Guardrail'):
            mid=save_lesson(l_kind, l_title, l_detail, 'dashboard_operator', symbol=l_sym, action_rule=l_rule)
            st.success(f'Saved #{mid}'); st.rerun()
    with jf2:
        j_sym=st.text_input('Instrument symbol', '', key='j_sym')
        j_thesis=st.text_area('Decision thesis', '', key='j_thesis')
        j_risk=st.text_input('Falsifier / exit trigger', '', key='j_risk')
        j_conf=st.text_input('Subjective probability (0.0 to 1.0)', '0.70', key='j_conf')
        j_hit=st.selectbox('Realized outcome (for Brier Score)', ['','1','0'], format_func=lambda x: 'Open' if x=='' else ('1 - Validated' if x=='1' else '0 - Falsified'))
        if st.button('Save Decision Thesis'):
            jid=save_journal(j_sym, j_thesis, 'dashboard_operator', risk=j_risk, confidence=j_conf, outcome_hit=j_hit)
            st.success(f'Saved thesis #{jid}'); st.rerun()

with opt_tab:
    st.subheader('Options Lab: Analytical Black-Scholes Greeks, IV Solver, Covered Call & LEAPS')
    st.caption('Deterministic Black-Scholes-Merton model over explicit inputs (100-share contract); not live OPRA quotes.')
    oc1, oc2, oc3=st.columns(3)
    with oc1:
        o_strat=st.selectbox('Option strategy', ['covered_call','leaps_call','long_call','long_put'])
        o_spot=st.text_input('Underlying spot price', '100')
        o_basis=st.text_input('Share cost basis (for Covered Call)', '95')
    with oc2:
        o_strike=st.text_input('Strike price', '110')
        o_dte=st.number_input('Days to expiry (DTE)', min_value=1, max_value=1095, value=45)
    with oc3:
        o_prem=st.text_input('Option market premium (optional -> solves IV)', '')
        o_vol=st.text_input('Assumed Implied Volatility (if premium blank)', '0.35')
    if st.button('Analyze Option Strategy'):
        try: st.json(analyze_option_strategy(o_strat, o_spot, o_strike, int(o_dte), o_prem, o_vol, '0.04', o_basis))
        except Exception as exc: st.error(str(exc))

with strat_tab:
    st.subheader('Strategy Lab: Walk-Forward Historical Replay (Zero Look-Ahead Bias)')
    st.caption('Compares (A) 100% Buy & Hold, (B) 70/30 Drawdown Dip-Buy, (C) Periodic 10d DCA, and (D) 80% Core + 20% 3x Daily-Reset Sleeve on cached bars.')
    sc_sym=st.text_input('Benchmark / Ticker in local bar cache', 'QQQ')
    sc_cap=st.text_input('Initial capital', '10000')
    sc_bps=st.text_input('Transaction cost (bps)', '5')
    if st.button('Run Strategy Comparison'):
        bt=backtest_strategies(sc_sym, sc_cap, sc_bps)
        if bt.get('strategies'):
            st.caption(f"Window: {bt['window']['from']} to {bt['window']['to']} ({bt['window']['trading_days']} trading days)")
            st.dataframe([{'strategy':k, **v} for k,v in bt['strategies'].items()], hide_index=True)
        else:
            st.info(bt)

with ledger_tab:
    tax=tax_positions(selected)
    st.subheader('Canadian Tax-Weighted Stock Summary (Taxable vs TFSA & ITA s.54 Watch)')
    if tax.get('positions'): st.dataframe(tax['positions'], hide_index=True)
    st.subheader('Raw SUMMARY Positions & Ledger Transactions')
    st.dataframe([r for r in snap['positions'] if selected in (None,r['account'])], hide_index=True)
    cat_sel=st.selectbox('Ledger category', ['trade','cash','corporate_action'])
    st.dataframe(transactions(cat_sel, selected), hide_index=True)

with ops_tab:
    st.subheader('Stateful Workflows (Including 8-Section Investment Committee Memo) & HITL Approvals')
    pending_runs=list_workflow_runs('awaiting_approval', 20)
    if pending_runs:
        st.warning(f'{len(pending_runs)} workflow proposal(s) paused at awaiting_approval')
        st.dataframe(pending_runs, hide_index=True)
        ap1, ap2, ap3=st.columns(3)
        with ap1: sel_run=st.selectbox('Pending Run ID', [r['run_id'] for r in pending_runs])
        with ap2: op_note=st.text_input('Operator decision note', '')
        with ap3:
            if st.button('Approve & Commit'):
                st.json(approve_workflow(sel_run, op_note)); st.rerun()
            if st.button('Reject'):
                st.json(reject_workflow(sel_run, op_note or 'rejected_in_dashboard')); st.rerun()
    wc1, wc2=st.columns(2)
    with wc1:
        wf_choice=st.selectbox('Workflow', [w['workflow_id'] for w in list_workflows()])
        wf_params=st.text_input('Workflow JSON params', '{"symbol":"QQQ","amount_base":"2500"}')
        if st.button('Execute Workflow'):
            try: st.json(run_workflow(wf_choice, wf_params))
            except Exception as exc: st.error(str(exc))
    with wc2:
        recent_wf=list_workflow_runs(None, 10)
        if recent_wf: st.dataframe(recent_wf, hide_index=True)
    st.divider()
    st.subheader('DLP-Safe Observability Spans & 10-Invariant Golden Evaluation Suite')
    telem=telemetry_summary(15)
    st.json(telem.get('summary') or {})
    if telem.get('by_tool'): st.dataframe(telem['by_tool'], hide_index=True)
    if st.button('Run 10-Case Golden Evaluation Benchmark'):
        st.json(run_eval_benchmark())
    eval_hist=list_eval_runs(5)
    if eval_hist: st.dataframe([{'eval_id':e['eval_id'],'passed':e['passed'],'total':e['total'],'score_pct':e['score_pct'],'created_at':e['created_at']} for e in eval_hist], hide_index=True)
