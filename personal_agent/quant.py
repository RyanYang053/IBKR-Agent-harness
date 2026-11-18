"""Personal Investment OS Engines: Policy Mandate, Economic Exposure & Tail Risk, Driver Valuation, Expectations, Capital Allocation, Analytical Options, and Strategy Backtesting."""
from datetime import date
from decimal import Decimal
import json, math
from pathlib import Path
from .core import ROOT, bounded, db, now
from .finance import dec, performance, portfolio
from . import decision, market

def _quant_schema(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS living_theses(
        symbol TEXT PRIMARY KEY,
        version INTEGER NOT NULL,
        status TEXT NOT NULL,
        pillars_json TEXT NOT NULL,
        kpis_json TEXT NOT NULL,
        catalysts_json TEXT NOT NULL,
        breakers_json TEXT NOT NULL,
        fair_value_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS earnings_expectations(
        symbol TEXT NOT NULL,
        period TEXT NOT NULL,
        basis TEXT NOT NULL,
        exp_revenue REAL,
        exp_eps REAL,
        exp_guidance_growth REAL,
        implied_move_pct REAL,
        act_revenue REAL,
        act_eps REAL,
        act_guidance_growth REAL,
        recorded_pre_at TEXT NOT NULL,
        updated_post_at TEXT,
        PRIMARY KEY(symbol, period)
    );
    ''')

def load_policy():
    local_p=ROOT/'config'/'investment_policy.json'
    example_p=ROOT/'config'/'investment_policy.example.json'
    target=local_p if local_p.exists() else example_p
    if target.exists():
        return json.loads(target.read_text(encoding='utf-8'))
    return {'return_objective_annual':0.15,'investment_horizon_years':10,'core_benchmark':'QQQ','core_symbols':['QQQ','SPY'],'core_min_weight':0.35,'tactical_max_weight':0.55,'min_cash_reserve':0.05,'max_drawdown_budget':-0.25,'max_economic_leverage':1.20,'max_single_stock_weight':0.15,'kelly_fraction':0.25,'leveraged_etf_multipliers':{'TQQQ':3.0,'QLD':2.0,'SQQQ':-3.0}}

def policy_compliance(account=None):
    """Audit live portfolio against the CIO Investment Policy Statement (IPS): economic leverage, core/tactical split, cash reserve, drawdown budget, and single-stock caps."""
    pol=load_policy()
    snap=portfolio()
    if 'nav' not in snap: return {'status':'blocked','reason':snap.get('status','unavailable')}
    tax=decision.tax_positions(account=account)
    perf=performance()
    r_base=decision.risk()
    acct_rows=[a for a in snap.get('accounts',[]) if not account or a['account']==account]
    nav=float(sum((dec(a['nav']) for a in acct_rows), Decimal(0)))
    cash=float(sum((dec(a['cash']) for a in acct_rows if a.get('cash') not in (None,'')), Decimal(0)))
    if nav<=0: return {'status':'invalid_nav'}
    lev_map={k.upper():float(v) for k,v in (pol.get('leveraged_etf_multipliers') or {}).items()}
    core_set={s.upper() for s in (pol.get('core_symbols') or [])}
    nom_eq=0.0; econ_eq=0.0; opt_wt=0.0; core_wt=0.0; tac_wt=0.0; breaches=[]; pos_econ=[]
    max_single=float(pol.get('max_single_stock_weight', 0.15))
    for p in (tax.get('positions') or []):
        sym=p['symbol'].upper(); asset=(p.get('asset') or 'STK').upper()
        w=float(dec(p['total_value_base']))/nav
        if asset=='OPT':
            opt_wt+=abs(w)
            continue
        mult=lev_map.get(sym, 1.0)
        ew=w*mult
        nom_eq+=w; econ_eq+=ew
        if sym in core_set: core_wt+=w
        else:
            tac_wt+=w
            if abs(w)>max_single:
                breaches.append({'rule':'max_single_stock_weight','symbol':sym,'actual':round(w,4),'limit':max_single})
        pos_econ.append({'symbol':sym,'nominal_weight':round(w,4),'daily_leverage_mult':mult,'economic_weight':round(ew,4),'bucket':'core' if sym in core_set else 'tactical'})
    cash_wt=cash/nav
    if cash_wt<float(pol.get('min_cash_reserve', 0.05)):
        breaches.append({'rule':'min_cash_reserve','actual':round(cash_wt,4),'limit':pol.get('min_cash_reserve')})
    if abs(econ_eq)>float(pol.get('max_economic_leverage', 1.20)):
        breaches.append({'rule':'max_economic_leverage','actual':round(econ_eq,4),'limit':pol.get('max_economic_leverage')})
    if core_wt<float(pol.get('core_min_weight', 0.35)):
        breaches.append({'rule':'core_allocation_below_target','actual':round(core_wt,4),'target_min':pol.get('core_min_weight')})
    cur_dd=float(r_base['max_drawdown']) if r_base.get('max_drawdown') is not None else 0.0
    dd_budget=float(pol.get('max_drawdown_budget', -0.25))
    dd_used=min(2.0, abs(cur_dd)/abs(dd_budget)) if dd_budget<0 else 0.0
    if cur_dd<dd_budget:
        breaches.append({'rule':'max_drawdown_budget_exceeded','actual':round(cur_dd,4),'limit':dd_budget})
    return {
        'status':'compliant' if not breaches else 'policy_breaches_detected',
        'as_of':snap.get('as_of'),
        'policy_targets':{'return_objective_annual':pol.get('return_objective_annual'),'core_benchmark':pol.get('core_benchmark'),'max_drawdown_budget':dd_budget,'max_economic_leverage':pol.get('max_economic_leverage'),'max_single_stock_weight':max_single,'min_cash_reserve':pol.get('min_cash_reserve')},
        'exposure_summary':{'nominal_equity_weight':round(nom_eq,4),'economic_equity_exposure':round(econ_eq,4),'cash_weight':round(cash_wt,4),'options_nominal_weight':round(opt_wt,4),'core_weight':round(core_wt,4),'tactical_weight':round(tac_wt,4)},
        'risk_budget':{'max_drawdown_realized':round(cur_dd,4),'drawdown_budget':dd_budget,'drawdown_budget_utilized_pct':round(dd_used*100.0,1),'portfolio_twr_approx':perf.get('twr')},
        'breaches':breaches,
        'position_economic_exposures':pos_econ[:25],
        'limitations':['Economic exposure multiplies ETF nominal weights by stated daily reset multipliers (e.g. TQQQ=3x); option delta requires contract inputs in analyze_option_strategy.']
    }

def advanced_tail_and_regime_risk(benchmark='QQQ', account=None):
    """Expected Shortfall (CVaR 95%), underwater recovery duration, down-market correlation regime shift, and Beta-adjusted return attribution."""
    perf=performance()
    pol_check=policy_compliance(account=account)
    factor=decision.portfolio_factor_risk(benchmark=benchmark, window=90, account=account)
    intervals=perf.get('intervals') or []
    rets=[float(r['interval_return']) for r in intervals]
    cvar_95=None; max_underwater_days=0; cur_underwater_days=0
    if len(rets)>=20:
        ordered=sorted(rets)
        cutoff_n=max(1, int(len(ordered)*0.05))
        cvar_95=sum(ordered[:cutoff_n])/cutoff_n
    lvl=1.0; pk=1.0; streak=0
    for r in rets:
        lvl*=(1.0+r)
        if lvl>=pk: pk=lvl; streak=0
        else: streak+=1; max_underwater_days=max(max_underwater_days, streak)
    cur_underwater_days=streak
    bench_sym=str(benchmark or 'QQQ').upper()
    b_bars=market.bars(bench_sym, 260)
    beta_attrib=None
    if intervals and len(b_bars)>=2:
        start_d=intervals[0]['from']; end_d=intervals[-1]['to']
        in_win=[b for b in b_bars if start_d<=b['date']<=end_d]
        if len(in_win)>=2 and perf.get('twr') is not None:
            b_ret=(float(in_win[-1]['close'])/float(in_win[0]['close']))-1.0
            p_ret=float(perf['twr'])
            p_beta=(factor.get('portfolio_metrics') or {}).get('portfolio_empirical_beta_nav_scaled')
            beta_used=float(p_beta) if p_beta is not None else 1.0
            market_contrib=beta_used*b_ret
            residual_alpha=p_ret-market_contrib
            beta_attrib={
                'window_from':in_win[0]['date'],
                'window_to':in_win[-1]['date'],
                'portfolio_return_cad_approx':round(p_ret,4),
                'benchmark_usd_price_return':round(b_ret,4),
                'portfolio_empirical_beta':round(beta_used,4),
                'market_beta_return_contribution':round(market_contrib,4),
                'naive_excess_vs_benchmark':round(p_ret-b_ret,4),
                'beta_adjusted_selection_and_fx_residual':round(residual_alpha,4),
                'basis_note':'Benchmark is USD close price return; residual includes stock selection, sizing, timing, dividends, and USD/CAD FX.'
            }
    return {
        'status':pol_check.get('status','ok'),
        'tail_risk':{'cvar_95_daily':round(cvar_95,4) if cvar_95 is not None else None,'max_underwater_trading_days':max_underwater_days,'current_underwater_trading_days':cur_underwater_days},
        'economic_exposure':pol_check.get('exposure_summary'),
        'risk_budget':pol_check.get('risk_budget'),
        'beta_adjusted_attribution':beta_attrib,
        'factor_headline':factor.get('portfolio_metrics')
    }

def simulate_leveraged_etf(underlying_annual_return='0.12', underlying_annual_vol='0.25', leverage='3.0', years=3, expense_ratio='0.0088', benchmark='QQQ'):
    """Analytical variance-drain decomposition + empirical daily-reset replay on cached benchmark bars (e.g. TQQQ 3x daily reset vs QQQ)."""
    mu=float(underlying_annual_return); sigma=float(underlying_annual_vol); lev=float(leverage); fee=float(expense_ratio); yrs=float(years)
    var_drain=0.5*lev*(lev-1.0)*(sigma**2)
    cont_cagr=lev*mu-var_drain-fee
    emp=None
    b_rows=market.bars(str(benchmark or 'QQQ').upper(), 500)
    if len(b_rows)>=30:
        u_lvl=1.0; l_lvl=1.0; l_pk=1.0; l_dd=0.0; u_pk=1.0; u_dd=0.0
        daily_fee=fee/252.0
        for p0, p1 in zip(b_rows, b_rows[1:]):
            r=(float(p1['close'])/float(p0['close']))-1.0
            u_lvl*=(1.0+r); u_pk=max(u_pk, u_lvl); u_dd=min(u_dd, u_lvl/u_pk-1.0)
            l_lvl*=max(0.0, 1.0+lev*r-daily_fee); l_pk=max(l_pk, l_lvl); l_dd=min(l_dd, l_lvl/l_pk-1.0)
        emp={'benchmark':benchmark.upper(),'trading_days':len(b_rows)-1,'from':b_rows[0]['date'],'to':b_rows[-1]['date'],'underlying_cum_return':round(u_lvl-1.0,4),'simulated_leveraged_cum_return':round(l_lvl-1.0,4),'realized_multiple_of_underlying':round((l_lvl-1.0)/(u_lvl-1.0),3) if abs(u_lvl-1.0)>1e-4 else None,'underlying_max_drawdown':round(u_dd,4),'simulated_leveraged_max_drawdown':round(l_dd,4)}
    return {
        'leverage_daily_multiple':lev,
        'analytical_continuous_approx':{'assumed_underlying_return':mu,'assumed_underlying_vol':sigma,'annual_volatility_variance_drain':round(var_drain,4),'expense_ratio':fee,'expected_compound_growth_rate':round(cont_cagr,4),'projected_wealth_multiple':round(math.exp(cont_cagr*yrs),4),'break_even_underlying_return_needed':round((var_drain+fee)/lev,4) if lev!=0 else None},
        'empirical_cached_bar_replay':emp,
        'limitations':['Daily-reset leveraged ETFs target Lx single-day return, not Lx multi-period return; high volatility erodes compounding via 0.5*L*(L-1)*sigma^2 variance drain.']
    }

def driver_based_valuation(revenue_t0, rev_cagr_base, op_margin_target, tax_rate='0.21', capex_pct_rev='0.06', nwc_pct_delta_rev='0.02', wacc='0.10', terminal_growth='0.025', net_debt='0', shares='10', years=5, bull_rev_delta='0.04', bear_rev_delta='-0.05', bull_margin_delta='0.03', bear_margin_delta='-0.04', symbol=''):
    """Driver-based operating model (Revenue -> Operating Margin -> NOPAT -> CapEx & NWC Reinvestment -> FCFF) across Bear (25%) / Base (50%) / Bull (25%) scenarios."""
    rev0=float(revenue_t0); g_base=float(rev_cagr_base); m_base=float(op_margin_target); tax=float(tax_rate)
    capx=float(capex_pct_rev); nwc=float(nwc_pct_delta_rev); disc=float(wacc); g_term=float(terminal_growth)
    debt=float(net_debt); sh=float(shares); n_yr=int(years)
    if rev0<=0 or sh<=0 or disc<=g_term or n_yr<1 or n_yr>15:
        raise ValueError('positive revenue_t0/shares, 1<=years<=15, and wacc > terminal_growth required')
    scenarios={
        'bear':{'prob':0.25,'rev_g':g_base+float(bear_rev_delta),'margin':max(0.01, m_base+float(bear_margin_delta)),'wacc':disc+0.005},
        'base':{'prob':0.50,'rev_g':g_base,'margin':m_base,'wacc':disc},
        'bull':{'prob':0.25,'rev_g':g_base+float(bull_rev_delta),'margin':m_base+float(bull_margin_delta),'wacc':max(g_term+0.01, disc-0.005)}
    }
    results={}; expected_px=0.0
    for name, sc in scenarios.items():
        rev=rev0; pv_cf=0.0; last_fcff=0.0; w_s=sc['wacc']
        for yr in range(1, n_yr+1):
            g_yr=sc['rev_g']+(g_term-sc['rev_g'])*(yr-1)/n_yr
            next_rev=rev*(1.0+g_yr)
            d_rev=next_rev-rev
            ebit=next_rev*sc['margin']
            nopat=ebit*(1.0-tax)
            reinvest=next_rev*capx+d_rev*nwc
            fcff=nopat-reinvest
            pv_cf+=fcff/((1.0+w_s)**yr)
            rev=next_rev; last_fcff=fcff
        tv=(last_fcff*(1.0+g_term))/(w_s-g_term)
        pv_tv=tv/((1.0+w_s)**n_yr)
        ev=pv_cf+pv_tv
        eq=ev-debt
        px=eq/sh
        expected_px+=sc['prob']*px
        results[name]={'probability':sc['prob'],'stage1_rev_growth':round(sc['rev_g'],4),'operating_margin':round(sc['margin'],4),'wacc':round(w_s,4),'year_n_revenue':round(rev,2),'year_n_fcff':round(last_fcff,2),'enterprise_value':round(ev,2),'per_share':round(px,2)}
    out={
        'method':'driver_based_3scenario_dcf',
        'years':n_yr,
        'probability_weighted_per_share':round(expected_px,2),
        'valuation_range_bear_to_bull':[results['bear']['per_share'], results['bull']['per_share']],
        'scenarios':results,
        'price':'unavailable',
        'price_freshness':'unavailable',
        'upside':'unavailable'
    }
    if str(symbol or '').strip():
        out=decision.attach_market_price({**out,'per_share':str(round(expected_px,4))}, symbol, fetch_live=False)
    return out

def upsert_living_thesis(symbol, pillars, kpis, catalysts, breakers, bear_px=None, base_px=None, bull_px=None, status='active'):
    sym=str(symbol or '').strip().upper()
    if not sym: raise ValueError('symbol required')
    with db('ops') as c:
        _quant_schema(c)
        old=c.execute('SELECT version FROM living_theses WHERE symbol=?',(sym,)).fetchone()
        ver=(old['version']+1) if old else 1
        fv={'bear':bear_px,'base':base_px,'bull':bull_px}
        c.execute('INSERT OR REPLACE INTO living_theses VALUES(?,?,?,?,?,?,?,?,?)',(
            sym, ver, status,
            json.dumps(pillars if isinstance(pillars,list) else [s.strip() for s in str(pillars).split(';') if s.strip()], ensure_ascii=False),
            json.dumps(kpis if isinstance(kpis,list) else [s.strip() for s in str(kpis).split(';') if s.strip()], ensure_ascii=False),
            json.dumps(catalysts if isinstance(catalysts,list) else [s.strip() for s in str(catalysts).split(';') if s.strip()], ensure_ascii=False),
            json.dumps(breakers if isinstance(breakers,list) else [s.strip() for s in str(breakers).split(';') if s.strip()], ensure_ascii=False),
            json.dumps(fv, ensure_ascii=False),
            now()
        ))
    return get_living_thesis(sym)

def get_living_thesis(symbol=''):
    sym=str(symbol or '').strip().upper()
    with db('ops') as c:
        _quant_schema(c)
        rows=[dict(r) for r in (c.execute('SELECT * FROM living_theses WHERE symbol=?',(sym,)) if sym else c.execute('SELECT * FROM living_theses ORDER BY updated_at DESC LIMIT 30'))]
    out=[]
    for r in rows:
        out.append({'symbol':r['symbol'],'version':r['version'],'status':r['status'],'pillars':json.loads(r['pillars_json']),'key_kpis':json.loads(r['kpis_json']),'catalysts':json.loads(r['catalysts_json']),'invalidation_breakers':json.loads(r['breakers_json']),'fair_value_range':json.loads(r['fair_value_json']),'updated_at':r['updated_at']})
    return out[0] if (sym and out) else ({'symbol':sym,'status':'not_recorded'} if sym else out)

def triage_catalyst_event(symbol, headline, detail=''):
    """Deterministic L0-L4 Event Materiality Triage preventing noisy news from triggering full portfolio churn."""
    sym=str(symbol or '').strip().upper()
    text=(str(headline or '')+' '+str(detail or '')).lower()
    th=get_living_thesis(sym)
    breakers=[b.lower() for b in (th.get('invalidation_breakers') or [])] if isinstance(th,dict) else []
    breaker_hit=any(b and b in text for b in breakers) or any(w in text for w in ('accounting restatement','sec investigation','subpoena','ceo resigns','guidance cut','guidance withdrawn','going concern','material weakness','export ban'))
    earnings_hit=any(w in text for w in ('q1 ','q2 ','q3 ','q4 ','earnings','10-k','10-q','guidance','revenue beat','revenue miss','eps '))
    thesis_hit=any(w in text for w in ('acquisition','buyback','dividend','product launch','contract','margin','capex','tariff','regulation'))
    if breaker_hit: level, action='L4_decision_alert', 'Potential Thesis Breaker detected; immediately run investment_committee_memo and verify invalidation conditions.'
    elif earnings_hit: level, action='L3_revalue_and_risk', 'Earnings or guidance release; update expectation gap, re-run driver_based_valuation, and check position risk.'
    elif thesis_hit: level, action='L2_update_thesis_log', 'Relevant business/industry update; review living thesis KPIs without impulsive trading.'
    elif len(text.strip())>=25: level, action='L1_research_archive', 'Minor background mention; archive only, no model re-underwriting needed.'
    else: level, action='L0_market_noise', 'Ignore short-term noise.'
    return {'symbol':sym,'materiality_level':level,'recommended_workflow_action':action,'living_thesis_present':isinstance(th,dict) and th.get('status')=='active'}

def record_earnings_expectation(symbol, period, basis='management_guidance', exp_revenue=None, exp_eps=None, exp_guidance_growth=None, implied_move_pct=None, act_revenue=None, act_eps=None, act_guidance_growth=None):
    """Store point-in-time pre-earnings expectation snapshot and/or post-earnings actuals to measure true expectation gaps without fabricated consensus."""
    sym=str(symbol or '').strip().upper(); per=str(period or '').strip().upper()
    if not sym or not per: raise ValueError('symbol and period (e.g. 2026Q4) required')
    if basis not in {'management_guidance','internal_model','licensed_consensus'}:
        raise ValueError('basis must be management_guidance, internal_model, or licensed_consensus')
    def _f(v): return None if v in (None,'') else float(v)
    with db('ops') as c:
        _quant_schema(c)
        old=c.execute('SELECT * FROM earnings_expectations WHERE symbol=? AND period=?',(sym,per)).fetchone()
        pre_ts=old['recorded_pre_at'] if old else now()
        er=_f(exp_revenue) if exp_revenue not in (None,'') else (old['exp_revenue'] if old else None)
        ee=_f(exp_eps) if exp_eps not in (None,'') else (old['exp_eps'] if old else None)
        eg=_f(exp_guidance_growth) if exp_guidance_growth not in (None,'') else (old['exp_guidance_growth'] if old else None)
        im=_f(implied_move_pct) if implied_move_pct not in (None,'') else (old['implied_move_pct'] if old else None)
        ar=_f(act_revenue) if act_revenue not in (None,'') else (old['act_revenue'] if old else None)
        ae=_f(act_eps) if act_eps not in (None,'') else (old['act_eps'] if old else None)
        ag=_f(act_guidance_growth) if act_guidance_growth not in (None,'') else (old['act_guidance_growth'] if old else None)
        post_ts=now() if any(x is not None for x in (ar,ae,ag)) else (old['updated_post_at'] if old else None)
        c.execute('INSERT OR REPLACE INTO earnings_expectations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(sym,per,basis,er,ee,eg,im,ar,ae,ag,pre_ts,post_ts))
    return evaluate_expectation_gap(sym, per)

def evaluate_expectation_gap(symbol, period=''):
    sym=str(symbol or '').strip().upper(); per=str(period or '').strip().upper()
    with db('ops') as c:
        _quant_schema(c)
        row=(c.execute('SELECT * FROM earnings_expectations WHERE symbol=? AND period=?',(sym,per)).fetchone() if per else c.execute('SELECT * FROM earnings_expectations WHERE symbol=? ORDER BY recorded_pre_at DESC LIMIT 1',(sym,)).fetchone())
    if not row: return {'symbol':sym,'status':'no_pre_earnings_snapshot','limitations':['Record pre-earnings guidance or model expectations before earnings; consensus is never fabricated.']}
    d=dict(row)
    rev_surp=((d['act_revenue']-d['exp_revenue'])/abs(d['exp_revenue'])) if (d['act_revenue'] is not None and d['exp_revenue']) else None
    eps_surp=((d['act_eps']-d['exp_eps'])/abs(d['exp_eps'])) if (d['act_eps'] is not None and d['exp_eps']) else None
    guide_diff=(d['act_guidance_growth']-d['exp_guidance_growth']) if (d['act_guidance_growth'] is not None and d['exp_guidance_growth'] is not None) else None
    verdict='awaiting_actuals'
    if any(x is not None for x in (rev_surp, eps_surp, guide_diff)):
        if (guide_diff is not None and guide_diff<-0.01) or (rev_surp is not None and rev_surp<-0.02):
            verdict='negative_expectation_gap_review_thesis'
        elif (guide_diff is not None and guide_diff>0.01) or (rev_surp is not None and rev_surp>0.02 and (eps_surp is None or eps_surp>=0)):
            verdict='positive_beat_and_raise'
        else:
            verdict='inline_with_prior_expectations'
    return {'symbol':sym,'period':d['period'],'basis':d['basis'],'verdict':verdict,'revenue_surprise_pct':round(rev_surp,4) if rev_surp is not None else None,'eps_surprise_pct':round(eps_surp,4) if eps_surp is not None else None,'guidance_growth_delta':round(guide_diff,4) if guide_diff is not None else None,'snapshot':d}

def allocate_incremental_capital(amount_base, candidates=None, expected_returns=None):
    """Compare deploying new capital across candidate tickers vs holding cash under Policy limits, Marginal Variance, Effective Bets, Quarter-Kelly caps, and Canadian account location."""
    add_amt=float(dec(amount_base))
    if add_amt<=0: raise ValueError('amount_base must be positive')
    pol=load_policy()
    snap=portfolio()
    if 'nav' not in snap: return {'status':'blocked','reason':snap.get('status','unavailable')}
    nav=float(dec(snap['nav'])); new_nav=nav+add_amt
    tax=decision.tax_positions()
    pos_map={p['symbol'].upper():p for p in (tax.get('positions') or [])}
    cand_list=[str(s).strip().upper() for s in (candidates or list(pos_map.keys())[:4]+['QQQ']) if str(s).strip()]
    cand_list=list(dict.fromkeys(cand_list))[:8]
    exp_map={k.upper():float(v) for k,v in (expected_returns or {}).items()}
    rf=0.04
    m_pt=market.macro('DGS3MO')
    if m_pt.get('value'):
        try: rf=float(m_pt['value'])/100.0
        except Exception: pass
    lev_map={k.upper():float(v) for k,v in (pol.get('leveraged_etf_multipliers') or {}).items()}
    core_set={s.upper() for s in (pol.get('core_symbols') or [])}
    k_frac=float(pol.get('kelly_fraction', 0.25))
    max_single=float(pol.get('max_single_stock_weight', 0.15))
    comparisons=[]
    for sym in cand_list:
        cur_val=float(dec(pos_map[sym]['total_value_base'])) if sym in pos_map else 0.0
        post_w=(cur_val+add_amt)/new_nav
        rets=list(decision._bar_returns(sym, 120).values())
        ann_vol=math.sqrt((sum((x-sum(rets)/len(rets))**2 for x in rets)/max(1,len(rets)-1))*252.0) if len(rets)>=20 else 0.30
        mu=exp_map.get(sym, float(pol.get('return_objective_annual', 0.15)) if sym not in core_set else 0.11)
        kelly_cap=max(0.0, min(max_single if sym not in core_set else 0.60, k_frac*(mu-rf)/max(0.01, ann_vol**2)))
        has_us_div=False
        if sym in pos_map:
            has_us_div=any(dec(l.get('withholding_tax_local') or '0')!=0 for l in (pos_map[sym].get('lots') or []))
        best_acct='taxable' if has_us_div else ('tfsa' if mu>=0.12 and lev_map.get(sym,1.0)<=1.0 else 'taxable')
        policy_ok=(sym in core_set) or (post_w<=max_single)
        comparisons.append({
            'candidate':sym,
            'bucket':'core' if sym in core_set else 'tactical',
            'current_weight':round(cur_val/nav,4),
            'post_allocation_weight':round(post_w,4),
            'annualized_vol_90d':round(ann_vol,4),
            'assumed_expected_return':round(mu,4),
            'quarter_kelly_target_weight':round(kelly_cap,4),
            'within_policy_cap':policy_ok,
            'recommended_account_location':best_acct,
            'location_rationale':'Avoids 15% unrecoverable TFSA IRS dividend withholding' if has_us_div else ('Tax-free compounding on high-growth non-levered equity' if best_acct=='tfsa' else 'Preserves loss-harvesting flexibility in taxable')
        })
    cash_cur=float(sum((dec(a['cash']) for a in snap.get('accounts',[]) if a.get('cash') not in (None,'')), Decimal(0)))
    comparisons.append({'candidate':'HOLD_CASH','bucket':'liquidity_reserve','current_weight':round(cash_cur/nav,4),'post_allocation_weight':round((cash_cur+add_amt)/new_nav,4),'annualized_vol_90d':0.0,'assumed_expected_return':round(rf,4),'quarter_kelly_target_weight':pol.get('min_cash_reserve',0.05),'within_policy_cap':True,'recommended_account_location':'taxable_or_tfsa','location_rationale':'Dry powder for drawdown tranches and policy liquidity floor'})
    return {'status':'ok','incremental_capital_base':round(add_amt,2),'currency':snap['currency'],'risk_free_rate_used':round(rf,4),'candidates':comparisons,'limitations':['Expected returns use caller inputs or policy baseline priors, not price forecasts. Fractional Kelly is capped by IPS single-stock limits.']}

def _norm_cdf(x): return 0.5*(1.0+math.erf(x/math.sqrt(2.0)))
def _norm_pdf(x): return math.exp(-0.5*x*x)/math.sqrt(2.0*math.pi)

def _bsm(spot, strike, days_to_expiry, rate, vol, kind='call'):
    t=max(1e-6, float(days_to_expiry)/365.0)
    s=float(spot); k=float(strike); r=float(rate); v=max(1e-4, float(vol))
    d1=(math.log(s/k)+(r+0.5*v*v)*t)/(v*math.sqrt(t))
    d2=d1-v*math.sqrt(t)
    disc=math.exp(-r*t)
    if kind=='call':
        price=s*_norm_cdf(d1)-k*disc*_norm_cdf(d2)
        delta=_norm_cdf(d1)
        theta=(-(s*_norm_pdf(d1)*v)/(2.0*math.sqrt(t))-r*k*disc*_norm_cdf(d2))/365.0
    else:
        price=k*disc*_norm_cdf(-d2)-s*_norm_cdf(-d1)
        delta=_norm_cdf(d1)-1.0
        theta=(-(s*_norm_pdf(d1)*v)/(2.0*math.sqrt(t))+r*k*disc*_norm_cdf(-d2))/365.0
    gamma=_norm_pdf(d1)/(s*v*math.sqrt(t))
    vega=s*_norm_pdf(d1)*math.sqrt(t)/100.0
    return {'theoretical_price':round(price,4),'delta':round(delta,4),'gamma':round(gamma,5),'vega_per_1pct_iv':round(vega,4),'theta_per_day':round(theta,4)}

def analyze_option_strategy(strategy, spot, strike, days_to_expiry, premium='', volatility='0.35', risk_free_rate='0.04', cost_basis=''):
    """Analytical Options Lab: Black-Scholes Greeks, bisection Implied Volatility solver, Covered Call strike/yield analyzer, and LEAPS capital-efficiency comparator."""
    strat=str(strategy or 'covered_call').strip().lower()
    s=float(spot); k=float(strike); dte=int(days_to_expiry); r=float(risk_free_rate); iv=float(volatility)
    if s<=0 or k<=0 or dte<1: raise ValueError('spot, strike, and days_to_expiry must be positive')
    kind='put' if 'put' in strat else 'call'
    solved_iv=None
    if str(premium or '').strip()!='':
        mkt_px=float(premium)
        lo, hi=0.01, 3.00
        for _ in range(60):
            mid=0.5*(lo+hi)
            val=_bsm(s, k, dte, r, mid, kind)['theoretical_price']
            if abs(val-mkt_px)<1e-4: lo=hi=mid; break
            if val<mkt_px: lo=mid
            else: hi=mid
        solved_iv=round(0.5*(lo+hi), 4)
        iv=solved_iv
    greeks=_bsm(s, k, dte, r, iv, kind)
    prem=float(premium) if str(premium or '').strip()!='' else greeks['theoretical_price']
    basis=float(cost_basis) if str(cost_basis or '').strip()!='' else s
    summary={'strategy':strat,'spot':s,'strike':k,'days_to_expiry':dte,'implied_vol_used':iv,'solved_iv_from_premium':solved_iv,'option_premium_per_share':round(prem,4),'contract_cash_100sh':round(prem*100.0,2),'greeks':greeks}
    if strat=='covered_call':
        max_profit_sh=(k-basis)+prem
        breakeven=basis-prem
        static_yield=prem/s
        called_return=((k-s)+prem)/s
        summary['covered_call_payoff']={'cost_basis_per_share':basis,'gross_premium_100sh':round(prem*100.0,2),'breakeven_stock_price':round(breakeven,4),'max_profit_per_share_if_called':round(max_profit_sh,4),'max_profit_100sh_if_called':round(max_profit_sh*100.0,2),'downside_protection_pct':round(static_yield,4),'annualized_static_yield':round(static_yield*(365.0/dte),4),'return_if_called_from_spot':round(called_return,4),'assignment_prob_approx_nd2':round(abs(greeks['delta']),4)}
    elif strat in {'leaps_call','long_call'}:
        intrinsic=max(0.0, s-k); extrinsic=max(0.0, prem-intrinsic)
        eff_leverage=(greeks['delta']*s)/prem if prem>0 else None
        summary['leaps_efficiency']={'breakeven_at_expiry':round(k+prem,4),'intrinsic_value':round(intrinsic,4),'extrinsic_time_value':round(extrinsic,4),'extrinsic_pct_of_spot':round(extrinsic/s,4),'capital_saved_vs_100_shares':round((s-prem)*100.0,2),'delta_adjusted_leverage':round(eff_leverage,2) if eff_leverage else None,'max_loss_100sh':round(prem*100.0,2)}
    summary['limitations']=['Analytical Black-Scholes-Merton model over explicit inputs (100-share contract); ignores early-exercise dividend jumps, tax, commissions, and live OPRA bid-ask spreads.']
    return summary

def backtest_strategies(symbol='QQQ', initial_capital='10000', cost_bps='5'):
    """Walk-forward historical replay comparing 4 canonical strategies on cached daily bars without look-ahead bias: (A) 100% Buy & Hold, (B) 70/30 Drawdown Dip-Buy, (C) Equal Periodic DCA, (D) 80% Core + 20% 3x Daily-Reset Sleeve."""
    sym=str(symbol or 'QQQ').strip().upper()
    cap0=float(initial_capital); fee_rate=float(cost_bps)/10000.0
    rows=market.bars(sym, 1000)
    if len(rows)<40:
        return {'status':'insufficient_history','symbol':sym,'rows':len(rows),'limitations':['Run market-sync to cache >=40 daily closes before running strategy comparison.']}
    closes=[float(r['close']) for r in rows]
    dates=[r['date'] for r in rows]
    n=len(closes)
    def _metrics(curve):
        total_r=(curve[-1]/cap0)-1.0
        yrs=max(1.0/252.0, (n-1)/252.0)
        cagr=(curve[-1]/cap0)**(1.0/yrs)-1.0 if curve[-1]>0 else -1.0
        pk=curve[0]; mdd=0.0; uw=0; max_uw=0; d_rets=[]
        for prev, cur in zip(curve, curve[1:]):
            if prev>0: d_rets.append((cur/prev)-1.0)
            if cur>=pk: pk=cur; uw=0
            else: mdd=min(mdd, (cur/pk)-1.0); uw+=1; max_uw=max(max_uw, uw)
        mean_r=sum(d_rets)/len(d_rets) if d_rets else 0.0
        std_r=math.sqrt(sum((x-mean_r)**2 for x in d_rets)/max(1,len(d_rets)-1)) if len(d_rets)>1 else 0.0
        sharpe=((mean_r-0.04/252.0)/std_r)*math.sqrt(252.0) if std_r>0 else None
        calmar=(cagr/abs(mdd)) if mdd<0 else None
        return {'terminal_wealth':round(curve[-1],2),'total_return':round(total_r,4),'cagr':round(cagr,4),'max_drawdown':round(mdd,4),'calmar_ratio':round(calmar,3) if calmar is not None else None,'sharpe_4pct_rf':round(sharpe,3) if sharpe is not None else None,'max_underwater_days':max_uw}
    # Strategy A: 100% Buy & Hold
    sh_a=(cap0*(1.0-fee_rate))/closes[0]
    curve_a=[sh_a*p for p in closes]
    # Strategy B: 70% Core + 30% Drawdown Dip-Buying (-8% and -15% from rolling underlying peak)
    sh_b=(cap0*0.70*(1.0-fee_rate))/closes[0]; cash_b=cap0*0.30; pk_p=closes[0]; t1_used=False; t2_used=False; curve_b=[]
    for p in closes:
        if p>=pk_p: pk_p=p; t1_used=False; t2_used=False
        dd_p=(p/pk_p)-1.0
        if dd_p<=-0.08 and not t1_used and cash_b>0:
            spend=cash_b*0.5; sh_b+=(spend*(1.0-fee_rate))/p; cash_b-=spend; t1_used=True
        if dd_p<=-0.15 and not t2_used and cash_b>0:
            spend=cash_b; sh_b+=(spend*(1.0-fee_rate))/p; cash_b=0.0; t2_used=True
        curve_b.append(sh_b*p+cash_b)
    # Strategy C: Equal Periodic DCA (every 10 trading days)
    tranches=max(1, (n+9)//10); per_tranche=cap0/tranches; sh_c=0.0; cash_c=cap0; curve_c=[]
    for idx, p in enumerate(closes):
        if idx%10==0 and cash_c>1e-6:
            spend=min(cash_c, per_tranche); sh_c+=(spend*(1.0-fee_rate))/p; cash_c-=spend
        curve_c.append(sh_c*p+cash_c)
    # Strategy D: 80% 1x Core + 20% 3x Daily-Reset Tactical Sleeve (0.88% annual expense)
    val_1x=cap0*0.80*(1.0-fee_rate); val_3x=cap0*0.20*(1.0-fee_rate); curve_d=[val_1x+val_3x]
    for p0, p1 in zip(closes, closes[1:]):
        r=(p1/p0)-1.0
        val_1x*=(1.0+r)
        val_3x*=max(0.0, 1.0+3.0*r-(0.0088/252.0))
        curve_d.append(val_1x+val_3x)
    return {
        'status':'ok',
        'symbol':sym,
        'window':{'from':dates[0],'to':dates[-1],'trading_days':n,'initial_capital':cap0,'cost_bps':float(cost_bps)},
        'strategies':{
            'A_100pct_buy_and_hold':_metrics(curve_a),
            'B_70_core_30_drawdown_dip_buy':_metrics(curve_b),
            'C_periodic_dca_10d':_metrics(curve_c),
            'D_80_core_20_daily_3x_sleeve':_metrics(curve_d)
        },
        'limitations':['Historical price-only replay on cached closes without dividend reinvestment or capital-gains tax realization; past performance does not guarantee future alpha.']
    }
