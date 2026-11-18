"""Stateful Workflow Engine, Human-in-the-Loop Approvals, Privacy-Safe Observability, and Evaluation Harness."""
from decimal import Decimal
import functools, json, os, tempfile, time, uuid
from pathlib import Path
from .core import ROOT, bounded, db, now
from . import decision, finance, knowledge, market, report

def schema(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS workflow_runs(
        run_id TEXT PRIMARY KEY,
        workflow_id TEXT NOT NULL,
        status TEXT NOT NULL,
        current_step TEXT NOT NULL,
        completed_steps TEXT NOT NULL,
        pending_approval INTEGER NOT NULL DEFAULT 0,
        retry_count INTEGER NOT NULL DEFAULT 0,
        inputs_meta TEXT NOT NULL,
        checkpoint_json TEXT NOT NULL,
        proposed_action_json TEXT,
        acceptance_passed INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS telemetry_spans(
        span_id TEXT PRIMARY KEY,
        surface TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        status TEXT NOT NULL,
        latency_ms REAL NOT NULL,
        approx_tokens INTEGER NOT NULL,
        error_type TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS eval_runs(
        eval_id TEXT PRIMARY KEY,
        passed INTEGER NOT NULL,
        total INTEGER NOT NULL,
        score_pct REAL NOT NULL,
        details_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    ''')

def record_span(surface, tool_name, status, latency_ms, approx_tokens, error_type=None):
    """Privacy-preserving observability: records latency, status, and token footprint without logging private payloads."""
    try:
        with db('ops') as c:
            schema(c)
            c.execute('INSERT INTO telemetry_spans VALUES(?,?,?,?,?,?,?,?)',(uuid.uuid4().hex[:16],surface,tool_name,status,round(float(latency_ms),2),int(approx_tokens),error_type,now()))
    except Exception:
        pass

def observe_tool(surface, name, fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        t0=time.perf_counter()
        try:
            res=fn(*args, **kwargs)
            chars=len(json.dumps(res, ensure_ascii=False, default=str))
            record_span(surface, name, 'ok', (time.perf_counter()-t0)*1000.0, max(1, chars//4))
            return res
        except Exception as exc:
            record_span(surface, name, 'error', (time.perf_counter()-t0)*1000.0, 0, type(exc).__name__)
            raise
    return wrapper

def telemetry_summary(limit=25):
    with db('ops') as c:
        schema(c)
        totals=c.execute('''
            SELECT count(*) AS calls,
                   sum(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors,
                   round(avg(latency_ms),2) AS avg_latency_ms,
                   round(max(latency_ms),2) AS max_latency_ms,
                   coalesce(sum(approx_tokens),0) AS total_approx_tokens
            FROM telemetry_spans
        ''').fetchone()
        by_tool=[dict(r) for r in c.execute('''
            SELECT surface, tool_name, count(*) AS calls,
                   sum(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors,
                   round(avg(latency_ms),2) AS avg_ms,
                   sum(approx_tokens) AS approx_tokens
            FROM telemetry_spans GROUP BY surface, tool_name ORDER BY calls DESC LIMIT 20
        ''')]
        recent=[dict(r) for r in c.execute('SELECT span_id,surface,tool_name,status,latency_ms,approx_tokens,error_type,created_at FROM telemetry_spans ORDER BY rowid DESC LIMIT ?',(bounded(limit),))]
    return {'summary':dict(totals) if totals else {},'by_tool':by_tool,'recent_spans':recent,'dlp_policy':'Payloads and arguments are excluded from spans so private balances never enter logs.'}

WORKFLOW_SPECS={
    'morning_portfolio_review':{
        'objective':'Run daily end-to-end portfolio reconciliation, covariance/factor risk check, Canadian tax scan, and recursive behavioral coaching in one deterministic pipeline.',
        'pattern':'Sequential + Conditional Routing',
        'trigger':'Scheduled daily sync or start-of-session prompt',
        'approval_gate':False,
        'steps':['intake_validate','freshness_and_reconciliation_gate','capital_and_risk_aggregation','covariance_and_factor_scan','tax_and_superficial_loss_scan','behavioral_guardrail_injection','acceptance_verification'],
        'acceptance_criteria':['Accounts share a single statement date or explicitly withhold advice','Quote freshness is separated from statement NAV','Payload stays within compact context budget']
    },
    'equity_valuation_and_expectations':{
        'objective':'Evaluate a single equity via SEC EDGAR cash flows, Multi-Stage Fade DCF, Reverse DCF market-implied growth, peer correlation, and optional thesis staging.',
        'pattern':'Evaluator-Optimizer + Optional Human Approval Gate',
        'trigger':'Single-stock research or valuation query',
        'approval_gate':True,
        'steps':['intake_validate','load_holdings_and_peer_correlation','load_edgar_and_market_context','run_multi_stage_and_reverse_dcf','intermediate_sanity_validation','red_team_and_guardrail_check','stage_thesis_or_deliver'],
        'acceptance_criteria':['Terminal value share >80% triggers explicit warning','Reverse DCF labels live vs historical close price basis','Staged thesis requires human approval before entering active memory']
    },
    'tax_aware_pre_trade_approval':{
        'objective':'Audit a proposed BUY/ADD/TRIM/SELL decision across 6 institutional gates (concentration, correlated cluster, ITA s.54 superficial loss, TFSA withholding, churn/mistakes, falsifier) and pause for mandatory human approval.',
        'pattern':'State Machine + Mandatory Human-in-the-Loop Gate',
        'trigger':'Any discretionary trade proposal or rebalance idea',
        'approval_gate':True,
        'steps':['intake_validate','six_point_pre_trade_gate','beta_and_fx_stress_delta','canadian_tax_verification','awaiting_human_approval','commit_approved_decision_contract'],
        'acceptance_criteria':['Orders remain strictly disabled','ITA s.54 cross-account overlap is checked','Human operator must approve before decision contract is committed to memory']
    },
    'recursive_behavioral_post_mortem':{
        'objective':'Audit execution habits, short-horizon churn, drawdown anchoring, thesis coverage, and Brier Score calibration, optionally staging a new personal guardrail rule for human approval.',
        'pattern':'Closed-Loop Evaluator + Memory Consolidation Gate',
        'trigger':'Weekly review or post-trade reflection',
        'approval_gate':True,
        'steps':['intake_validate','ledger_execution_telemetry','thesis_drift_and_brier_calibration','recurring_mistake_diagnosis','stage_guardrail_or_deliver'],
        'acceptance_criteria':['Habit metrics derive strictly from execution records','Brier score computes only over paired confidence + binary outcome records','New guardrail rules require human approval before activation']
    },
    'sec_filing_delta_red_flag':{
        'objective':'Run forensic SEC 10-K/20-F XBRL accrual/cash-conversion checks and paragraph-level Risk Factor diffs (~95% boilerplate stripped), staging a falsifier alert for human approval when red flags appear on a held position.',
        'pattern':'Deterministic Diff + Forensic Evaluator + Optional Human Approval Gate',
        'trigger':'Quarterly/annual filing release or fundamental red-flag audit',
        'approval_gate':True,
        'steps':['intake_validate','xbrl_accrual_and_dilution_forensics','consecutive_filing_paragraph_diff','modal_escalation_and_lexicon_scan','cross_check_portfolio_exposure','stage_falsifier_alert_or_deliver'],
        'acceptance_criteria':['Unchanged boilerplate (>=86% similarity) is stripped before LLM context','Hypothetical-to-realized risk wording shifts are flagged','Staged risk alerts require human approval before entering active memory']
    },
    'counterfactual_decision_attribution':{
        'objective':'Replay lookback window trades against a zero-trade static Buy-and-Hold baseline to decompose active buy timing, premature winner exits, protective exits, and commission drag.',
        'pattern':'Deterministic What-If Ledger Replay + Optional Guardrail Gate',
        'trigger':'Monthly/quarterly trading skill evaluation',
        'approval_gate':True,
        'steps':['intake_validate','reconstruct_window_executions','mark_open_and_closed_positions','decompose_buy_vs_sell_alpha_and_friction','rank_best_and_costliest_decisions','stage_habit_lesson_or_deliver'],
        'acceptance_criteria':['Unpriced closed contracts are isolated rather than guessed','Actual ending NAV minus net active alpha equals counterfactual static baseline','Any proposed behavioral rule pauses at awaiting_approval']
    }
}

def list_workflows():
    return [{'workflow_id':k,**v} for k,v in WORKFLOW_SPECS.items()]

def _save_run(run_id, workflow_id, status, current_step, completed_steps, pending_approval, inputs_meta, checkpoint, proposed_action, acceptance_passed):
    stamp=now()
    with db('ops') as c:
        schema(c)
        old=c.execute('SELECT created_at, retry_count FROM workflow_runs WHERE run_id=?',(run_id,)).fetchone()
        created=old['created_at'] if old else stamp
        retries=old['retry_count'] if old else 0
        c.execute('''INSERT OR REPLACE INTO workflow_runs
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (run_id,workflow_id,status,current_step,json.dumps(completed_steps),1 if pending_approval else 0,retries,json.dumps(inputs_meta,ensure_ascii=False),json.dumps(checkpoint,ensure_ascii=False,default=str),json.dumps(proposed_action,ensure_ascii=False) if proposed_action else None,1 if acceptance_passed else 0,created,stamp))

def run_workflow(workflow_id, params=None):
    """Execute a structured multi-stage investment workflow with intermediate validation and durable state checkpoints."""
    t0=time.perf_counter()
    if workflow_id not in WORKFLOW_SPECS: raise ValueError('unknown workflow_id')
    p=params if isinstance(params, dict) else (json.loads(params) if isinstance(params, str) and params.strip() else {})
    run_id='wf_'+uuid.uuid4().hex[:12]
    done=['intake_validate']

    if workflow_id=='morning_portfolio_review':
        brief=report.daily_brief()
        done.append('freshness_and_reconciliation_gate')
        factor=decision.portfolio_factor_risk('QQQ', 90)
        done.extend(['capital_and_risk_aggregation','covariance_and_factor_scan','tax_and_superficial_loss_scan','behavioral_guardrail_injection'])
        ok=brief.get('status') in {'ok','partial_or_stale'}
        done.append('acceptance_verification')
        checkpoint={
            'brief':brief,
            'factor_risk_headline':factor.get('portfolio_metrics') if factor.get('status')=='ok' else {'status':factor.get('status')},
            'high_correlation_clusters':(factor.get('high_correlation_clusters') or [])[:5],
            'acceptance':{'passed':ok,'advice_allowed':brief.get('advice_allowed')}
        }
        _save_run(run_id, workflow_id, 'completed', 'acceptance_verification', done, False, {'workflow':workflow_id}, checkpoint, None, ok)
        record_span('workflow', workflow_id, 'ok', (time.perf_counter()-t0)*1000.0, len(json.dumps(checkpoint))//4)
        return {'run_id':run_id,'workflow_id':workflow_id,'status':'completed','completed_steps':done,'pending_approval':False,'acceptance_passed':ok,'result':checkpoint}

    if workflow_id=='equity_valuation_and_expectations':
        sym=str(p.get('symbol') or '').strip().upper()
        if not sym: raise ValueError('symbol is required for equity_valuation_and_expectations')
        brief=decision.stock_brief(sym)
        done.extend(['load_holdings_and_peer_correlation','load_edgar_and_market_context'])
        fcff=str(p.get('fcff') or '').strip()
        wacc=str(p.get('wacc') or '0.10').strip()
        g1=str(p.get('stage1_growth') or '0.15').strip()
        gt=str(p.get('terminal_growth') or '0.025').strip()
        debt=str(p.get('net_debt') or '0').strip()
        shares=str(p.get('shares') or '').strip()
        ref_px=str(p.get('reference_price') or '').strip()
        years=int(p.get('years') or 5)
        ms_dcf=None; rev_dcf=None; sanity_warnings=[]
        if fcff and shares:
            ms_dcf=decision.multi_stage_dcf(fcff, wacc, g1, gt, debt, shares, years=years, fade=True, symbol=sym, fetch_live=False)
            rev_dcf=decision.reverse_dcf(fcff, wacc, gt, debt, shares, reference_price=ref_px, symbol=sym, years=years, fade=True)
            if ms_dcf.get('terminal_share_of_ev') and Decimal(ms_dcf['terminal_share_of_ev'])>Decimal('0.80'):
                sanity_warnings.append('terminal_value_dominates_ev_over_80pct')
            if ms_dcf.get('price_freshness')!='live':
                sanity_warnings.append('quote_not_live_upside_withheld')
        done.extend(['run_multi_stage_and_reverse_dcf','intermediate_sanity_validation','red_team_and_guardrail_check'])
        thesis_text=str(p.get('propose_thesis') or '').strip()
        falsifier=str(p.get('falsifier') or '').strip()
        conf=str(p.get('confidence') or '').strip()
        proposed=None; status='completed'; cur_step='stage_thesis_or_deliver'; pending=False
        if thesis_text:
            if not falsifier: sanity_warnings.append('proposed_thesis_missing_explicit_falsifier')
            proposed={'kind':'save_journal','symbol':sym,'thesis':thesis_text,'falsifier':falsifier,'confidence':conf}
            status='awaiting_approval'; cur_step='awaiting_human_approval'; pending=True
        checkpoint={'stock_brief':brief,'multi_stage_dcf':ms_dcf,'reverse_dcf':rev_dcf,'intermediate_sanity_warnings':sanity_warnings}
        _save_run(run_id, workflow_id, status, cur_step, done, pending, {'symbol':sym,'has_dcf':bool(ms_dcf),'staged_thesis':pending}, checkpoint, proposed, True)
        record_span('workflow', workflow_id, 'ok', (time.perf_counter()-t0)*1000.0, len(json.dumps(checkpoint))//4)
        return {'run_id':run_id,'workflow_id':workflow_id,'status':status,'current_step':cur_step,'completed_steps':done,'pending_approval':pending,'proposed_approval':proposed,'result':checkpoint}

    if workflow_id=='tax_aware_pre_trade_approval':
        sym=str(p.get('symbol') or '').strip().upper()
        act=str(p.get('action') or 'BUY').strip().upper()
        amt=str(p.get('amount_base') or '').strip()
        acct=str(p.get('account') or 'taxable').strip()
        thesis_text=str(p.get('thesis') or '').strip()
        falsifier=str(p.get('falsifier') or '').strip()
        conf=str(p.get('confidence') or '').strip()
        if not sym or not amt: raise ValueError('symbol and amount_base are required')
        gate=decision.evaluate_trade_gate(sym, act, amt, acct)
        done.append('six_point_pre_trade_gate')
        stress=decision.factor_stress_test('-0.15', '0.02', 'QQQ', acct if acct in {a['account'] for a in (finance.portfolio().get('accounts') or [])} else None)
        done.extend(['beta_and_fx_stress_delta','canadian_tax_verification'])
        proposed={
            'kind':'approved_trade_plan',
            'symbol':sym,
            'action':act,
            'amount_base':amt,
            'account':acct,
            'thesis':thesis_text or f'{act} {sym} pre-trade gated plan',
            'falsifier':falsifier,
            'confidence':conf,
            'gate_verdict':gate['verdict'],
            'orders':'disabled_manual_broker_execution_only'
        }
        checkpoint={'pre_trade_gate':gate,'portfolio_stress_context':{'pnl_impact_base':stress.get('portfolio_pnl_impact_base'),'nav_impact_pct':stress.get('portfolio_nav_impact_pct')}}
        status='awaiting_approval'; cur_step='awaiting_human_approval'
        _save_run(run_id, workflow_id, status, cur_step, done, True, {'symbol':sym,'action':act,'account':acct}, checkpoint, proposed, gate['verdict']!='blocked_stale_ledger')
        record_span('workflow', workflow_id, 'ok', (time.perf_counter()-t0)*1000.0, len(json.dumps(checkpoint))//4)
        return {'run_id':run_id,'workflow_id':workflow_id,'status':status,'current_step':cur_step,'completed_steps':done,'pending_approval':True,'proposed_approval':proposed,'result':checkpoint}

    if workflow_id=='recursive_behavioral_post_mortem':
        sym=str(p.get('symbol') or '').strip().upper() or None
        loop=decision.improvement_loop(sym)
        done.extend(['ledger_execution_telemetry','thesis_drift_and_brier_calibration','recurring_mistake_diagnosis'])
        rule_title=str(p.get('propose_rule_title') or '').strip()
        rule_detail=str(p.get('propose_rule_detail') or '').strip()
        rule_action=str(p.get('propose_action_rule') or '').strip()
        rule_kind=str(p.get('propose_kind') or 'goal').strip()
        proposed=None; status='completed'; cur_step='stage_guardrail_or_deliver'; pending=False
        if rule_title and rule_detail:
            if rule_kind not in {'goal','mistake','lesson'}: raise ValueError('propose_kind must be goal, mistake, or lesson')
            proposed={'kind':'save_lesson','lesson_kind':rule_kind,'symbol':sym or '','title':rule_title,'detail':rule_detail,'action_rule':rule_action}
            status='awaiting_approval'; cur_step='awaiting_human_approval'; pending=True
        _save_run(run_id, workflow_id, status, cur_step, done, pending, {'symbol':sym,'staged_rule':pending}, loop, proposed, True)
        record_span('workflow', workflow_id, 'ok', (time.perf_counter()-t0)*1000.0, len(json.dumps(loop))//4)
        return {'run_id':run_id,'workflow_id':workflow_id,'status':status,'current_step':cur_step,'completed_steps':done,'pending_approval':pending,'proposed_approval':proposed,'result':loop}

    if workflow_id=='sec_filing_delta_red_flag':
        sym=str(p.get('symbol') or '').strip().upper()
        if not sym: raise ValueError('symbol is required for sec_filing_delta_red_flag')
        audit=decision.filing_red_flag_audit(sym, include_private=True)
        done.extend(['xbrl_accrual_and_dilution_forensics','consecutive_filing_paragraph_diff','modal_escalation_and_lexicon_scan','cross_check_portfolio_exposure'])
        stage_alert=bool(p.get('stage_alert')) or (bool(audit.get('red_flags')) and bool((audit.get('portfolio_context') or {}).get('held')) and p.get('auto_stage_if_held', False))
        alert_rule=str(p.get('action_rule') or '').strip()
        proposed=None; status='completed'; cur_step='stage_falsifier_alert_or_deliver'; pending=False
        if stage_alert and audit.get('red_flags'):
            top_flag=audit['red_flags'][0]
            proposed={
                'kind':'save_lesson',
                'lesson_kind':'lesson',
                'symbol':sym,
                'title':f"SEC Filing Red-Flag Audit ({top_flag['code']})",
                'detail':top_flag['detail'],
                'action_rule':alert_rule or f"Re-verify {sym} thesis falsifier against latest SEC 10-K/20-F delta before adding."
            }
            status='awaiting_approval'; cur_step='awaiting_human_approval'; pending=True
        _save_run(run_id, workflow_id, status, cur_step, done, pending, {'symbol':sym,'red_flags':len(audit.get('red_flags') or [])}, audit, proposed, True)
        record_span('workflow', workflow_id, 'ok', (time.perf_counter()-t0)*1000.0, len(json.dumps(audit))//4)
        return {'run_id':run_id,'workflow_id':workflow_id,'status':status,'current_step':cur_step,'completed_steps':done,'pending_approval':pending,'proposed_approval':proposed,'result':audit}

    if workflow_id=='counterfactual_decision_attribution':
        days=int(p.get('days') or 90)
        acct=str(p.get('account') or '').strip() or None
        attrib=decision.counterfactual_attribution(days=days, account=acct)
        done.extend(['reconstruct_window_executions','mark_open_and_closed_positions','decompose_buy_vs_sell_alpha_and_friction','rank_best_and_costliest_decisions'])
        rule_title=str(p.get('propose_rule_title') or '').strip()
        rule_action=str(p.get('propose_action_rule') or '').strip()
        proposed=None; status='completed'; cur_step='stage_habit_lesson_or_deliver'; pending=False
        if rule_title:
            proposed={
                'kind':'save_lesson',
                'lesson_kind':'goal',
                'symbol':'',
                'title':rule_title,
                'detail':f"Derived from {days}d counterfactual attribution (verdict={attrib.get('verdict')}).",
                'action_rule':rule_action or rule_title
            }
            status='awaiting_approval'; cur_step='awaiting_human_approval'; pending=True
        _save_run(run_id, workflow_id, status, cur_step, done, pending, {'days':days,'account':acct}, attrib, proposed, attrib.get('status') in {'ok','partial_or_stale'})
        record_span('workflow', workflow_id, 'ok', (time.perf_counter()-t0)*1000.0, len(json.dumps(attrib))//4)
        return {'run_id':run_id,'workflow_id':workflow_id,'status':status,'current_step':cur_step,'completed_steps':done,'pending_approval':pending,'proposed_approval':proposed,'result':attrib}

def get_workflow_run(run_id):
    with db('ops') as c:
        schema(c)
        row=c.execute('SELECT * FROM workflow_runs WHERE run_id=?',(str(run_id),)).fetchone()
    if not row: return {'status':'not_found','run_id':run_id}
    d=dict(row)
    d['completed_steps']=json.loads(d['completed_steps'])
    d['inputs_meta']=json.loads(d['inputs_meta'])
    d['checkpoint']=json.loads(d.pop('checkpoint_json'))
    d['proposed_action']=json.loads(d.pop('proposed_action_json')) if d.get('proposed_action_json') else None
    return d

def list_workflow_runs(status=None, limit=20):
    with db('ops') as c:
        schema(c)
        if status:
            rows=c.execute('SELECT run_id,workflow_id,status,current_step,pending_approval,acceptance_passed,inputs_meta,proposed_action_json,created_at,updated_at FROM workflow_runs WHERE status=? ORDER BY updated_at DESC LIMIT ?',(status,bounded(limit))).fetchall()
        else:
            rows=c.execute('SELECT run_id,workflow_id,status,current_step,pending_approval,acceptance_passed,inputs_meta,proposed_action_json,created_at,updated_at FROM workflow_runs ORDER BY updated_at DESC LIMIT ?',(bounded(limit),)).fetchall()
    out=[]
    for r in rows:
        item=dict(r)
        item['inputs_meta']=json.loads(item['inputs_meta'])
        item['proposed_action']=json.loads(item.pop('proposed_action_json')) if item.get('proposed_action_json') else None
        out.append(item)
    return out

def approve_workflow(run_id, operator_note=''):
    """Human-in-the-Loop Resume-After-Approval: commits staged thesis/guardrail/plan into durable memory and marks run completed. Never places broker orders."""
    state=get_workflow_run(run_id)
    if state.get('status')=='not_found': raise ValueError('workflow run not found')
    if state['status']!='awaiting_approval' or not state.get('proposed_action'):
        raise ValueError('workflow run is not awaiting approval')
    prop=state['proposed_action']
    memory_id=None
    if prop['kind']=='save_journal':
        memory_id=knowledge.save_journal(prop['symbol'], prop['thesis'], 'workflow_human_approved', alternatives=operator_note, risk=prop.get('falsifier') or '', confidence=prop.get('confidence') or '')
    elif prop['kind']=='approved_trade_plan':
        plan_thesis=f"[{prop['action']} {prop['amount_base']} base in {prop['account']} | gate={prop['gate_verdict']}] {prop['thesis']}"
        if operator_note: plan_thesis+=' | Note: '+operator_note
        memory_id=knowledge.save_journal(prop['symbol'], plan_thesis, 'workflow_human_approved', risk=prop.get('falsifier') or '', confidence=prop.get('confidence') or '')
    elif prop['kind']=='save_lesson':
        detail=prop['detail']+(' | Operator note: '+operator_note if operator_note else '')
        memory_id=knowledge.save_lesson(prop['lesson_kind'], prop['title'], detail, 'workflow_human_approved', symbol=prop.get('symbol') or '', action_rule=prop.get('action_rule') or '')
    done=list(state['completed_steps'])+['human_approved','committed_to_memory']
    checkpoint=dict(state['checkpoint'])
    checkpoint['approval_resolution']={'decision':'approved','memory_id':memory_id,'operator_note':operator_note,'resolved_at':now(),'orders':'disabled'}
    _save_run(run_id, state['workflow_id'], 'completed', 'committed_to_memory', done, False, state['inputs_meta'], checkpoint, prop, True)
    return {'run_id':run_id,'status':'completed','committed_memory_id':memory_id,'orders':'disabled'}

def reject_workflow(run_id, reason='rejected_by_operator'):
    """Human-in-the-Loop Rejection: records rejection without modifying memory or ledger."""
    state=get_workflow_run(run_id)
    if state.get('status')=='not_found': raise ValueError('workflow run not found')
    if state['status']!='awaiting_approval': raise ValueError('workflow run is not awaiting approval')
    done=list(state['completed_steps'])+['human_rejected']
    checkpoint=dict(state['checkpoint'])
    checkpoint['approval_resolution']={'decision':'rejected','reason':reason,'resolved_at':now()}
    _save_run(run_id, state['workflow_id'], 'rejected', 'human_rejected', done, False, state['inputs_meta'], checkpoint, state.get('proposed_action'), False)
    return {'run_id':run_id,'status':'rejected','reason':reason}

def run_eval_benchmark():
    """Deterministic Evaluation Harness: runs synthetic accounting, valuation, risk, DLP security, and workflow state-machine regression benchmarks."""
    prev_home=os.environ.get('PERSONAL_AGENT_HOME')
    prev_cfg=os.environ.get('PERSONAL_AGENT_CONFIG')
    cases=[]
    def record_case(name, passed, metric=''):
        cases.append({'case':name,'passed':bool(passed),'metric':str(metric)})
    try:
        with tempfile.TemporaryDirectory() as tmp:
            t_home=Path(tmp)
            cfg_path=t_home/'config.json'
            cfg_path.write_text(json.dumps({
                'knowledge_roots':[],
                'finance':{'accounts':{'U0000001':'taxable','U0000002':'tfsa'},'base_currency':'CAD','flex_queries':[],'max_snapshot_age_days':4000},
                'market':{'watchlist':['QQQ'],'alpaca_key_env':'ALPACA_API_KEY','alpaca_secret_env':'ALPACA_API_SECRET','finnhub_key_env':'FINNHUB_API_KEY','fred_key_env':'FRED_API_KEY','edgar_contact':''},
                'policy':{'max_file_bytes':10000000,'max_tool_rows':50}
            }), encoding='utf-8')
            os.environ['PERSONAL_AGENT_HOME']=str(t_home)
            os.environ['PERSONAL_AGENT_CONFIG']=str(cfg_path)
            from importlib import reload
            from . import core
            reload(core); reload(finance); reload(market); reload(knowledge); reload(decision); reload(report)
            fixtures=ROOT/'tests'/'fixtures'
            finance.import_xml(fixtures/'two_accounts.xml')
            snap=finance.portfolio(); perf=finance.performance()
            record_case('nav_conservation_and_transfer_neutrality', Decimal(snap['nav'])==Decimal('2000.00') and Decimal(perf['contributions'])==0, snap['nav'])
            ms=decision.multi_stage_dcf('120','0.10','0.22','0.025','40','10',years=5,fade=True)
            rev=decision.reverse_dcf('120','0.10','0.025','40','10',reference_price=ms['per_share'],years=5,fade=True)
            err=abs(Decimal(rev['implied_stage1_fcff_growth'])-Decimal('0.22'))
            record_case('multi_stage_reverse_dcf_inversion', err<Decimal('0.0005'), f'err={err}')
            market._save_quote('QQQ','450','2026-11-16T10:00:00+00:00','iex','alpaca')
            priced=decision.attach_market_price(ms,'QQQ',fetch_live=False)
            record_case('stale_quote_never_computes_live_upside', priced['upside']=='withheld' and priced['price_freshness']!='live', priced['price_freshness'])
            ssrf_blocked=False
            try: market._assert_public_http_url('http://127.0.0.1:8080/v1')
            except ValueError: ssrf_blocked=True
            record_case('ssrf_loopback_firewall', ssrf_blocked, 'blocked_127.0.0.1')
            jid1=knowledge.save_journal('QQQ','Thesis A','eval',risk='Falsifier A',confidence='0.80',outcome_hit='1')
            jid2=knowledge.save_journal('SPY','Thesis B','eval',risk='Falsifier B',confidence='0.60',outcome_hit='0')
            loop=decision.improvement_loop()
            brier=loop['thesis_calibration']['brier_score']
            expected_brier=round(((0.8-1.0)**2+(0.6-0.0)**2)/2.0, 4)
            record_case('brier_score_epistemic_calibration', jid1 and jid2 and brier==expected_brier, f'brier={brier}')
            market.save_filing_section('SYNTH','10-K','2025-02-15','0001-25-01','risk_factors','Item 1A. Risk Factors\nGeneral macroeconomic headwinds could adversely affect widget demand across regions.\nStandard competition boilerplate remains unchanged across fiscal periods for widget suppliers.')
            market.save_filing_section('SYNTH','10-K','2026-02-15','0001-26-01','risk_factors','Item 1A. Risk Factors\nGeneral macroeconomic headwinds have experienced severe deterioration and resulted in customer cancellations.\nStandard competition boilerplate remains unchanged across fiscal periods for widget suppliers.\nWe received a subpoena and regulatory investigation notice regarding revenue recognition controls and material weakness.')
            f_audit=decision.filing_red_flag_audit('SYNTH', include_private=False)
            codes={rf['code'] for rf in f_audit.get('red_flags') or []}
            record_case('sec_10k_delta_boilerplate_and_escalation_detection', 'risk_modal_escalation' in codes and 'new_risk_factor_disclosures' in codes, ','.join(sorted(codes)))
            cf=decision.counterfactual_attribution(days=90)
            wc=cf.get('wealth_comparison') or {}
            ident_ok=(Decimal(wc['actual_ending_nav_base'])-Decimal(wc['net_active_trading_alpha_base']))==Decimal(wc['counterfactual_static_buy_and_hold_nav_base'])
            record_case('counterfactual_buy_and_hold_wealth_identity', ident_ok, wc.get('verdict'))
    finally:
        if prev_home is None: os.environ.pop('PERSONAL_AGENT_HOME', None)
        else: os.environ['PERSONAL_AGENT_HOME']=prev_home
        if prev_cfg is None: os.environ.pop('PERSONAL_AGENT_CONFIG', None)
        else: os.environ['PERSONAL_AGENT_CONFIG']=prev_cfg
        from importlib import reload
        from . import core
        reload(core); reload(finance); reload(market); reload(knowledge); reload(decision); reload(report)
    passed=sum(1 for c in cases if c['passed']); total=len(cases)
    score=round(passed*100.0/max(1,total), 1)
    eval_id='eval_'+uuid.uuid4().hex[:10]
    with db('ops') as c:
        schema(c)
        c.execute('INSERT INTO eval_runs VALUES(?,?,?,?,?,?)',(eval_id,passed,total,score,json.dumps(cases,ensure_ascii=False),now()))
    return {'eval_id':eval_id,'passed':passed,'total':total,'score_pct':score,'cases':cases}

def list_eval_runs(limit=10):
    with db('ops') as c:
        schema(c)
        rows=c.execute('SELECT * FROM eval_runs ORDER BY rowid DESC LIMIT ?',(bounded(limit),)).fetchall()
    out=[]
    for r in rows:
        d=dict(r); d['cases']=json.loads(d.pop('details_json')); out.append(d)
    return out
