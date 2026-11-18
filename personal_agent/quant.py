"""Personal Investment OS Engines: Unified Policy Mandate, Economic Exposure & Tail Risk, Driver Valuation, Versioned Theses, Append-Only Expectations, Multi-Asset Capital Optimization, Options Lab, and Event-Driven Backtesting."""
from datetime import date
from decimal import Decimal
import hashlib
import json, math
from pathlib import Path
import numpy as np
from .core import ROOT, bounded, db, now
from .finance import AccountRecord, PortfolioSnapshot, PositionRecord, canonical_snapshot, dec, load_full_portfolio_snapshot, performance, portfolio
from . import decision, market

def _quant_schema(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS living_theses(
        symbol TEXT NOT NULL,
        version INTEGER NOT NULL,
        status TEXT NOT NULL,
        pillars_json TEXT NOT NULL,
        kpis_json TEXT NOT NULL,
        catalysts_json TEXT NOT NULL,
        breakers_json TEXT NOT NULL,
        fair_value_json TEXT NOT NULL,
        evidence_ids_json TEXT NOT NULL DEFAULT '[]',
        effective_at TEXT NOT NULL DEFAULT '',
        previous_version INTEGER,
        change_reason TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL,
        PRIMARY KEY(symbol, version)
    );
    CREATE TABLE IF NOT EXISTS earnings_expectations(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        period TEXT NOT NULL,
        entry_type TEXT NOT NULL DEFAULT 'pre_earnings',
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
        note TEXT NOT NULL DEFAULT ''
    );
    ''')
    # Migrate legacy single-PK living_theses if present
    lt_sql=c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='living_theses'").fetchone()
    lt_cols={r[1] for r in c.execute('PRAGMA table_info(living_theses)')}
    if lt_sql and 'PRIMARY KEY(symbol, version)' not in (lt_sql[0] or '') and 'symbol TEXT PRIMARY KEY' in (lt_sql[0] or ''):
        c.executescript('''
        ALTER TABLE living_theses RENAME TO _living_theses_legacy;
        CREATE TABLE living_theses(
            symbol TEXT NOT NULL,
            version INTEGER NOT NULL,
            status TEXT NOT NULL,
            pillars_json TEXT NOT NULL,
            kpis_json TEXT NOT NULL,
            catalysts_json TEXT NOT NULL,
            breakers_json TEXT NOT NULL,
            fair_value_json TEXT NOT NULL,
            evidence_ids_json TEXT NOT NULL DEFAULT '[]',
            effective_at TEXT NOT NULL DEFAULT '',
            previous_version INTEGER,
            change_reason TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY(symbol, version)
        );
        INSERT INTO living_theses(symbol,version,status,pillars_json,kpis_json,catalysts_json,breakers_json,fair_value_json,evidence_ids_json,effective_at,previous_version,change_reason,updated_at)
        SELECT symbol,version,status,pillars_json,kpis_json,catalysts_json,breakers_json,fair_value_json,'[]',updated_at,CASE WHEN version>1 THEN version-1 ELSE NULL END,'migrated_from_v030',updated_at
        FROM _living_theses_legacy;
        DROP TABLE _living_theses_legacy;
        ''')
    elif 'evidence_ids_json' not in lt_cols:
        for col_def in ("evidence_ids_json TEXT NOT NULL DEFAULT '[]'", "effective_at TEXT NOT NULL DEFAULT ''", 'previous_version INTEGER', "change_reason TEXT NOT NULL DEFAULT ''"):
            cname=col_def.split()[0]
            if cname not in lt_cols: c.execute(f'ALTER TABLE living_theses ADD COLUMN {col_def}')
    # Migrate legacy PK(symbol, period) earnings_expectations to append-only
    ee_cols={r[1] for r in c.execute('PRAGMA table_info(earnings_expectations)')}
    if 'entry_type' not in ee_cols:
        c.executescript('''
        ALTER TABLE earnings_expectations RENAME TO _earnings_expectations_legacy;
        CREATE TABLE earnings_expectations(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            period TEXT NOT NULL,
            entry_type TEXT NOT NULL DEFAULT 'pre_earnings',
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
            note TEXT NOT NULL DEFAULT ''
        );
        INSERT INTO earnings_expectations(symbol,period,entry_type,basis,exp_revenue,exp_eps,exp_guidance_growth,implied_move_pct,act_revenue,act_eps,act_guidance_growth,recorded_pre_at,updated_post_at,note)
        SELECT symbol,period,'pre_earnings',basis,exp_revenue,exp_eps,exp_guidance_growth,implied_move_pct,act_revenue,act_eps,act_guidance_growth,recorded_pre_at,updated_post_at,'migrated_from_v030'
        FROM _earnings_expectations_legacy;
        DROP TABLE _earnings_expectations_legacy;
        ''')

def load_policy():
    local_p=ROOT/'config'/'investment_policy.json'
    example_p=ROOT/'config'/'investment_policy.example.json'
    target=local_p if local_p.exists() else example_p
    if target.exists():
        return json.loads(target.read_text(encoding='utf-8'))
    return {
        'return_objective_annual':0.15,'investment_horizon_years':10,'core_benchmark':'QQQ','equity_risk_premium':0.055,
        'core_symbols':['QQQ','SPY'],'core_min_weight':0.35,'tactical_max_weight':0.55,'min_cash_reserve':0.05,
        'max_drawdown_budget':-0.25,'max_economic_leverage':1.20,'max_gross_economic_leverage':1.35,
        'max_single_stock_weight':0.15,'max_correlated_cluster_weight':0.30,'rebalance_drift_band':0.05,
        'kelly_fraction':0.25,'robust_uncertainty_penalty':0.50,'risk_aversion_lambda':3.0,'approval_ttl_hours':48,
        'leveraged_etf_multipliers':{'TQQQ':3.0,'QLD':2.0,'SQQQ':-3.0}
    }

def policy_hash(pol=None):
    data=pol if pol is not None else load_policy()
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode('utf-8')).hexdigest()[:16]

def evaluate_policy(portfolio_snapshot=None, proposed_trade=None, market_snapshot=None, investment_policy=None, account=None, mode='PROPOSED_PLAN', risk_context=None):
    """Pure canonical IPS Policy Engine (mode='AUDIT'|'PROPOSED_PLAN') over immutable per-account, per-contract PortfolioSnapshot."""
    pol=investment_policy or load_policy()
    if portfolio_snapshot is None:
        csnap=load_full_portfolio_snapshot()
    elif isinstance(portfolio_snapshot, dict) and 'nav' not in portfolio_snapshot:
        return {'status':'blocked','reason':portfolio_snapshot.get('status','unavailable'),'hard_blocks':[{'rule':'ledger_unavailable','detail':portfolio_snapshot.get('status','unavailable')}],'breaches':[],'risk_coverage_complete':False}
    else:
        fallback_pos=None
        if isinstance(portfolio_snapshot, dict) and portfolio_snapshot.get('positions') is None:
            fallback_pos=(decision.tax_positions(account=account).get('positions') or [])
        csnap=canonical_snapshot(portfolio_snapshot, tax_positions_fallback=fallback_pos)
    if csnap.status in {'unavailable','date_mismatch','unmatched_transfers','truncated_snapshot','missing_accounts','not_configured'} or csnap.nav<=0:
        return {'status':'invalid_nav' if csnap.nav<=0 else csnap.status,'hard_blocks':[{'rule':'ledger_unavailable','detail':csnap.status}],'breaches':[],'risk_coverage_complete':False}
    perf=risk_context if (risk_context and 'twr' in risk_context) else performance()
    r_base=risk_context if (risk_context and 'max_drawdown' in risk_context) else decision.risk()
    hard_blocks=[]
    if proposed_trade:
        try:
            csnap=simulate_allocation(csnap, proposed_trades=[proposed_trade], investment_policy=pol, require_explicit_account=False)
        except ValueError as exc:
            msg=str(exc); rule=msg.split(':',1)[0]
            if rule in {'unknown_account','missing_account_buying_power','insufficient_account_buying_power','insufficient_account_position','oversell_rejected'}:
                r_norm='insufficient_account_position' if rule=='oversell_rejected' else rule
                hard_blocks.append({'rule':r_norm,'symbol':str(proposed_trade.get('symbol') or '').upper(),'account':str(proposed_trade.get('account') or ''),'detail':msg})
            else:
                hard_blocks.append({'rule':'invalid_proposed_trade','detail':msg})
    acct_rows=[a for a in csnap.accounts if not account or a.account==account]
    nav=float(sum((a.nav for a in acct_rows), Decimal(0))) if acct_rows else float(csnap.nav)
    post_cash=float(sum((a.cash for a in acct_rows if a.cash is not None), Decimal(0))) if acct_rows else float(csnap.cash)
    if nav<=0:
        return {'status':'invalid_nav','hard_blocks':[{'rule':'invalid_nav','detail':'Portfolio NAV <= 0'}],'breaches':[],'risk_coverage_complete':False}
    lev_map={k.upper():float(v) for k,v in (pol.get('leveraged_etf_multipliers') or {}).items()}
    core_set={s.upper() for s in (pol.get('core_symbols') or [])}
    max_single=float(pol.get('max_single_stock_weight', 0.15))
    max_cluster=float(pol.get('max_correlated_cluster_weight', 0.30))
    tactical_max=float(pol.get('tactical_max_weight', 0.55))
    core_min=float(pol.get('core_min_weight', 0.35))
    min_cash=float(pol.get('min_cash_reserve', 0.05))
    max_net_lev=float(pol.get('max_economic_leverage', 1.20))
    max_gross_lev=float(pol.get('max_gross_economic_leverage', 1.35))
    dd_budget=float(pol.get('max_drawdown_budget', -0.25))

    nom_eq=0.0; net_econ_eq=0.0; gross_econ_eq=0.0; opt_wt=0.0; opt_econ_wt=0.0; opt_unknown=False; core_wt=0.0; tac_wt=0.0
    breaches=[]; pos_econ=[]; sym_net_vals={}
    for rec in csnap.positions:
        if account and rec.account and rec.account!=account: continue
        sym=rec.symbol; val=float(rec.market_value_base); asset=rec.asset
        if abs(val)<1e-6: continue
        w=val/nav
        if asset=='OPT':
            opt_wt+=abs(w)
            if rec.delta is not None and rec.underlying_price is not None and rec.quantity!=0 and rec.fx_rate_to_base is not None:
                ew=(float(rec.quantity)*float(rec.multiplier)*float(rec.underlying_price)*float(rec.fx_rate_to_base)*float(rec.delta))/nav
                opt_econ_wt+=ew
                net_econ_eq+=ew
                gross_econ_eq+=abs(ew)
                tac_wt+=max(0.0, w)
                pos_econ.append({'symbol':sym,'account':rec.account,'asset':'OPT','nominal_weight':round(w,4),'quantity':float(rec.quantity),'delta':float(rec.delta),'fx_rate_to_base':float(rec.fx_rate_to_base),'economic_weight':round(ew,4),'bucket':'tactical_option'})
            else:
                opt_unknown=True
                hard_blocks.append({'rule':'option_delta_notional_unavailable','symbol':sym,'detail':'Option position lacks contract quantity, underlying_price, fx_rate_to_base, or delta; refusing silent premium-based exposure.'})
                pos_econ.append({'symbol':sym,'account':rec.account,'asset':'OPT','nominal_weight':round(w,4),'economic_weight':None,'bucket':'tactical_option'})
            continue
        mult=lev_map.get(sym, 1.0)
        ew=w*mult
        nom_eq+=w; net_econ_eq+=ew; gross_econ_eq+=abs(ew)
        sym_net_vals[sym]=sym_net_vals.get(sym, 0.0)+val
        if sym in core_set: core_wt+=w
        else: tac_wt+=w
        pos_econ.append({'symbol':sym,'account':rec.account,'asset':asset,'nominal_weight':round(w,4),'daily_leverage_mult':mult,'economic_weight':round(ew,4),'bucket':'core' if sym in core_set else 'tactical'})
    for sym, tot_v in sym_net_vals.items():
        if sym not in core_set and abs(tot_v/nav)>max_single:
            breaches.append({'rule':'max_single_stock_weight','symbol':sym,'actual':round(tot_v/nav,4),'limit':max_single})

    cash_wt=post_cash/nav
    if cash_wt<min_cash:
        breaches.append({'rule':'min_cash_reserve','actual':round(cash_wt,4),'limit':min_cash})
    if abs(net_econ_eq)>max_net_lev:
        b_lev={'rule':'max_economic_leverage','actual':round(net_econ_eq,4),'limit':max_net_lev}
        breaches.append(b_lev)
        if mode=='PROPOSED_PLAN' or proposed_trade: hard_blocks.append(b_lev)
    if gross_econ_eq>max_gross_lev:
        b_glev={'rule':'max_gross_economic_leverage','actual':round(gross_econ_eq,4),'limit':max_gross_lev}
        breaches.append(b_glev)
        if mode=='PROPOSED_PLAN' or proposed_trade: hard_blocks.append(b_glev)
    if core_wt<core_min:
        breaches.append({'rule':'core_allocation_below_target','actual':round(core_wt,4),'target_min':core_min})
    if tac_wt>tactical_max:
        breaches.append({'rule':'tactical_max_weight_exceeded','actual':round(tac_wt,4),'limit':tactical_max})

    cur_dd=float(r_base['max_drawdown']) if r_base.get('max_drawdown') is not None else 0.0
    dd_used=min(2.0, abs(cur_dd)/abs(dd_budget)) if dd_budget<0 else 0.0
    if cur_dd<dd_budget:
        breaches.append({'rule':'max_drawdown_budget_exceeded','actual':round(cur_dd,4),'limit':dd_budget})

    if not csnap.advice_allowed:
        hard_blocks.append({'rule':'stale_or_incomplete_ledger','detail':'Ledger snapshot is stale or incomplete; sync Flex before acting.'})

    status='compliant' if (not breaches and not hard_blocks) else ('blocked_policy_violation' if hard_blocks else 'policy_breaches_detected')
    return {
        'status':status,
        'snapshot_id':csnap.snapshot_id,
        'policy_hash':policy_hash(pol),
        'as_of':csnap.as_of,
        'risk_coverage_complete':not opt_unknown,
        'policy_targets':{
            'return_objective_annual':pol.get('return_objective_annual'),
            'core_benchmark':pol.get('core_benchmark'),
            'max_drawdown_budget':dd_budget,
            'max_economic_leverage':max_net_lev,
            'max_gross_economic_leverage':max_gross_lev,
            'max_single_stock_weight':max_single,
            'max_correlated_cluster_weight':max_cluster,
            'tactical_max_weight':tactical_max,
            'core_min_weight':core_min,
            'min_cash_reserve':min_cash
        },
        'exposure_summary':{
            'nominal_equity_weight':round(nom_eq,4),
            'economic_equity_exposure':round(net_econ_eq,4),
            'gross_economic_exposure':round(gross_econ_eq,4),
            'cash_weight':round(cash_wt,4),
            'options_nominal_weight':round(opt_wt,4),
            'options_delta_equiv_weight':None if opt_unknown else round(opt_econ_wt,4),
            'core_weight':round(core_wt,4),
            'tactical_weight':round(tac_wt,4)
        },
        'risk_budget':{'max_drawdown_realized':round(cur_dd,4),'drawdown_budget':dd_budget,'drawdown_budget_utilized_pct':round(dd_used*100.0,1),'portfolio_twr_approx':perf.get('twr')},
        'hard_blocks':hard_blocks,
        'breaches':breaches,
        'position_economic_exposures':pos_econ,
        'limitations':['Evaluated over canonical per-account, per-contract PortfolioSnapshot; option delta-notional requires quantity, multiplier, underlying_price, fx_rate_to_base, and delta.']
    }

def policy_compliance(account=None):
    """Audit live portfolio against the CIO Investment Policy Statement (IPS) via the canonical evaluate_policy engine."""
    return evaluate_policy(account=account)

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

def simulate_leveraged_etf(underlying_annual_return='0.12', underlying_annual_vol='0.25', leverage='3.0', years=3, expense_ratio='0.0088', benchmark='QQQ', financing_rate='0.045'):
    """Analytical variance-drain & financing decomposition + empirical daily-reset replay on cached benchmark bars (e.g. TQQQ 3x daily reset vs QQQ)."""
    mu=float(underlying_annual_return); sigma=float(underlying_annual_vol); lev=float(leverage); fee=float(expense_ratio); yrs=float(years); fin=float(financing_rate)
    var_drain=0.5*lev*(lev-1.0)*(sigma**2)
    fin_drag=max(0.0, abs(lev)-1.0)*fin
    cont_cagr=lev*mu-var_drain-fee-fin_drag
    emp=None
    b_rows=market.bars(str(benchmark or 'QQQ').upper(), 10000)
    if len(b_rows)>=30:
        u_lvl=1.0; l_lvl=1.0; l_pk=1.0; l_dd=0.0; u_pk=1.0; u_dd=0.0
        daily_drag=(fee+fin_drag)/252.0
        for p0, p1 in zip(b_rows, b_rows[1:]):
            r=(float(p1['close'])/float(p0['close']))-1.0
            u_lvl*=(1.0+r); u_pk=max(u_pk, u_lvl); u_dd=min(u_dd, u_lvl/u_pk-1.0)
            l_lvl*=max(0.0, 1.0+lev*r-daily_drag); l_pk=max(l_pk, l_lvl); l_dd=min(l_dd, l_lvl/l_pk-1.0)
        emp={'benchmark':benchmark.upper(),'trading_days':len(b_rows)-1,'from':b_rows[0]['date'],'to':b_rows[-1]['date'],'underlying_cum_return':round(u_lvl-1.0,4),'simulated_leveraged_cum_return':round(l_lvl-1.0,4),'realized_multiple_of_underlying':round((l_lvl-1.0)/(u_lvl-1.0),3) if abs(u_lvl-1.0)>1e-4 else None,'underlying_max_drawdown':round(u_dd,4),'simulated_leveraged_max_drawdown':round(l_dd,4)}
    return {
        'leverage_daily_multiple':lev,
        'analytical_continuous_approx':{'assumed_underlying_return':mu,'assumed_underlying_vol':sigma,'annual_volatility_variance_drain':round(var_drain,4),'expense_ratio':fee,'annual_financing_cost':round(fin_drag,4),'expected_compound_growth_rate':round(cont_cagr,4),'projected_wealth_multiple':round(math.exp(cont_cagr*yrs),4),'break_even_underlying_return_needed':round((var_drain+fee+fin_drag)/lev,4) if lev!=0 else None},
        'empirical_cached_bar_replay':emp,
        'limitations':['Daily-reset leveraged ETFs target Lx single-day return, not Lx multi-period return; compounding suffers from 0.5*L*(L-1)*sigma^2 variance drain plus (|L|-1)*financing_rate carry.']
    }

def driver_based_valuation(revenue_t0, rev_cagr_base, op_margin_target, tax_rate='0.21', capex_pct_rev='0.06', nwc_pct_delta_rev='0.02', wacc='0.10', terminal_growth='0.025', net_debt='0', shares='10', years=5, bull_rev_delta='0.04', bear_rev_delta='-0.05', bull_margin_delta='0.03', bear_margin_delta='-0.04', symbol='', da_pct_rev='0.03', op_margin_t0=None, sbc_pct_rev='0.0', annual_dilution_rate='0.0', terminal_roic=None, bear_prob='0.25', base_prob='0.50', bull_prob='0.25', rf_rate=None, beta=None, erp=None):
    """Institutional Driver-based Financial Forecast & 3-Scenario DCF: Revenue -> Margin Ramp -> NOPAT + D&A - CapEx - dNWC -> FCFF, SBC/Dilution, and ROIC-anchored Terminal Value."""
    rev0=float(revenue_t0); g_base=float(rev_cagr_base); m_base=float(op_margin_target); tax=float(tax_rate)
    capx=float(capex_pct_rev); da=float(da_pct_rev); nwc=float(nwc_pct_delta_rev); g_term=float(terminal_growth)
    sbc=float(sbc_pct_rev); dil=float(annual_dilution_rate)
    m0=float(op_margin_t0) if op_margin_t0 not in (None,'') else None
    t_roic=float(terminal_roic) if terminal_roic not in (None,'') else None
    if str(wacc).strip().lower()=='auto' and beta not in (None,''):
        rf=float(rf_rate if rf_rate not in (None,'') else 0.04)
        e_rp=float(erp if erp not in (None,'') else load_policy().get('equity_risk_premium', 0.055))
        disc=rf+float(beta)*e_rp
    else:
        disc=float(wacc)
    debt=float(net_debt); sh0=float(shares); n_yr=int(years)
    if rev0<=0 or sh0<=0 or disc<=g_term or n_yr<1 or n_yr>15:
        raise ValueError('positive revenue_t0/shares, 1<=years<=15, and wacc > terminal_growth required')
    p_bear=max(0.0, float(bear_prob)); p_base=max(0.0, float(base_prob)); p_bull=max(0.0, float(bull_prob))
    p_sum=p_bear+p_base+p_bull
    if p_sum<=0: raise ValueError('scenario probabilities must sum to positive value')
    p_bear, p_base, p_bull = p_bear/p_sum, p_base/p_sum, p_bull/p_sum
    scenarios={
        'bear':{'prob':p_bear,'rev_g':g_base+float(bear_rev_delta),'margin':max(0.01, m_base+float(bear_margin_delta)),'wacc':disc+0.005},
        'base':{'prob':p_base,'rev_g':g_base,'margin':m_base,'wacc':disc},
        'bull':{'prob':p_bull,'rev_g':g_base+float(bull_rev_delta),'margin':m_base+float(bull_margin_delta),'wacc':max(g_term+0.01, disc-0.005)}
    }
    results={}; expected_px=0.0; base_schedule=[]
    for name, sc in scenarios.items():
        rev=rev0; pv_cf=0.0; last_fcff=0.0; last_nopat=0.0; w_s=sc['wacc']
        m_start=m0 if m0 is not None else sc['margin']
        for yr in range(1, n_yr+1):
            g_yr=sc['rev_g']+(g_term-sc['rev_g'])*(yr-1)/n_yr
            m_yr=m_start+(sc['margin']-m_start)*(yr/n_yr)
            next_rev=rev*(1.0+g_yr)
            d_rev=next_rev-rev
            ebit=next_rev*m_yr
            nopat=ebit*(1.0-tax)
            da_amt=next_rev*da
            capex_amt=next_rev*capx
            dnwc_amt=d_rev*nwc
            sbc_amt=next_rev*sbc
            fcff=nopat+da_amt-capex_amt-dnwc_amt-sbc_amt
            pv_cf+=fcff/((1.0+w_s)**yr)
            if name=='base':
                base_schedule.append({'year':yr,'revenue':round(next_rev,2),'op_margin':round(m_yr,4),'nopat':round(nopat,2),'da':round(da_amt,2),'capex':round(capex_amt,2),'dnwc':round(dnwc_amt,2),'fcff':round(fcff,2)})
            rev=next_rev; last_fcff=fcff; last_nopat=nopat
        if t_roic is not None and t_roic>g_term:
            term_reinv=g_term/t_roic
            term_fcff=last_nopat*(1.0+g_term)*(1.0-term_reinv)
        else:
            term_reinv=None
            term_fcff=last_fcff*(1.0+g_term)
        tv=term_fcff/(w_s-g_term)
        pv_tv=tv/((1.0+w_s)**n_yr)
        ev=pv_cf+pv_tv
        eq=ev-debt
        diluted_sh=sh0*((1.0+dil)**n_yr)
        px=eq/diluted_sh
        expected_px+=sc['prob']*px
        results[name]={
            'probability':round(sc['prob'],4),'stage1_rev_growth':round(sc['rev_g'],4),'operating_margin_target':round(sc['margin'],4),
            'wacc':round(w_s,4),'year_n_revenue':round(rev,2),'year_n_fcff':round(last_fcff,2),
            'terminal_reinvestment_rate':round(term_reinv,4) if term_reinv is not None else None,
            'diluted_shares':round(diluted_sh,4),'enterprise_value':round(ev,2),'per_share':round(px,2)
        }
    out={
        'method':'driver_based_3scenario_dcf',
        'years':n_yr,
        'fcff_formula':'NOPAT + D&A - CapEx - dNWC - SBC',
        'probability_weighted_per_share':round(expected_px,2),
        'valuation_range_bear_to_bull':[results['bear']['per_share'], results['bull']['per_share']],
        'scenarios':results,
        'base_forecast_schedule':base_schedule,
        'price':'unavailable',
        'price_freshness':'unavailable',
        'upside':'unavailable'
    }
    if str(symbol or '').strip():
        out=decision.attach_market_price({**out,'per_share':str(round(expected_px,4))}, symbol, fetch_live=False)
    return out

def upsert_living_thesis(symbol, pillars, kpis, catalysts, breakers, bear_px=None, base_px=None, bull_px=None, status='active', evidence_ids=None, change_reason='', effective_at=None):
    """Append a new immutable version of a Living Thesis (PRIMARY KEY (symbol, version)) preserving full audit history."""
    sym=str(symbol or '').strip().upper()
    if not sym: raise ValueError('symbol required')
    eff=str(effective_at or now())
    ev_ids=evidence_ids if isinstance(evidence_ids, list) else ([s.strip() for s in str(evidence_ids or '').split(',') if s.strip()])
    with db('ops') as c:
        _quant_schema(c)
        old=c.execute('SELECT max(version) AS max_v FROM living_theses WHERE symbol=?',(sym,)).fetchone()
        prev_v=old['max_v'] if (old and old['max_v'] is not None) else None
        ver=(prev_v+1) if prev_v is not None else 1
        fv={'bear':bear_px,'base':base_px,'bull':bull_px}
        c.execute('''INSERT INTO living_theses
            (symbol,version,status,pillars_json,kpis_json,catalysts_json,breakers_json,fair_value_json,evidence_ids_json,effective_at,previous_version,change_reason,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
            sym, ver, status,
            json.dumps(pillars if isinstance(pillars,list) else [s.strip() for s in str(pillars).split(';') if s.strip()], ensure_ascii=False),
            json.dumps(kpis if isinstance(kpis,list) else [s.strip() for s in str(kpis).split(';') if s.strip()], ensure_ascii=False),
            json.dumps(catalysts if isinstance(catalysts,list) else [s.strip() for s in str(catalysts).split(';') if s.strip()], ensure_ascii=False),
            json.dumps(breakers if isinstance(breakers,list) else [s.strip() for s in str(breakers).split(';') if s.strip()], ensure_ascii=False),
            json.dumps(fv, ensure_ascii=False),
            json.dumps(ev_ids, ensure_ascii=False),
            eff, prev_v, str(change_reason or ''), now()
        ))
    return get_living_thesis(sym)

def _format_thesis_row(r):
    return {
        'symbol':r['symbol'],'version':r['version'],'previous_version':r['previous_version'],'status':r['status'],
        'pillars':json.loads(r['pillars_json']),'key_kpis':json.loads(r['kpis_json']),
        'catalysts':json.loads(r['catalysts_json']),'invalidation_breakers':json.loads(r['breakers_json']),
        'fair_value_range':json.loads(r['fair_value_json']),'evidence_ids':json.loads(r['evidence_ids_json'] or '[]'),
        'effective_at':r['effective_at'],'change_reason':r['change_reason'],'updated_at':r['updated_at']
    }

def get_living_thesis(symbol='', include_history=False):
    sym=str(symbol or '').strip().upper()
    with db('ops') as c:
        _quant_schema(c)
        if sym:
            rows=[dict(r) for r in c.execute('SELECT * FROM living_theses WHERE symbol=? ORDER BY version DESC',(sym,))]
            if not rows: return {'symbol':sym,'status':'not_recorded'}
            return [_format_thesis_row(r) for r in rows] if include_history else _format_thesis_row(rows[0])
        rows=[dict(r) for r in c.execute('''SELECT t.* FROM living_theses t
            INNER JOIN (SELECT symbol, MAX(version) AS max_v FROM living_theses GROUP BY symbol) m
            ON t.symbol=m.symbol AND t.version=m.max_v ORDER BY t.updated_at DESC LIMIT 30''')]
        return [_format_thesis_row(r) for r in rows]

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

def record_earnings_expectation(symbol, period, basis='management_guidance', exp_revenue=None, exp_eps=None, exp_guidance_growth=None, implied_move_pct=None, act_revenue=None, act_eps=None, act_guidance_growth=None, note=''):
    """Append-only Point-in-Time pre-earnings expectation snapshot & post-earnings actuals. Pre-earnings snapshots are locked against retroactive mutation."""
    sym=str(symbol or '').strip().upper(); per=str(period or '').strip().upper()
    if not sym or not per: raise ValueError('symbol and period (e.g. 2026Q4) required')
    if basis not in {'management_guidance','internal_model','licensed_consensus'}:
        raise ValueError('basis must be management_guidance, internal_model, or licensed_consensus')
    def _f(v): return None if v in (None,'') else float(v)
    er, ee, eg, im = _f(exp_revenue), _f(exp_eps), _f(exp_guidance_growth), _f(implied_move_pct)
    ar, ae, ag = _f(act_revenue), _f(act_eps), _f(act_guidance_growth)
    has_exp=any(x is not None for x in (er, ee, eg, im))
    has_act=any(x is not None for x in (ar, ae, ag))
    if not has_exp and not has_act: raise ValueError('at least one expectation or actual metric is required')
    stamp=now()
    with db('ops') as c:
        _quant_schema(c)
        existing=[dict(r) for r in c.execute('SELECT * FROM earnings_expectations WHERE symbol=? AND period=? ORDER BY id ASC',(sym,per))]
        pre_row=next((r for r in existing if r['entry_type']=='pre_earnings'), existing[0] if existing else None)
        if pre_row is None:
            c.execute('''INSERT INTO earnings_expectations
                (symbol,period,entry_type,basis,exp_revenue,exp_eps,exp_guidance_growth,implied_move_pct,act_revenue,act_eps,act_guidance_growth,recorded_pre_at,updated_post_at,note)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (sym,per,'pre_earnings',basis,er,ee,eg,im,ar,ae,ag,stamp,stamp if has_act else None,str(note or '')))
        else:
            if has_exp:
                raise ValueError(f'pre_earnings expectation snapshot for {sym} {per} is locked at {pre_row["recorded_pre_at"]} and cannot be retroactively modified')
            if has_act:
                c.execute('''INSERT INTO earnings_expectations
                    (symbol,period,entry_type,basis,exp_revenue,exp_eps,exp_guidance_growth,implied_move_pct,act_revenue,act_eps,act_guidance_growth,recorded_pre_at,updated_post_at,note)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (sym,per,'actuals',basis,pre_row['exp_revenue'],pre_row['exp_eps'],pre_row['exp_guidance_growth'],pre_row['implied_move_pct'],ar,ae,ag,pre_row['recorded_pre_at'],stamp,str(note or '')))
    return evaluate_expectation_gap(sym, per)

def evaluate_expectation_gap(symbol, period=''):
    sym=str(symbol or '').strip().upper(); per=str(period or '').strip().upper()
    with db('ops') as c:
        _quant_schema(c)
        if not per:
            latest=c.execute('SELECT period FROM earnings_expectations WHERE symbol=? ORDER BY id DESC LIMIT 1',(sym,)).fetchone()
            if not latest: return {'symbol':sym,'status':'no_pre_earnings_snapshot','limitations':['Record pre-earnings guidance or model expectations before earnings; consensus is never fabricated.']}
            per=latest['period']
        rows=[dict(r) for r in c.execute('SELECT * FROM earnings_expectations WHERE symbol=? AND period=? ORDER BY id ASC',(sym,per))]
    if not rows: return {'symbol':sym,'status':'no_pre_earnings_snapshot','limitations':['Record pre-earnings guidance or model expectations before earnings; consensus is never fabricated.']}
    pre=next((r for r in rows if r['entry_type']=='pre_earnings'), rows[0])
    merged_act={'act_revenue':pre.get('act_revenue'),'act_eps':pre.get('act_eps'),'act_guidance_growth':pre.get('act_guidance_growth'),'updated_post_at':pre.get('updated_post_at')}
    for r in rows:
        for k in ('act_revenue','act_eps','act_guidance_growth','updated_post_at'):
            if r.get(k) is not None: merged_act[k]=r[k]
    rev_surp=((merged_act['act_revenue']-pre['exp_revenue'])/abs(pre['exp_revenue'])) if (merged_act['act_revenue'] is not None and pre['exp_revenue']) else None
    eps_surp=((merged_act['act_eps']-pre['exp_eps'])/abs(pre['exp_eps'])) if (merged_act['act_eps'] is not None and pre['exp_eps']) else None
    guide_diff=(merged_act['act_guidance_growth']-pre['exp_guidance_growth']) if (merged_act['act_guidance_growth'] is not None and pre['exp_guidance_growth'] is not None) else None
    verdict='awaiting_actuals'
    if any(x is not None for x in (rev_surp, eps_surp, guide_diff)):
        if (guide_diff is not None and guide_diff<-0.01) or (rev_surp is not None and rev_surp<-0.02):
            verdict='negative_expectation_gap_review_thesis'
        elif (guide_diff is not None and guide_diff>0.01) or (rev_surp is not None and rev_surp>0.02 and (eps_surp is None or eps_surp>=0)):
            verdict='positive_beat_and_raise'
        else:
            verdict='inline_with_prior_expectations'
    snap_merged={**pre,**merged_act,'history_entries':len(rows)}
    return {'symbol':sym,'period':per,'basis':pre['basis'],'verdict':verdict,'revenue_surprise_pct':round(rev_surp,4) if rev_surp is not None else None,'eps_surprise_pct':round(eps_surp,4) if eps_surp is not None else None,'guidance_growth_delta':round(guide_diff,4) if guide_diff is not None else None,'snapshot':snap_merged}

def simulate_allocation(portfolio_snapshot, cash_contribution=0.0, proposed_trades=None, contribution_account=None, investment_policy=None, require_explicit_account=True):
    """Single canonical deterministic transaction reducer conserving account cash, buying power, signed quantities, and NAV."""
    csnap=canonical_snapshot(portfolio_snapshot)
    pol=investment_policy or load_policy()
    acct_constraints=pol.get('account_constraints') or {}
    contrib=dec(cash_contribution or 0)
    accts=[AccountRecord(account=a.account,tax_bucket=a.tax_bucket,nav=a.nav,cash=a.cash,allow_margin=a.allow_margin,source=a.source) for a in csnap.accounts]
    pos_list=list(csnap.positions)
    if contrib!=0 and accts:
        if contribution_account:
            idx=next((i for i,a in enumerate(accts) if a.account==contribution_account), None)
            if idx is None: raise ValueError(f'unknown_account:{contribution_account}')
        elif len(accts)==1:
            idx=0
        elif not require_explicit_account:
            idx=next((i for i,a in enumerate(accts) if a.allow_margin), 0)
        else:
            raise ValueError('explicit_account_required:specify contribution_account for multi-account cash contribution')
        a0=accts[idx]
        accts[idx]=AccountRecord(account=a0.account, tax_bucket=a0.tax_bucket, nav=a0.nav+contrib, cash=(a0.cash if a0.cash is not None else Decimal(0))+contrib, allow_margin=a0.allow_margin, source=a0.source)
    for tr in (proposed_trades or []):
        sym=str(tr.get('symbol') or '').strip().upper()
        cid=str(tr.get('conid') or '').strip()
        act=str(tr.get('action') or tr.get('side') or 'BUY').strip().upper()
        amt=abs(dec(tr.get('amount_base') or 0))
        fee=abs(dec(tr.get('estimated_fees_base') or 0))
        acct_t=str(tr.get('account') or tr.get('account_id') or '').strip()
        if not sym or amt<=0: continue
        delta_v=amt if act in {'BUY','ADD','COVER'} else -amt
        if acct_t:
            a_idx=next((i for i,a in enumerate(accts) if a.account==acct_t), None)
            if a_idx is None: raise ValueError(f'unknown_account:{acct_t}')
        elif len(accts)==1:
            a_idx=0; acct_t=accts[0].account
        elif not require_explicit_account and accts:
            a_idx=next((i for i,a in enumerate(accts) if a.allow_margin), 0); acct_t=accts[a_idx].account
        else:
            raise ValueError(f'explicit_account_required:specify account for trade {sym}')
        a_cur=accts[a_idx]
        acct_rule=acct_constraints.get(acct_t) or acct_constraints.get(acct_t.lower()) or acct_constraints.get(a_cur.tax_bucket) or {}
        allow_m=bool(acct_rule.get('allow_margin', a_cur.allow_margin))
        if delta_v>0 and not allow_m:
            if a_cur.cash is None:
                raise ValueError(f'missing_account_buying_power:{acct_t}')
            if (a_cur.cash-delta_v-fee)<Decimal('-0.000001'):
                raise ValueError(f'insufficient_account_buying_power:{acct_t} needs {delta_v+fee}, has {a_cur.cash}')
        new_cash=((a_cur.cash if a_cur.cash is not None else Decimal(0))-delta_v-fee) if a_cur.cash is not None or delta_v<0 else None
        accts[a_idx]=AccountRecord(account=a_cur.account, tax_bucket=a_cur.tax_bucket, nav=a_cur.nav-fee, cash=new_cash, allow_margin=allow_m, source=a_cur.source)
        if cid:
            p_idx=next((i for i,p in enumerate(pos_list) if p.conid==cid and p.account==acct_t), None)
        else:
            p_idx=next((i for i,p in enumerate(pos_list) if p.symbol==sym and p.account==acct_t), None)
        tr_qty=dec(tr['quantity']) if tr.get('quantity') not in (None,'') else None
        if p_idx is not None:
            p0=pos_list[p_idx]
            new_mv=p0.market_value_base+delta_v
            if p0.market_value_base>0 and new_mv<Decimal('-0.000001'):
                raise ValueError(f'insufficient_account_position:{sym} in {acct_t}')
            if tr_qty is not None:
                new_qty=p0.quantity+(tr_qty if delta_v>0 else -tr_qty)
            elif p0.market_value_base!=0 and p0.quantity!=0:
                new_qty=p0.quantity*(new_mv/p0.market_value_base)
            else:
                new_qty=p0.quantity
            pos_list[p_idx]=PositionRecord(account=p0.account, conid=p0.conid, symbol=p0.symbol, asset=p0.asset, currency=p0.currency, quantity=new_qty, multiplier=p0.multiplier, market_value_base=max(Decimal(0), new_mv) if p0.market_value_base>=0 else new_mv, underlying_price=p0.underlying_price, underlying_currency=p0.underlying_currency, fx_rate_to_base=p0.fx_rate_to_base, delta=p0.delta, source=p0.source)
        else:
            if delta_v<0: raise ValueError(f'insufficient_account_position:unheld {sym} in {acct_t}')
            t_asset=str(tr.get('asset') or 'STK').upper()
            px_u=dec(tr['price_per_unit']) if tr.get('price_per_unit') not in (None,'') else None
            est_qty=tr_qty if tr_qty is not None else ((amt/px_u) if (px_u and px_u>0) else Decimal(0))
            pos_list.append(PositionRecord(
                account=acct_t, conid=cid or sym, symbol=sym, asset=t_asset,
                currency=csnap.currency, quantity=est_qty,
                multiplier=dec(tr.get('multiplier') or (100 if t_asset=='OPT' else 1)),
                market_value_base=delta_v,
                underlying_price=dec(tr['underlying_price']) if tr.get('underlying_price') is not None else None,
                underlying_currency=str(tr.get('underlying_currency') or csnap.currency).upper(),
                fx_rate_to_base=dec(tr['fx_rate_to_base']) if tr.get('fx_rate_to_base') is not None else Decimal(1),
                delta=dec(tr['delta']) if tr.get('delta') is not None else None,
            ))
    tot_nav=sum((a.nav for a in accts), Decimal(0))
    tot_cash=sum((a.cash or Decimal(0) for a in accts), Decimal(0))
    sim_hash=hashlib.sha256(json.dumps({'base':csnap.snapshot_id,'contrib':str(contrib),'acct':contribution_account,'trades':proposed_trades or []}, sort_keys=True, default=str).encode('utf-8')).hexdigest()[:16]
    return PortfolioSnapshot(
        snapshot_id=sim_hash,
        as_of=csnap.as_of, status=csnap.status, stale=csnap.stale,
        advice_allowed=csnap.advice_allowed, positions_complete=csnap.positions_complete,
        currency=csnap.currency, nav=tot_nav, cash=tot_cash,
        accounts=tuple(accts), positions=tuple(pos_list), limitations=csnap.limitations
    )

def allocate_incremental_capital(amount_base, candidates=None, expected_returns=None, account_constraints=None, contribution_account=None):
    """Full-Portfolio Covariance-aware Multi-Asset Robust Optimizer over canonical PortfolioSnapshot with post-trade IPS plan gate."""
    add_amt=float(dec(amount_base))
    if add_amt<=0: raise ValueError('amount_base must be positive')
    pol=load_policy()
    from . import finance as _fin
    csnap=load_full_portfolio_snapshot() if portfolio is _fin.portfolio else canonical_snapshot(portfolio())
    if csnap.status in {'unavailable','date_mismatch','unmatched_transfers','truncated_snapshot','missing_accounts','not_configured'} or csnap.nav<=0:
        return {'status':'blocked','reason':csnap.status}
    if not csnap.advice_allowed:
        return {'status':'blocked_stale_ledger','reason':'Ledger snapshot is stale or incomplete; sync Flex before optimizing allocation.'}
    nav=float(csnap.nav); new_nav=nav+add_amt
    pos_vals_by_sym={}
    for p in csnap.positions:
        pos_vals_by_sym[p.symbol]=pos_vals_by_sym.get(p.symbol, 0.0)+float(p.market_value_base)
    tax=decision.tax_positions()
    tax_map={p['symbol'].upper():p for p in (tax.get('positions') or [])}
    cand_list=[str(s).strip().upper() for s in (candidates or list(pos_vals_by_sym.keys())[:4]+['QQQ']) if str(s).strip()]
    cand_list=list(dict.fromkeys(cand_list))[:8]
    all_syms=list(cand_list)+[p.symbol for p in csnap.positions if p.symbol not in cand_list and p.asset=='STK']
    all_syms=list(dict.fromkeys(all_syms))
    exp_map={k.upper():float(v) for k,v in (expected_returns or {}).items()}
    rf=0.04
    m_pt=market.macro('DGS3MO')
    if m_pt.get('value'):
        try: rf=float(m_pt['value'])/100.0
        except Exception: pass
    erp=float(pol.get('equity_risk_premium', 0.055))
    bench=str(pol.get('core_benchmark') or 'QQQ').upper()
    bench_rets=decision._bar_returns(bench, 120)
    lev_map={k.upper():float(v) for k,v in (pol.get('leveraged_etf_multipliers') or {}).items()}
    core_set={s.upper() for s in (pol.get('core_symbols') or [])}
    k_frac=float(pol.get('kelly_fraction', 0.25))
    max_single=float(pol.get('max_single_stock_weight', 0.15))
    tactical_max=float(pol.get('tactical_max_weight', 0.55))
    kappa=float(pol.get('robust_uncertainty_penalty', 0.50))
    lam=float(pol.get('risk_aversion_lambda', 3.0))
    min_cash=float(pol.get('min_cash_reserve', 0.05))
    cash_cur=float(csnap.cash)

    # Build full-portfolio covariance matrix & CAPM/ERP priors across candidates + all existing holdings
    ret_series={sym:decision._bar_returns(sym, 120) for sym in all_syms}
    m_cand=len(cand_list); m_all=len(all_syms)
    cov_raw=np.zeros((m_all, m_all), dtype=float)
    vols={}; betas={}; mus={}; mu_robust={}; mu_sources={}
    for i, sym in enumerate(all_syms):
        r_map=ret_series[sym]
        r_vals=list(r_map.values())
        t_obs=len(r_vals)
        ann_vol=math.sqrt((sum((x-sum(r_vals)/t_obs)**2 for x in r_vals)/max(1,t_obs-1))*252.0) if t_obs>=20 else 0.30
        vols[sym]=ann_vol
        beta_val=1.0
        if len(bench_rets)>=20 and t_obs>=20:
            common=sorted(set(r_map.keys()) & set(bench_rets.keys()))
            if len(common)>=20:
                xs=[bench_rets[d] for d in common]; ys=[r_map[d] for d in common]
                mx=sum(xs)/len(xs); my=sum(ys)/len(ys)
                var_x=sum((x-mx)**2 for x in xs)
                if var_x>1e-12:
                    beta_val=max(0.2, min(2.5, sum((x-mx)*(y-my) for x,y in zip(xs,ys))/var_x))
        betas[sym]=beta_val
        if sym in exp_map:
            mu=exp_map[sym]; mu_sources[sym]='caller_input'
        else:
            mu=rf+beta_val*erp; mu_sources[sym]='capm_erp_prior'
        mus[sym]=mu
        unc_pen=kappa*(ann_vol/math.sqrt(max(20, t_obs)))
        mu_robust[sym]=mu-unc_pen
        cov_raw[i,i]=ann_vol**2
        for j in range(i):
            sym_j=all_syms[j]
            rho, _n=decision._pairwise_corr(r_map, ret_series[sym_j])
            cov_ij=(rho if rho is not None else 0.35)*ann_vol*vols[sym_j]
            cov_raw[i,j]=cov_ij; cov_raw[j,i]=cov_ij

    # Project covariance matrix onto Positive Semidefinite (PSD) cone
    if m_all>0:
        eigvals, eigvecs=np.linalg.eigh(cov_raw)
        cov_all=eigvecs@np.diag(np.maximum(eigvals, 1e-6))@eigvecs.T
    else:
        cov_all=cov_raw

    # Optimize incremental allocation weights delta_w over candidates (in full-portfolio space)
    cur_vals_all=np.array([pos_vals_by_sym.get(s, 0.0) for s in all_syms], dtype=float)
    w_cur_all=cur_vals_all/new_nav
    add_share=add_amt/new_nav
    kelly_caps={s:max(0.0, min(max_single if s not in core_set else (1.0-min_cash), k_frac*(mus[s]-rf)/max(0.01, vols[s]**2))) for s in cand_list}
    ub_total=np.array([min(1.0-min_cash, kelly_caps[s]) if s in core_set else min(max_single, kelly_caps[s]) for s in cand_list], dtype=float)
    ub_delta=np.maximum(0.0, (ub_total-w_cur_all[:m_cand])/max(1e-9, add_share))
    max_deploy_frac=max(0.0, min(1.0, (cash_cur+add_amt-min_cash*new_nav)/add_amt))
    if account_constraints and account_constraints.get('max_deploy_base') is not None:
        max_deploy_frac=min(max_deploy_frac, max(0.0, float(account_constraints['max_deploy_base'])/add_amt))
    cur_tac_w=sum(float(w_cur_all[i]) for i,s in enumerate(all_syms) if s not in core_set)
    max_tac_add=max(0.0, (tactical_max-cur_tac_w)/max(1e-9, add_share))
    mu_vec_all=np.array([mu_robust[s]-rf for s in all_syms], dtype=float)

    x=np.zeros(m_cand, dtype=float)
    for step in range(80):
        w_post_all=w_cur_all.copy()
        w_post_all[:m_cand]+=x*add_share
        grad_all=add_share*(mu_vec_all-lam*(cov_all@w_post_all))
        grad=grad_all[:m_cand]
        s_star=np.zeros(m_cand, dtype=float)
        rem=max_deploy_frac; rem_tac=max_tac_add
        for idx in np.argsort(-grad):
            if grad[idx]<=0 or rem<=1e-9: break
            cap_i=ub_delta[idx]
            if cand_list[idx] not in core_set:
                cap_i=min(cap_i, rem_tac)
            alloc=min(cap_i, rem)
            if alloc>1e-9:
                s_star[idx]=alloc; rem-=alloc
                if cand_list[idx] not in core_set: rem_tac-=alloc
        gamma=2.0/(step+2.0)
        x=(1.0-gamma)*x+gamma*s_star

    opt_alloc=[]
    deployed_amt=0.0
    for i, sym in enumerate(cand_list):
        frac=float(x[i])
        if frac<0.01: continue
        amt_i=round(frac*add_amt, 2)
        deployed_amt+=amt_i
        opt_alloc.append({'symbol':sym,'allocation_pct':round(frac*100.0,2),'amount_base':amt_i,'post_weight_of_nav':round(float(w_cur_all[i]+frac*add_share),4)})
    hold_cash_amt=round(max(0.0, add_amt-deployed_amt), 2)
    w_opt_post_all=w_cur_all.copy()
    w_opt_post_all[:m_cand]+=(x*add_share)
    port_vol_post=float(math.sqrt(max(0.0, float(w_opt_post_all.T@cov_all@w_opt_post_all)))) if m_all>0 else 0.0
    port_ret_post=float(np.dot(w_opt_post_all, np.array([mus[s] for s in all_syms])))+(max(0.0, (cash_cur+hold_cash_amt)/new_nav))*rf
    post_snapshot=simulate_allocation(csnap, cash_contribution=add_amt, proposed_trades=[{'symbol':a['symbol'],'action':'BUY','amount_base':str(a['amount_base']),'account':contribution_account or ''} for a in opt_alloc], contribution_account=contribution_account, investment_policy=pol, require_explicit_account=False)
    pol_check=evaluate_policy(portfolio_snapshot=post_snapshot, investment_policy=pol, mode='PROPOSED_PLAN')
    overall_status='blocked_policy_violation' if pol_check.get('hard_blocks') else 'ok'

    comparisons=[]
    for i, sym in enumerate(cand_list):
        cur_val=float(cur_vals_all[i])
        post_w=(cur_val+add_amt)/new_nav
        ann_vol=vols[sym]; mu=mus[sym]
        kelly_cap=kelly_caps[sym]
        has_us_div=False
        if sym in tax_map:
            has_us_div=any(dec(l.get('withholding_tax_local') or '0')!=0 for l in (tax_map[sym].get('lots') or []))
        best_acct='taxable' if has_us_div else ('tfsa' if mu>=0.12 and lev_map.get(sym,1.0)<=1.0 else 'taxable')
        policy_ok=(sym in core_set) or (post_w<=max_single)
        mctr_post=float((cov_all@w_opt_post_all)[i]/max(1e-6, port_vol_post)) if port_vol_post>0 else ann_vol
        comparisons.append({
            'candidate':sym,
            'bucket':'core' if sym in core_set else 'tactical',
            'current_weight':round(cur_val/nav,4),
            'post_allocation_weight':round(post_w,4),
            'annualized_vol_90d':round(ann_vol,4),
            'empirical_beta':round(betas[sym],3),
            'assumed_expected_return':round(mu,4),
            'robust_expected_return':round(mu_robust[sym],4),
            'expected_return_source':mu_sources[sym],
            'marginal_risk_contribution':round(mctr_post,4),
            'quarter_kelly_target_weight':round(kelly_cap,4),
            'within_policy_cap':policy_ok,
            'recommended_account_location':best_acct,
            'location_rationale':'Avoids 15% unrecoverable TFSA IRS dividend withholding' if has_us_div else ('Tax-free compounding on high-growth non-levered equity' if best_acct=='tfsa' else 'Preserves loss-harvesting flexibility in taxable')
        })
    comparisons.append({'candidate':'HOLD_CASH','bucket':'liquidity_reserve','current_weight':round(cash_cur/nav,4),'post_allocation_weight':round((cash_cur+add_amt)/new_nav,4),'annualized_vol_90d':0.0,'assumed_expected_return':round(rf,4),'expected_return_source':'macro_rf','quarter_kelly_target_weight':min_cash,'within_policy_cap':True,'recommended_account_location':'taxable_or_tfsa','location_rationale':'Dry powder for drawdown tranches and policy liquidity floor'})
    return {
        'status':overall_status,
        'snapshot_id':csnap.snapshot_id,
        'post_snapshot_id':post_snapshot.snapshot_id,
        'incremental_capital_base':round(add_amt,2),
        'currency':csnap.currency,
        'risk_free_rate_used':round(rf,4),
        'equity_risk_premium_used':round(erp,4),
        'optimal_multi_asset_allocation':{
            'allocations':opt_alloc,
            'hold_cash_base':hold_cash_amt,
            'post_portfolio_expected_return':round(port_ret_post,4),
            'post_portfolio_annualized_vol':round(port_vol_post,4),
            'optimization_method':'full_portfolio_psd_covariance_kelly_ips_frank_wolfe',
            'post_allocation_policy_status':pol_check.get('status'),
            'post_allocation_policy_hard_blocks':pol_check.get('hard_blocks',[]),
            'post_allocation_policy_breaches':pol_check.get('breaches',[])
        },
        'candidates':comparisons,
        'limitations':['Unprovided expected returns use CAPM/ERP baseline (rf + beta * ERP) penalized by estimation uncertainty; optimization spans all held equities with PSD-projected covariance.']
    }

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
        nd2_itm=_norm_cdf(d2)
        theta=(-(s*_norm_pdf(d1)*v)/(2.0*math.sqrt(t))-r*k*disc*_norm_cdf(d2))/365.0
    else:
        price=k*disc*_norm_cdf(-d2)-s*_norm_cdf(-d1)
        delta=_norm_cdf(d1)-1.0
        nd2_itm=_norm_cdf(-d2)
        theta=(-(s*_norm_pdf(d1)*v)/(2.0*math.sqrt(t))+r*k*disc*_norm_cdf(-d2))/365.0
    gamma=_norm_pdf(d1)/(s*v*math.sqrt(t))
    vega=s*_norm_pdf(d1)*math.sqrt(t)/100.0
    return {'theoretical_price':round(price,4),'delta':round(delta,4),'nd2_itm_prob':round(nd2_itm,4),'d1':round(d1,4),'d2':round(d2,4),'gamma':round(gamma,5),'vega_per_1pct_iv':round(vega,4),'theta_per_day':round(theta,4)}

def analyze_option_strategy(strategy, spot, strike, days_to_expiry, premium='', volatility='0.35', risk_free_rate='0.04', cost_basis=''):
    """Analytical Options Lab: Black-Scholes Greeks, exact N(d2) ITM probability, no-arbitrage bounded IV solver, Covered Call & LEAPS analytics."""
    strat=str(strategy or 'covered_call').strip().lower()
    s=float(spot); k=float(strike); dte=int(days_to_expiry); r=float(risk_free_rate); iv=float(volatility)
    if s<=0 or k<=0 or dte<1: raise ValueError('spot, strike, and days_to_expiry must be positive')
    kind='put' if 'put' in strat else 'call'
    t=dte/365.0; disc=math.exp(-r*t)
    lb=max(0.0, s-k*disc) if kind=='call' else max(0.0, k*disc-s)
    ub=s if kind=='call' else k*disc
    solved_iv=None; iv_status='model_input_vol'
    if str(premium or '').strip()!='':
        mkt_px=float(premium)
        if mkt_px<lb-1e-4 or mkt_px>ub+1e-4:
            iv_status='outside_no_arbitrage_bounds'
        else:
            lo, hi=0.005, 5.00
            p_lo=_bsm(s, k, dte, r, lo, kind)['theoretical_price']
            p_hi=_bsm(s, k, dte, r, hi, kind)['theoretical_price']
            if mkt_px<p_lo-1e-4 or mkt_px>p_hi+1e-4:
                iv_status='out_of_solver_bracket'
            else:
                for _ in range(64):
                    mid=0.5*(lo+hi)
                    val=_bsm(s, k, dte, r, mid, kind)['theoretical_price']
                    if abs(val-mkt_px)<1e-5: lo=hi=mid; break
                    if val<mkt_px: lo=mid
                    else: hi=mid
                solved_iv=round(0.5*(lo+hi), 4)
                iv=solved_iv
                iv_status='solved'
    if iv_status in {'outside_no_arbitrage_bounds','out_of_solver_bracket'}:
        return {'strategy':strat,'spot':s,'strike':k,'days_to_expiry':dte,'implied_vol_used':None,'solved_iv_from_premium':None,'iv_solver_status':iv_status,'no_arbitrage_bounds':[round(lb,4), round(ub,4)],'option_premium_per_share':float(premium),'greeks':None,'limitations':['Option premium violates European BSM no-arbitrage bounds or solver range; refusing to emit trade payoff metrics.']}
    greeks=_bsm(s, k, dte, r, iv, kind)
    prem=float(premium) if str(premium or '').strip()!='' else greeks['theoretical_price']
    basis=float(cost_basis) if str(cost_basis or '').strip()!='' else s
    summary={'strategy':strat,'spot':s,'strike':k,'days_to_expiry':dte,'implied_vol_used':iv,'solved_iv_from_premium':solved_iv,'iv_solver_status':iv_status,'no_arbitrage_bounds':[round(lb,4), round(ub,4)],'option_premium_per_share':round(prem,4),'contract_cash_100sh':round(prem*100.0,2),'greeks':greeks}
    if strat=='covered_call':
        max_profit_sh=(k-basis)+prem
        breakeven=basis-prem
        static_yield=prem/s
        called_return=((k-s)+prem)/s
        summary['covered_call_payoff']={'cost_basis_per_share':basis,'gross_premium_100sh':round(prem*100.0,2),'breakeven_stock_price':round(breakeven,4),'max_profit_per_share_if_called':round(max_profit_sh,4),'max_profit_100sh_if_called':round(max_profit_sh*100.0,2),'downside_protection_pct':round(static_yield,4),'annualized_static_yield':round(static_yield*(365.0/dte),4),'return_if_called_from_spot':round(called_return,4),'risk_neutral_itm_prob_nd2':greeks['nd2_itm_prob']}
    elif strat in {'leaps_call','long_call'}:
        intrinsic=max(0.0, s-k); extrinsic=max(0.0, prem-intrinsic)
        eff_leverage=(greeks['delta']*s)/prem if prem>0 else None
        summary['leaps_efficiency']={'breakeven_at_expiry':round(k+prem,4),'intrinsic_value':round(intrinsic,4),'extrinsic_time_value':round(extrinsic,4),'extrinsic_pct_of_spot':round(extrinsic/s,4),'capital_saved_vs_100_shares':round((s-prem)*100.0,2),'delta_adjusted_leverage':round(eff_leverage,2) if eff_leverage else None,'max_loss_100sh':round(prem*100.0,2),'risk_neutral_itm_prob_nd2':greeks['nd2_itm_prob']}
    summary['limitations']=['European Black-Scholes-Merton over explicit inputs; N(d2) is risk-neutral expiry ITM probability, not American early-exercise probability. Live OPRA bid/ask/OI chain not yet connected.']
    return summary

def backtest_strategies(symbol='QQQ', initial_capital='10000', cost_bps='5', financing_rate='0.045'):
    """Event-driven walk-forward replay with zero look-ahead (signal at close[t-1], execute at open[t]), fixed-schedule DCA, 3x financing cost, and regime/OOS validation."""
    sym=str(symbol or 'QQQ').strip().upper()
    cap0=float(initial_capital); fee_rate=float(cost_bps)/10000.0; fin_rate=float(financing_rate)
    rows=market.bars(sym, 10000)
    if len(rows)<40:
        return {'status':'insufficient_history','symbol':sym,'rows':len(rows),'limitations':['Run market-sync to cache >=40 daily closes before running strategy comparison.']}
    closes=[float(r['close']) for r in rows]
    opens=[float(r.get('open') or r['close']) for r in rows]
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
    # Strategy A: 100% Buy & Hold at t=0 open
    sh_a=(cap0*(1.0-fee_rate))/opens[0]
    curve_a=[sh_a*p for p in closes]
    # Strategy B: 70% Core + 30% Drawdown Dip-Buying (signal on close[t-1], execute on open[t])
    sh_b=(cap0*0.70*(1.0-fee_rate))/opens[0]; cash_b=cap0*0.30; pk_p=closes[0]; t1_used=False; t2_used=False
    curve_b=[sh_b*closes[0]+cash_b]
    for i in range(1, n):
        prev_c=closes[i-1]; exec_p=opens[i]
        if prev_c>=pk_p: pk_p=prev_c; t1_used=False; t2_used=False
        dd_p=(prev_c/pk_p)-1.0
        if dd_p<=-0.08 and not t1_used and cash_b>0:
            spend=cash_b*0.5; sh_b+=(spend*(1.0-fee_rate))/exec_p; cash_b-=spend; t1_used=True
        if dd_p<=-0.15 and not t2_used and cash_b>0:
            spend=cash_b; sh_b+=(spend*(1.0-fee_rate))/exec_p; cash_b=0.0; t2_used=True
        curve_b.append(sh_b*closes[i]+cash_b)
    # Strategy C: Fixed-schedule Periodic DCA (5% of initial capital every 10 trading days, independent of total n)
    tranche_amt=cap0*0.05; sh_c=0.0; cash_c=cap0; curve_c=[]
    for idx in range(n):
        if idx%10==0 and cash_c>1e-6:
            spend=min(cash_c, tranche_amt); sh_c+=(spend*(1.0-fee_rate))/opens[idx]; cash_c-=spend
        curve_c.append(sh_c*closes[idx]+cash_c)
    # Strategy D: 80% 1x Core + 20% 3x Daily-Reset Tactical Sleeve (0.88% ER + 2x financing cost)
    val_1x=cap0*0.80*(1.0-fee_rate); val_3x=cap0*0.20*(1.0-fee_rate); curve_d=[val_1x+val_3x]
    daily_3x_drag=(0.0088+2.0*fin_rate)/252.0
    for p0, p1 in zip(closes, closes[1:]):
        r=(p1/p0)-1.0
        val_1x*=(1.0+r)
        val_3x*=max(0.0, 1.0+3.0*r-daily_3x_drag)
        curve_d.append(val_1x+val_3x)
    # Crisis regimes & 60/40 Out-of-Sample validation
    regimes={}
    for rname, s_d, e_d in [('dotcom_2000_2002','2000-01-01','2002-12-31'),('gfc_2008_2009','2008-01-01','2009-03-31'),('covid_2020','2020-02-19','2020-04-30'),('rate_hike_2022','2022-01-01','2022-12-31')]:
        idxs=[k for k,d in enumerate(dates) if s_d<=d<=e_d]
        if len(idxs)>=10:
            i0, i1 = idxs[0], idxs[-1]
            regimes[rname]={'from':dates[i0],'to':dates[i1],'A_return':round(curve_a[i1]/curve_a[i0]-1.0,4),'B_return':round(curve_b[i1]/curve_b[i0]-1.0,4),'D_return':round(curve_d[i1]/curve_d[i0]-1.0,4)}
    split_idx=int(n*0.60)
    oos=None
    if split_idx>=20 and (n-split_idx)>=20:
        oos={'in_sample_end':dates[split_idx-1],'oos_start':dates[split_idx],'oos_end':dates[-1],'A_oos_return':round(curve_a[-1]/curve_a[split_idx]-1.0,4),'B_oos_return':round(curve_b[-1]/curve_b[split_idx]-1.0,4),'D_oos_return':round(curve_d[-1]/curve_d[split_idx]-1.0,4)}
    adj_bases=sorted({r.get('adj_basis') or 'unadjusted_legacy' for r in rows})
    return {
        'status':'ok',
        'symbol':sym,
        'window':{'from':dates[0],'to':dates[-1],'trading_days':n,'initial_capital':cap0,'cost_bps':float(cost_bps),'financing_rate':fin_rate,'execution_basis':'next_bar_open','adjustment_basis':adj_bases},
        'strategies':{
            'A_100pct_buy_and_hold':_metrics(curve_a),
            'B_70_core_30_drawdown_dip_buy':_metrics(curve_b),
            'C_periodic_dca_10d':_metrics(curve_c),
            'D_80_core_20_daily_3x_sleeve':_metrics(curve_d)
        },
        'crisis_regimes':regimes,
        'out_of_sample_split':oos,
        'limitations':['Uses split- and dividend-adjusted cached bars where available; signals on close[t-1] execute at open[t]. Realized capital-gains taxes are excluded.']
    }

def etf_lookthrough_exposure(etf_constituents=None, portfolio_snapshot=None):
    """v0.4.1 R01: Decompose ETF holdings into underlying constituent exposures without double-counting direct + ETF holdings."""
    csnap=canonical_snapshot(portfolio_snapshot) if portfolio_snapshot is not None else load_full_portfolio_snapshot()
    if csnap.nav<=0: return {'status':'invalid_nav','exposures':{}}
    nav=float(csnap.nav)
    const_map={k.upper():{ck.upper():float(cv) for ck,cv in v.items()} for k,v in (etf_constituents or {}).items()}
    direct={}; indirect={}; unallocated={}
    for p in csnap.positions:
        if p.asset!='STK': continue
        w=float(p.market_value_base)/nav
        if p.symbol in const_map:
            covered=0.0
            for c_sym, c_wt in const_map[p.symbol].items():
                indirect[c_sym]=indirect.get(c_sym, 0.0)+w*c_wt
                covered+=c_wt
            if covered<0.999:
                unallocated[p.symbol]=unallocated.get(p.symbol, 0.0)+w*(1.0-covered)
        else:
            direct[p.symbol]=direct.get(p.symbol, 0.0)+w
    all_syms=sorted(set(direct.keys()) | set(indirect.keys()))
    combined=[{'symbol':s,'direct_weight':round(direct.get(s,0.0),4),'etf_lookthrough_weight':round(indirect.get(s,0.0),4),'total_lookthrough_weight':round(direct.get(s,0.0)+indirect.get(s,0.0),4)} for s in all_syms]
    combined.sort(key=lambda r:abs(r['total_lookthrough_weight']), reverse=True)
    return {'status':'ok','snapshot_id':csnap.snapshot_id,'lookthrough_exposures':combined,'unallocated_etf_weights':{k:round(v,4) for k,v in unallocated.items()}}

def analyze_option_chain(symbol, chain_rows, spot, risk_free_rate='0.04', min_oi=10, max_spread_pct=0.25):
    """v0.4.1 R03: Filter option chain for missing/crossed/stale/illiquid quotes and compute conservative bid/ask entry pricing."""
    s=float(spot); r=float(risk_free_rate)
    if s<=0: raise ValueError('positive spot required')
    eligible=[]; rejected=[]
    for row in (chain_rows or []):
        bid=row.get('bid'); ask=row.get('ask'); oi=int(row.get('open_interest') or 0)
        strike=float(row.get('strike') or 0); dte=int(row.get('days_to_expiry') or 0)
        right=str(row.get('right') or 'call').lower()
        if bid is None or ask is None or bid<=0 or ask<=0 or bid>ask or strike<=0 or dte<1:
            rejected.append({**row,'reject_reason':'missing_or_crossed_quote'}); continue
        if row.get('stale') or (row.get('quote_age_seconds') is not None and float(row['quote_age_seconds'])>300):
            rejected.append({**row,'reject_reason':'stale_quote'}); continue
        mid=0.5*(float(bid)+float(ask))
        spread_pct=(float(ask)-float(bid))/mid if mid>0 else 1.0
        if oi<min_oi or spread_pct>max_spread_pct:
            rejected.append({**row,'reject_reason':'illiquid_or_wide_spread','spread_pct':round(spread_pct,4)}); continue
        strat='covered_call' if right=='call' else 'long_put'
        iv_ask=analyze_option_strategy(strat, s, strike, dte, premium=str(ask), risk_free_rate=str(r))
        eligible.append({
            'symbol':str(symbol).upper(),'right':right,'strike':strike,'days_to_expiry':dte,
            'bid':float(bid),'ask':float(ask),'mid':round(mid,4),'open_interest':oi,'spread_pct':round(spread_pct,4),
            'conservative_sell_entry':float(bid),'conservative_buy_entry':float(ask),
            'iv_at_ask':iv_ask.get('solved_iv_from_premium'),'iv_status':iv_ask.get('iv_solver_status'),
            'greeks_at_ask':iv_ask.get('greeks')
        })
    return {'symbol':str(symbol).upper(),'spot':s,'eligible_contracts':eligible,'rejected_contracts':rejected}

def analyze_option_roll(spot, close_strike, close_dte, close_ask, open_strike, open_dte, open_bid, contracts=1, multiplier=100, risk_free_rate='0.04'):
    """v0.4.1 R03: Conservative option roll analysis (buy-to-close at ask, sell-to-open at bid)."""
    n=int(contracts); mult=float(multiplier)
    close_cost=float(close_ask)*n*mult
    open_credit=float(open_bid)*n*mult
    net_cashflow=open_credit-close_cost
    c_info=analyze_option_strategy('covered_call', spot, close_strike, close_dte, premium=str(close_ask), risk_free_rate=str(risk_free_rate))
    o_info=analyze_option_strategy('covered_call', spot, open_strike, open_dte, premium=str(open_bid), risk_free_rate=str(risk_free_rate))
    return {
        'contracts':n,'multiplier':mult,
        'close_debit_at_ask':round(close_cost,2),'open_credit_at_bid':round(open_credit,2),
        'net_roll_cashflow':round(net_cashflow,2),'is_net_credit':net_cashflow>0,
        'dte_change':int(open_dte)-int(close_dte),
        'delta_change':round((o_info['greeks']['delta']-c_info['greeks']['delta']),4) if (o_info.get('greeks') and c_info.get('greeks')) else None,
        'close_leg_iv':c_info.get('solved_iv_from_premium'),'open_leg_iv':o_info.get('solved_iv_from_premium')
    }

def select_valuation_method(company_profile):
    """v0.5.1 V02: Select sector-appropriate primary valuation method rather than applying a single DCF blindly."""
    prof=str(company_profile or 'mature_tech').strip().lower()
    mapping={
        'mature_tech':{'primary':'fcff_dcf_and_forward_multiples','secondary':'reverse_dcf'},
        'high_growth_software':{'primary':'driver_based_dcf','secondary':'ev_to_gross_profit_fade'},
        'semiconductor':{'primary':'cyclical_scenario_dcf','secondary':'normalized_midcycle_pe'},
        'early_stage_pre_profit':{'primary':'probability_weighted_milestones_and_runway','secondary':'cash_runway_dilution'},
        'financials':{'primary':'residual_income','secondary':'price_to_tangible_book_roe'},
        'diversified':{'primary':'sotp','secondary':'consolidated_dcf'},
    }
    return {'company_profile':prof,**(mapping.get(prof, mapping['mature_tech']))}

def residual_income_valuation(book_value_per_share, roe, cost_of_equity, payout_ratio='0.30', years=5, terminal_growth='0.03'):
    """v0.5.1 V02: Residual Income Model for banks/financials where FCFF is undefined."""
    bv=float(book_value_per_share); r_eq=float(roe); ke=float(cost_of_equity); pay=float(payout_ratio); gt=float(terminal_growth)
    for v in (bv, r_eq, ke, pay, gt):
        if v!=v or abs(v)==float('inf'): raise ValueError('finite inputs required')
    if bv<=0 or ke<=gt: raise ValueError('positive book_value_per_share and cost_of_equity > terminal_growth required')
    pv_ri=0.0; cur_bv=bv; last_ri=0.0
    for yr in range(1, int(years)+1):
        ni=cur_bv*r_eq
        ri=ni-ke*cur_bv
        pv_ri+=ri/((1.0+ke)**yr)
        cur_bv+=ni*(1.0-pay)
        last_ri=ri
    tv_ri=(last_ri*(1.0+gt))/(ke-gt)
    pv_tv=tv_ri/((1.0+ke)**int(years))
    fair_px=bv+pv_ri+pv_tv
    return {'method':'residual_income','book_value_per_share':bv,'fair_value_per_share':round(fair_px,2),'implied_pb':round(fair_px/bv,2)}

def sotp_valuation(segments, net_debt='0', shares='10'):
    """v0.5.1 V02: Sum-of-the-Parts (SOTP) valuation across heterogeneous business segments."""
    sh=float(shares); debt=float(net_debt)
    if sh<=0: raise ValueError('positive shares required')
    tot_ev=0.0; breakdown=[]
    for seg in (segments or []):
        ev_s=float(seg['ev'])
        if ev_s!=ev_s or abs(ev_s)==float('inf'): raise ValueError('finite segment EV required')
        tot_ev+=ev_s
        breakdown.append({'segment':seg['name'],'ev':round(ev_s,2),'method':seg.get('method','ebitda_multiple')})
    eq=tot_ev-debt
    return {'method':'sotp','total_ev':round(tot_ev,2),'equity_value':round(eq,2),'per_share':round(eq/sh,2),'segments':breakdown}

def simulate_cashflow_strategy(symbol='QQQ', initial_capital='10000', monthly_contribution='500', strategy='buy_and_hold', cost_bps='5', cash_yield='0.04', financing_rate='0.045'):
    """v0.7.0 Q01/Q02: Reproducible cash-flow-matched strategy simulator with scheduled contributions, idle cash yield, next-bar open execution, and BacktestSpec hash."""
    sym=str(symbol or 'QQQ').strip().upper()
    cap0=float(initial_capital); m_contrib=float(monthly_contribution); fee_r=float(cost_bps)/10000.0; c_y=float(cash_yield); fin_r=float(financing_rate)
    rows=market.bars(sym, 10000)
    if len(rows)<40: return {'status':'insufficient_history','symbol':sym,'rows':len(rows)}
    opens=[float(r.get('open') or r['close']) for r in rows]
    closes=[float(r['close']) for r in rows]
    dates=[r['date'] for r in rows]
    n=len(closes)
    data_hash=hashlib.sha256(json.dumps([(r['date'],r.get('open'),r['close']) for r in rows]).encode('utf-8')).hexdigest()[:16]
    spec={'symbol':sym,'initial_capital':cap0,'monthly_contribution':m_contrib,'strategy':strategy,'cost_bps':float(cost_bps),'cash_yield':c_y,'financing_rate':fin_r,'from':dates[0],'to':dates[-1],'rows':n,'data_hash':data_hash}
    spec_hash=hashlib.sha256(json.dumps(spec, sort_keys=True).encode('utf-8')).hexdigest()[:16]
    total_contrib=cap0
    sh=0.0; cash=cap0; lev_sleeve=0.0
    if strategy=='buy_and_hold':
        sh=(cash*(1.0-fee_r))/opens[0]; cash=0.0
    elif strategy=='70_30_dip_buy':
        sh=(cash*0.70*(1.0-fee_r))/opens[0]; cash*=0.30
    elif strategy=='80_20_3x_sleeve':
        sh=(cash*0.80*(1.0-fee_r))/opens[0]; lev_sleeve=cash*0.20*(1.0-fee_r); cash=0.0
    pk=closes[0]; t1=False; t2=False; curve=[cap0]
    daily_3x_drag=(0.0088+2.0*fin_r)/252.0
    for i in range(1, n):
        cash*=(1.0+c_y/252.0)
        if strategy=='80_20_3x_sleeve':
            r_day=(closes[i]/closes[i-1])-1.0
            lev_sleeve*=max(0.0, 1.0+3.0*r_day-daily_3x_drag)
        if i%21==0 and m_contrib>0:
            cash+=m_contrib; total_contrib+=m_contrib
            if strategy=='buy_and_hold':
                sh+=(cash*(1.0-fee_r))/opens[i]; cash=0.0
            elif strategy=='80_20_3x_sleeve':
                sh+=(cash*0.80*(1.0-fee_r))/opens[i]; lev_sleeve+=cash*0.20*(1.0-fee_r); cash=0.0
        if strategy=='70_30_dip_buy':
            if closes[i-1]>=pk: pk=closes[i-1]; t1=False; t2=False
            dd=(closes[i-1]/pk)-1.0
            if dd<=-0.08 and not t1 and cash>0:
                sp=cash*0.5; sh+=(sp*(1.0-fee_r))/opens[i]; cash-=sp; t1=True
            if dd<=-0.15 and not t2 and cash>0:
                sp=cash; sh+=(sp*(1.0-fee_r))/opens[i]; cash=0.0; t2=True
        curve.append(sh*closes[i]+cash+lev_sleeve)
    end_val=curve[-1]
    return {'status':'ok','backtest_spec_hash':spec_hash,'spec':spec,'total_contributed':round(total_contrib,2),'terminal_wealth':round(end_val,2),'net_profit':round(end_val-total_contrib,2),'multiple_on_contributed':round(end_val/total_contrib,3) if total_contrib>0 else None}
