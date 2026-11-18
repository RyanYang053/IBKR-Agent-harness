"""Stateful Workflow Engine, Human-in-the-Loop Approvals, Privacy-Safe Observability, and Evaluation Harness."""
from datetime import datetime, timezone
from decimal import Decimal
import functools, hashlib, json, os, subprocess, sys, tempfile, time, uuid
from pathlib import Path
from .core import ROOT, bounded, db, now
from . import decision, finance, knowledge, market, quant, report

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
        updated_at TEXT NOT NULL,
        immutable_params_json TEXT NOT NULL DEFAULT '{}',
        input_hash TEXT NOT NULL DEFAULT '',
        workflow_version TEXT NOT NULL DEFAULT '1'
    );
    CREATE TABLE IF NOT EXISTS approval_intents(
        run_id TEXT PRIMARY KEY,
        decision TEXT NOT NULL,
        operator_note TEXT NOT NULL DEFAULT '',
        memory_id INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        lease_owner TEXT,
        lease_expires_at TEXT
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
    CREATE TABLE IF NOT EXISTS scheduled_jobs(
        job_id TEXT PRIMARY KEY,
        job_name TEXT NOT NULL,
        schedule_window TEXT NOT NULL,
        status TEXT NOT NULL,
        summary_json TEXT NOT NULL,
        ran_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS alerts(
        dedup_key TEXT PRIMARY KEY,
        symbol TEXT NOT NULL DEFAULT '',
        severity TEXT NOT NULL,
        title TEXT NOT NULL,
        detail TEXT NOT NULL,
        evidence_ids_json TEXT NOT NULL DEFAULT '[]',
        acknowledged INTEGER NOT NULL DEFAULT 0,
        hit_count INTEGER NOT NULL DEFAULT 1,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL
    );
    ''')
    wf_cols={r[1] for r in c.execute('PRAGMA table_info(workflow_runs)')}
    for col_def in ("immutable_params_json TEXT NOT NULL DEFAULT '{}'", "input_hash TEXT NOT NULL DEFAULT ''", "workflow_version TEXT NOT NULL DEFAULT '1'"):
        cname=col_def.split()[0]
        if cname not in wf_cols: c.execute(f'ALTER TABLE workflow_runs ADD COLUMN {col_def}')
    ai_cols={r[1] for r in c.execute('PRAGMA table_info(approval_intents)')}
    for col_def in ('lease_owner TEXT', 'lease_expires_at TEXT'):
        cname=col_def.split()[0]
        if cname not in ai_cols: c.execute(f'ALTER TABLE approval_intents ADD COLUMN {col_def}')

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
    },
    'investment_committee_memo':{
        'objective':'Run the 8-section Should-I-Invest? Investment Committee synthesis: Research Case, Market Expectations, Driver/Reverse Valuation, Red Team, Portfolio & Policy Impact, Alternatives vs QQQ/Cash, Capital Allocation, and Decision Memo halting for human approval.',
        'pattern':'Single-Orchestrator 8-Stage Committee Synthesis + Mandatory HITL Gate',
        'trigger':'New investment candidate, earnings re-underwriting, or major capital deployment',
        'approval_gate':True,
        'steps':['1_research_and_edgar_forensics','2_market_expectations_and_gap','3_driver_and_reverse_valuation','4_red_team_and_mistake_guardrails','5_portfolio_and_policy_impact','6_opportunity_cost_vs_qqq_and_cash','7_sizing_and_account_location','8_awaiting_human_committee_approval'],
        'acceptance_criteria':['Policy mandate compliance is checked','Opportunity cost against core benchmark and cash is quantified','Decision memo pauses at awaiting_approval before any commitment']
    }
}

def list_workflows():
    return [{'workflow_id':k,**v} for k,v in WORKFLOW_SPECS.items()]

def _save_run(run_id, workflow_id, status, current_step, completed_steps, pending_approval, inputs_meta, checkpoint, proposed_action, acceptance_passed, immutable_params=None, input_hash=None, workflow_version='1'):
    stamp=now()
    with db('ops') as c:
        schema(c)
        old=c.execute('SELECT created_at, retry_count, immutable_params_json, input_hash, workflow_version FROM workflow_runs WHERE run_id=?',(run_id,)).fetchone()
        created=old['created_at'] if old else stamp
        retries=old['retry_count'] if old else 0
        imm_json=old['immutable_params_json'] if old else json.dumps(immutable_params if immutable_params is not None else (inputs_meta.get('immutable_params') if isinstance(inputs_meta, dict) and 'immutable_params' in inputs_meta else {}), ensure_ascii=False, sort_keys=True)
        in_h=old['input_hash'] if old else (input_hash if input_hash is not None else (inputs_meta.get('input_hash','') if isinstance(inputs_meta, dict) else ''))
        wf_ver=old['workflow_version'] if old else str(workflow_version or '1')
        c.execute('''INSERT INTO workflow_runs
            (run_id,workflow_id,status,current_step,completed_steps,pending_approval,retry_count,inputs_meta,checkpoint_json,proposed_action_json,acceptance_passed,created_at,updated_at,immutable_params_json,input_hash,workflow_version)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(run_id) DO UPDATE SET
                status=excluded.status,
                current_step=excluded.current_step,
                completed_steps=excluded.completed_steps,
                pending_approval=excluded.pending_approval,
                inputs_meta=excluded.inputs_meta,
                checkpoint_json=excluded.checkpoint_json,
                proposed_action_json=excluded.proposed_action_json,
                acceptance_passed=excluded.acceptance_passed,
                updated_at=excluded.updated_at''',
            (run_id,workflow_id,status,current_step,json.dumps(completed_steps),1 if pending_approval else 0,retries,json.dumps(inputs_meta,ensure_ascii=False),json.dumps(checkpoint,ensure_ascii=False,default=str),json.dumps(proposed_action,ensure_ascii=False) if proposed_action else None,1 if acceptance_passed else 0,created,stamp,imm_json,in_h,wf_ver))

def _param_hash(obj):
    return hashlib.sha256(json.dumps(obj or {}, sort_keys=True, default=str).encode('utf-8')).hexdigest()[:16]

def _positions_hash():
    return finance.load_full_portfolio_snapshot().snapshot_id

def run_workflow(workflow_id, params=None, resume_run_id=None):
    """Execute a structured multi-stage investment workflow with immutable input binding, step-by-step durable SQLite checkpoints, and safe crash-resume."""
    t0=time.perf_counter()
    if workflow_id not in WORKFLOW_SPECS: raise ValueError('unknown workflow_id')
    raw_p=params if isinstance(params, dict) else (json.loads(params) if isinstance(params, str) and params.strip() else None)
    if resume_run_id:
        prior=get_workflow_run(resume_run_id)
        if prior.get('status')=='not_found': raise ValueError('resume_run_id not found')
        if prior.get('workflow_id')!=workflow_id: raise ValueError('workflow_id mismatch on resume')
        meta=dict(prior.get('inputs_meta') or {})
        locked_p=json.loads(prior['immutable_params_json']) if prior.get('immutable_params_json') is not None else meta.get('immutable_params')
        locked_h=prior.get('input_hash') or meta.get('input_hash')
        if locked_p is None:
            raise ValueError('workflow run lacks immutable_params; cannot safely resume')
        if raw_p is not None and _param_hash(raw_p)!=locked_h:
            raise ValueError('cannot resume workflow with modified parameters; start a new run')
        p=dict(locked_p)
        run_id=resume_run_id
        done=list(prior['completed_steps'] or ['intake_validate'])
        ckpt=dict(prior['checkpoint'] or {})
        with db('ops') as c:
            c.execute('UPDATE workflow_runs SET retry_count=retry_count+1, updated_at=? WHERE run_id=?',(now(),run_id))
    else:
        p=raw_p or {}
        run_id='wf_'+uuid.uuid4().hex[:12]
        done=['intake_validate']
        ckpt={}
        meta={'workflow':workflow_id,'immutable_params':p,'input_hash':_param_hash(p)}
        _save_run(run_id, workflow_id, 'running', 'intake_validate', done, False, meta, ckpt, None, False, immutable_params=p, input_hash=_param_hash(p), workflow_version='1')

    def _ckpt(step_name, key=None, val=None):
        if key is not None: ckpt[key]=val
        if step_name not in done: done.append(step_name)
        _save_run(run_id, workflow_id, 'running', step_name, done, False, meta, ckpt, None, False)

    if workflow_id=='morning_portfolio_review':
        if 'brief' not in ckpt:
            _ckpt('freshness_and_reconciliation_gate', 'brief', report.daily_brief())
        if 'factor_risk_headline' not in ckpt:
            factor=decision.portfolio_factor_risk('QQQ', 90)
            ckpt['factor_risk_headline']=factor.get('portfolio_metrics') if factor.get('status')=='ok' else {'status':factor.get('status')}
            ckpt['high_correlation_clusters']=(factor.get('high_correlation_clusters') or [])[:5]
            done.extend([s for s in ('capital_and_risk_aggregation','covariance_and_factor_scan','tax_and_superficial_loss_scan','behavioral_guardrail_injection') if s not in done])
            _save_run(run_id, workflow_id, 'running', 'behavioral_guardrail_injection', done, False, {'workflow':workflow_id}, ckpt, None, False)
        brief=ckpt['brief']
        ok=brief.get('status') in {'ok','partial_or_stale'}
        if 'acceptance_verification' not in done: done.append('acceptance_verification')
        ckpt['acceptance']={'passed':ok,'advice_allowed':brief.get('advice_allowed')}
        _save_run(run_id, workflow_id, 'completed', 'acceptance_verification', done, False, {'workflow':workflow_id}, ckpt, None, ok)
        record_span('workflow', workflow_id, 'ok', (time.perf_counter()-t0)*1000.0, len(json.dumps(ckpt))//4)
        return {'run_id':run_id,'workflow_id':workflow_id,'status':'completed','completed_steps':done,'pending_approval':False,'acceptance_passed':ok,'result':ckpt}

    if workflow_id=='equity_valuation_and_expectations':
        sym=str(p.get('symbol') or '').strip().upper()
        if not sym: raise ValueError('symbol is required for equity_valuation_and_expectations')
        if 'stock_brief' not in ckpt:
            _ckpt('load_holdings_and_peer_correlation', 'stock_brief', decision.stock_brief(sym))
            _ckpt('load_edgar_and_market_context')
        brief=ckpt['stock_brief']
        fcff=str(p.get('fcff') or '').strip()
        wacc=str(p.get('wacc') or '0.10').strip()
        g1=str(p.get('stage1_growth') or '0.15').strip()
        gt=str(p.get('terminal_growth') or '0.025').strip()
        debt=str(p.get('net_debt') or '0').strip()
        shares=str(p.get('shares') or '').strip()
        ref_px=str(p.get('reference_price') or '').strip()
        years=int(p.get('years') or 5)
        if 'multi_stage_dcf' not in ckpt:
            ms_dcf=None; rev_dcf=None; sanity_warnings=[]
            if fcff and shares:
                ms_dcf=decision.multi_stage_dcf(fcff, wacc, g1, gt, debt, shares, years=years, fade=True, symbol=sym, fetch_live=False)
                rev_dcf=decision.reverse_dcf(fcff, wacc, gt, debt, shares, reference_price=ref_px, symbol=sym, years=years, fade=True)
                if ms_dcf.get('terminal_share_of_ev') and Decimal(ms_dcf['terminal_share_of_ev'])>Decimal('0.80'):
                    sanity_warnings.append('terminal_value_dominates_ev_over_80pct')
                if ms_dcf.get('price_freshness')!='live':
                    sanity_warnings.append('quote_not_live_upside_withheld')
            ckpt['multi_stage_dcf']=ms_dcf; ckpt['reverse_dcf']=rev_dcf; ckpt['intermediate_sanity_warnings']=sanity_warnings
            for stp in ('run_multi_stage_and_reverse_dcf','intermediate_sanity_validation','red_team_and_guardrail_check'):
                if stp not in done: done.append(stp)
            _save_run(run_id, workflow_id, 'running', 'red_team_and_guardrail_check', done, False, {'symbol':sym,'has_dcf':bool(ms_dcf)}, ckpt, None, False)
        thesis_text=str(p.get('propose_thesis') or '').strip()
        falsifier=str(p.get('falsifier') or '').strip()
        conf=str(p.get('confidence') or '').strip()
        proposed=None; status='completed'; cur_step='stage_thesis_or_deliver'; pending=False
        if thesis_text:
            if not falsifier: ckpt['intermediate_sanity_warnings'].append('proposed_thesis_missing_explicit_falsifier')
            proposed={'kind':'save_journal','symbol':sym,'thesis':thesis_text,'falsifier':falsifier,'confidence':conf}
            status='awaiting_approval'; cur_step='awaiting_human_approval'; pending=True
        _save_run(run_id, workflow_id, status, cur_step, done, pending, {'symbol':sym,'has_dcf':bool(ckpt.get('multi_stage_dcf')),'staged_thesis':pending}, ckpt, proposed, True)
        record_span('workflow', workflow_id, 'ok', (time.perf_counter()-t0)*1000.0, len(json.dumps(ckpt))//4)
        return {'run_id':run_id,'workflow_id':workflow_id,'status':status,'current_step':cur_step,'completed_steps':done,'pending_approval':pending,'proposed_approval':proposed,'result':ckpt}

    if workflow_id=='tax_aware_pre_trade_approval':
        sym=str(p.get('symbol') or '').strip().upper()
        act=str(p.get('action') or 'BUY').strip().upper()
        amt=str(p.get('amount_base') or '').strip()
        acct=str(p.get('account') or 'taxable').strip()
        thesis_text=str(p.get('thesis') or '').strip()
        falsifier=str(p.get('falsifier') or '').strip()
        conf=str(p.get('confidence') or '').strip()
        if not sym or not amt: raise ValueError('symbol and amount_base are required')
        if 'pre_trade_gate' not in ckpt:
            _ckpt('six_point_pre_trade_gate', 'pre_trade_gate', decision.evaluate_trade_gate(sym, act, amt, acct))
        gate=ckpt['pre_trade_gate']
        if 'portfolio_stress_context' not in ckpt:
            stress=decision.factor_stress_test('-0.15', '0.02', 'QQQ', acct if acct in {a['account'] for a in (finance.portfolio().get('accounts') or [])} else None)
            ckpt['portfolio_stress_context']={'pnl_impact_base':stress.get('portfolio_pnl_impact_base'),'nav_impact_pct':stress.get('portfolio_nav_impact_pct')}
            for stp in ('beta_and_fx_stress_delta','canadian_tax_verification'):
                if stp not in done: done.append(stp)
        g_verd=gate.get('verdict') or gate.get('status','blocked')
        proposed={
            'kind':'approved_trade_plan',
            'symbol':sym,
            'action':act,
            'amount_base':amt,
            'account':acct,
            'thesis':thesis_text or f'{act} {sym} pre-trade gated plan',
            'falsifier':falsifier,
            'confidence':conf,
            'gate_verdict':g_verd,
            'staged_portfolio_as_of':gate.get('portfolio_as_of'),
            'staged_portfolio_nav':gate.get('portfolio_nav'),
            'staged_policy_hash':gate.get('policy_hash'),
            'staged_positions_hash':_positions_hash(),
            'orders':'disabled_manual_broker_execution_only'
        }
        status='awaiting_approval'; cur_step='awaiting_human_approval'
        acc_ok=g_verd in {'clear','caution_review_flags'}
        _save_run(run_id, workflow_id, status, cur_step, done, True, {'symbol':sym,'action':act,'account':acct,'staged_nav':gate.get('portfolio_nav'),'staged_policy_hash':gate.get('policy_hash')}, ckpt, proposed, acc_ok)
        record_span('workflow', workflow_id, 'ok', (time.perf_counter()-t0)*1000.0, len(json.dumps(ckpt))//4)
        return {'run_id':run_id,'workflow_id':workflow_id,'status':status,'current_step':cur_step,'completed_steps':done,'pending_approval':True,'proposed_approval':proposed,'result':ckpt}

    if workflow_id=='recursive_behavioral_post_mortem':
        sym=str(p.get('symbol') or '').strip().upper() or None
        if 'loop_result' not in ckpt:
            ckpt['loop_result']=decision.improvement_loop(sym)
            for stp in ('ledger_execution_telemetry','thesis_drift_and_brier_calibration','recurring_mistake_diagnosis'):
                if stp not in done: done.append(stp)
            _save_run(run_id, workflow_id, 'running', 'recurring_mistake_diagnosis', done, False, {'symbol':sym}, ckpt, None, False)
        loop=ckpt['loop_result']
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
        if 'audit_result' not in ckpt:
            ckpt['audit_result']=decision.filing_red_flag_audit(sym, include_private=True)
            for stp in ('xbrl_accrual_and_dilution_forensics','consecutive_filing_paragraph_diff','modal_escalation_and_lexicon_scan','cross_check_portfolio_exposure'):
                if stp not in done: done.append(stp)
            _save_run(run_id, workflow_id, 'running', 'cross_check_portfolio_exposure', done, False, {'symbol':sym}, ckpt, None, False)
        audit=ckpt['audit_result']
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
        if 'attrib_result' not in ckpt:
            ckpt['attrib_result']=decision.counterfactual_attribution(days=days, account=acct)
            for stp in ('reconstruct_window_executions','mark_open_and_closed_positions','decompose_buy_vs_sell_alpha_and_friction','rank_best_and_costliest_decisions'):
                if stp not in done: done.append(stp)
            _save_run(run_id, workflow_id, 'running', 'rank_best_and_costliest_decisions', done, False, {'days':days,'account':acct}, ckpt, None, False)
        attrib=ckpt['attrib_result']
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

    if workflow_id=='investment_committee_memo':
        if not resume_run_id: done=[]
        sym=str(p.get('symbol') or '').strip().upper()
        amt=str(p.get('amount_base') or '2500').strip()
        act=str(p.get('action') or 'BUY').strip().upper()
        acct=str(p.get('account') or 'taxable').strip()
        if not sym: raise ValueError('symbol is required for investment_committee_memo')
        if '1_research_case' not in ckpt:
            _ckpt('1_research_and_edgar_forensics', '1_research_case', {'stock_brief':decision.stock_brief(sym),'filing_forensics':decision.filing_red_flag_audit(sym, include_private=True),'living_thesis':quant.get_living_thesis(sym)})
        if '2_market_expectations' not in ckpt:
            _ckpt('2_market_expectations_and_gap', '2_market_expectations', quant.evaluate_expectation_gap(sym))
        if '3_valuation_scenarios' not in ckpt:
            s3_val=None
            if p.get('revenue_t0') and p.get('shares'):
                s3_val=quant.driver_based_valuation(p['revenue_t0'], p.get('rev_cagr_base','0.15'), p.get('op_margin_target','0.25'), wacc=p.get('wacc','0.10'), terminal_growth=p.get('terminal_growth','0.025'), net_debt=p.get('net_debt','0'), shares=p['shares'], symbol=sym)
            _ckpt('3_driver_and_reverse_valuation', '3_valuation_scenarios', s3_val)
        if '4_red_team_and_gate' not in ckpt:
            _ckpt('4_red_team_and_mistake_guardrails', '4_red_team_and_gate', decision.evaluate_trade_gate(sym, act, amt, acct))
        if '5_policy_compliance' not in ckpt:
            _ckpt('5_portfolio_and_policy_impact', '5_policy_compliance', quant.policy_compliance().get('exposure_summary'))
        if '6_and_7_alternatives_and_allocation' not in ckpt:
            s6_alloc=quant.allocate_incremental_capital(amt, candidates=[sym, quant.load_policy().get('core_benchmark','QQQ')])
            if '6_opportunity_cost_vs_qqq_and_cash' not in done: done.append('6_opportunity_cost_vs_qqq_and_cash')
            _ckpt('7_sizing_and_account_location', '6_and_7_alternatives_and_allocation', s6_alloc)
        s4_gate=ckpt['4_red_team_and_gate']
        g_verd=s4_gate.get('verdict') or s4_gate.get('status','blocked')
        proposed={
            'kind':'approved_trade_plan',
            'symbol':sym,
            'action':act,
            'amount_base':amt,
            'account':acct,
            'thesis':str(p.get('thesis') or f'IC Memo {act} {sym}'),
            'falsifier':str(p.get('falsifier') or ''),
            'confidence':str(p.get('confidence') or ''),
            'gate_verdict':g_verd,
            'staged_portfolio_as_of':s4_gate.get('portfolio_as_of'),
            'staged_portfolio_nav':s4_gate.get('portfolio_nav'),
            'staged_policy_hash':s4_gate.get('policy_hash'),
            'staged_positions_hash':_positions_hash(),
            'orders':'disabled_manual_broker_execution_only'
        }
        acc_ok=g_verd in {'clear','caution_review_flags'}
        _save_run(run_id, workflow_id, 'awaiting_approval', '8_awaiting_human_committee_approval', done, True, {'symbol':sym,'action':act,'amount_base':amt,'staged_nav':s4_gate.get('portfolio_nav'),'staged_policy_hash':s4_gate.get('policy_hash')}, ckpt, proposed, acc_ok)
        record_span('workflow', workflow_id, 'ok', (time.perf_counter()-t0)*1000.0, len(json.dumps(ckpt))//4)
        return {'run_id':run_id,'workflow_id':workflow_id,'status':'awaiting_approval','current_step':'8_awaiting_human_committee_approval','completed_steps':done,'pending_approval':True,'proposed_approval':proposed,'result':ckpt}

def resume_workflow(run_id, params=None):
    """Resume an interrupted workflow run from its latest durable SQLite checkpoint."""
    state=get_workflow_run(run_id)
    if state.get('status')=='not_found': raise ValueError('workflow run not found')
    if state['status']!='running': raise ValueError(f"workflow run is in status={state['status']}, not resumable running state")
    return run_workflow(state['workflow_id'], params=params, resume_run_id=run_id)

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
    """Human-in-the-Loop Resume-After-Approval: crash-recoverable approval_intents, TTL, idempotency, positions/policy binding, and live gate re-validation."""
    state=get_workflow_run(run_id)
    if state.get('status')=='not_found': raise ValueError('workflow run not found')
    checkpoint=dict(state['checkpoint'])
    rec_mid=None; rec_note=''; need_repair=False; recovering_null_intent=False
    with db('ops') as c:
        schema(c)
        intent=c.execute('SELECT * FROM approval_intents WHERE run_id=?',(str(run_id),)).fetchone()
        if intent:
            if intent['decision']=='approved':
                if state['status']!='completed':
                    rec_mid=intent['memory_id']; rec_note=intent['operator_note']; need_repair=True
                else:
                    return {'run_id':run_id,'status':'completed','idempotent':True,'committed_memory_id':intent['memory_id'],'orders':'disabled'}
            elif intent['decision']=='rejected':
                raise ValueError('workflow run already rejected')
            elif intent['decision']=='approving' and intent['memory_id'] is not None:
                rec_mid=intent['memory_id']; rec_note=intent['operator_note']; need_repair=True
                c.execute("UPDATE approval_intents SET decision='approved', updated_at=? WHERE run_id=?",(now(), str(run_id)))
            elif intent['decision']=='approving' and intent['memory_id'] is None:
                k_existing=knowledge.find_by_approval_run_id(str(run_id))
                if k_existing:
                    rec_mid=k_existing['id']; rec_note=intent['operator_note']; need_repair=True
                    c.execute("UPDATE approval_intents SET memory_id=?, decision='approved', updated_at=? WHERE run_id=?",(rec_mid, now(), str(run_id)))
                else:
                    lease_id=f'lease_{uuid.uuid4().hex[:8]}'
                    t_now=datetime.now(timezone.utc)
                    exp_ts=(t_now.timestamp()+120.0)
                    exp_iso=datetime.fromtimestamp(exp_ts, tz=timezone.utc).isoformat()
                    cur_l=c.execute("UPDATE approval_intents SET lease_owner=?, lease_expires_at=?, updated_at=? WHERE run_id=? AND decision='approving' AND memory_id IS NULL AND (lease_owner IS NULL OR lease_expires_at<?)",(lease_id, exp_iso, t_now.isoformat(), str(run_id), t_now.isoformat()))
                    if cur_l.rowcount==0:
                        raise ValueError('approval recovery already in progress by active lease owner')
                    recovering_null_intent=True
    if need_repair and rec_mid is not None:
        done=list(state['completed_steps'])
        for s_name in ('human_approved','committed_to_memory'):
            if s_name not in done: done.append(s_name)
        checkpoint['approval_resolution']={'decision':'approved','memory_id':rec_mid,'operator_note':rec_note,'resolved_at':now(),'recovered_from_crash':True,'orders':'disabled'}
        _save_run(run_id, state['workflow_id'], 'completed', 'committed_to_memory', done, False, state['inputs_meta'], checkpoint, state.get('proposed_action'), True)
        return {'run_id':run_id,'status':'completed','recovered':True,'committed_memory_id':rec_mid,'orders':'disabled'}
    if state['status']=='completed' and (checkpoint.get('approval_resolution') or {}).get('decision')=='approved':
        res=checkpoint['approval_resolution']
        return {'run_id':run_id,'status':'completed','idempotent':True,'committed_memory_id':res.get('memory_id'),'orders':'disabled'}
    if state['status'] not in {'awaiting_approval','approving'} or not state.get('proposed_action'):
        raise ValueError('workflow run is not awaiting approval')
    prop=state['proposed_action']

    # TTL expiration check
    ttl_hours=float(quant.load_policy().get('approval_ttl_hours', 48))
    try:
        staged_dt=datetime.fromisoformat(state['updated_at'])
        age_hours=(datetime.now(timezone.utc)-staged_dt.astimezone(timezone.utc)).total_seconds()/3600.0
        if age_hours>ttl_hours:
            raise ValueError(f'approval expired ({age_hours:.1f}h > {ttl_hours}h TTL); re-run workflow')
    except ValueError as ve:
        if 'approval expired' in str(ve): raise

    # Hard gate & data-version re-validation for trade plans
    if prop['kind']=='approved_trade_plan':
        if not state.get('acceptance_passed') or prop.get('gate_verdict') not in {'clear','caution_review_flags'}:
            raise ValueError(f"cannot approve blocked trade plan (gate_verdict={prop.get('gate_verdict')}); resolve gate issues and re-run")
        re_gate=decision.evaluate_trade_gate(prop['symbol'], prop['action'], prop['amount_base'], prop['account'])
        re_verd=re_gate.get('verdict') or re_gate.get('status','blocked')
        if re_verd not in {'clear','caution_review_flags'}:
            raise ValueError(f'approval blocked on live re-validation: {re_verd}')
        staged_pol=prop.get('staged_policy_hash')
        if staged_pol and re_gate.get('policy_hash') and staged_pol!=re_gate.get('policy_hash'):
            raise ValueError('investment policy changed since workflow staging; re-run workflow before approving')
        staged_pos=prop.get('staged_positions_hash')
        if staged_pos and staged_pos!=_positions_hash():
            raise ValueError('portfolio positions or NAV changed since workflow staging; re-run workflow before approving')
        staged_nav=prop.get('staged_portfolio_nav')
        cur_nav=re_gate.get('portfolio_nav')
        if staged_nav and cur_nav:
            try:
                if abs(float(cur_nav)-float(staged_nav))/max(1.0, float(staged_nav))>0.05:
                    raise ValueError('portfolio NAV shifted >5% since workflow staging; re-run workflow before approving')
            except ValueError as ve:
                if 'shifted >5%' in str(ve): raise

    # Atomic CAS intent claim via approval_intents + workflow_runs (or resume approving-NULL-memory recovery)
    stamp=now()
    if not recovering_null_intent:
        with db('ops') as c:
            schema(c)
            cur=c.execute("UPDATE workflow_runs SET status='approving', updated_at=? WHERE run_id=? AND status='awaiting_approval'",(stamp, str(run_id)))
            if cur.rowcount==0:
                intent=c.execute('SELECT * FROM approval_intents WHERE run_id=?',(str(run_id),)).fetchone()
                if intent and intent['decision']=='approved':
                    return {'run_id':run_id,'status':'completed','idempotent':True,'committed_memory_id':intent['memory_id'],'orders':'disabled'}
                raise ValueError('workflow run already claimed or no longer awaiting approval')
            c.execute("INSERT OR IGNORE INTO approval_intents(run_id,decision,operator_note,memory_id,created_at,updated_at) VALUES(?,?,?,?,?,?)",(str(run_id),'approving',operator_note,None,stamp,stamp))

    memory_id=None
    try:
        if prop['kind']=='save_journal':
            memory_id=knowledge.save_journal(prop['symbol'], prop['thesis'], 'workflow_human_approved', alternatives=operator_note, risk=prop.get('falsifier') or '', confidence=prop.get('confidence') or '', approval_run_id=str(run_id))
        elif prop['kind']=='approved_trade_plan':
            plan_thesis=f"[{prop['action']} {prop['amount_base']} base in {prop['account']} | gate={prop['gate_verdict']}] {prop['thesis']}"
            if operator_note: plan_thesis+=' | Note: '+operator_note
            memory_id=knowledge.save_journal(prop['symbol'], plan_thesis, 'workflow_human_approved', risk=prop.get('falsifier') or '', confidence=prop.get('confidence') or '', approval_run_id=str(run_id))
        elif prop['kind']=='save_lesson':
            detail=prop['detail']+(' | Operator note: '+operator_note if operator_note else '')
            memory_id=knowledge.save_lesson(prop['lesson_kind'], prop['title'], detail, 'workflow_human_approved', symbol=prop.get('symbol') or '', action_rule=prop.get('action_rule') or '', approval_run_id=str(run_id))
        else:
            raise ValueError(f"unsupported proposed_action kind: {prop.get('kind')}")
        with db('ops') as c:
            c.execute("UPDATE approval_intents SET memory_id=?, decision='approved', updated_at=? WHERE run_id=?",(memory_id, now(), str(run_id)))
    except Exception:
        with db('ops') as c:
            c.execute("UPDATE workflow_runs SET status='awaiting_approval', updated_at=? WHERE run_id=? AND status='approving'",(now(), str(run_id)))
            c.execute("DELETE FROM approval_intents WHERE run_id=? AND decision='approving' AND memory_id IS NULL",(str(run_id),))
        raise
    done=list(state['completed_steps'])+['human_approved','committed_to_memory']
    checkpoint['approval_resolution']={'decision':'approved','memory_id':memory_id,'operator_note':operator_note,'resolved_at':now(),'orders':'disabled'}
    _save_run(run_id, state['workflow_id'], 'completed', 'committed_to_memory', done, False, state['inputs_meta'], checkpoint, prop, True)
    return {'run_id':run_id,'status':'completed','committed_memory_id':memory_id,'orders':'disabled'}

def reject_workflow(run_id, reason='rejected_by_operator'):
    """Human-in-the-Loop Rejection: atomically claims workflow_runs + approval_intents without modifying memory or ledger."""
    state=get_workflow_run(run_id)
    if state.get('status')=='not_found': raise ValueError('workflow run not found')
    stamp=now()
    with db('ops') as c:
        schema(c)
        cur=c.execute("UPDATE workflow_runs SET status='rejected', current_step='human_rejected', pending_approval=0, updated_at=? WHERE run_id=? AND status='awaiting_approval'",(stamp, str(run_id)))
        if cur.rowcount==0:
            raise ValueError('workflow run is not awaiting approval or already claimed')
        c.execute("INSERT OR IGNORE INTO approval_intents(run_id,decision,operator_note,memory_id,created_at,updated_at) VALUES(?,?,?,?,?,?)",(str(run_id),'rejected',reason,None,stamp,stamp))
    done=list(state['completed_steps'])+['human_rejected']
    checkpoint=dict(state['checkpoint'])
    checkpoint['approval_resolution']={'decision':'rejected','reason':reason,'resolved_at':stamp}
    _save_run(run_id, state['workflow_id'], 'rejected', 'human_rejected', done, False, state['inputs_meta'], checkpoint, state.get('proposed_action'), False)
    return {'run_id':run_id,'status':'rejected','reason':reason}

def run_eval_benchmark():
    """Subprocess-isolated Deterministic Evaluation Harness: never mutates caller os.environ or loaded modules."""
    out=subprocess.check_output(
        [sys.executable, '-c', 'import json; from personal_agent import ops; print(json.dumps(ops._run_eval_in_process()))'],
        cwd=str(ROOT), text=True
    )
    res=json.loads(out.strip().splitlines()[-1])
    with db('ops') as c:
        schema(c)
        c.execute('INSERT OR REPLACE INTO eval_runs VALUES(?,?,?,?,?,?)',(res['eval_id'],res['passed'],res['total'],res['score_pct'],json.dumps(res['cases'],ensure_ascii=False),now()))
    return res

def _run_eval_in_process():
    """Inner eval suite executed inside an isolated subprocess."""
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
            record_case('counterfactual_buy_and_hold_wealth_identity', ident_ok, cf.get('verdict'))
            drv=quant.driver_based_valuation('1000','0.15','0.25',shares='100')
            record_case('driver_based_3scenario_valuation', drv['scenarios']['bear']['per_share']<drv['probability_weighted_per_share']<drv['scenarios']['bull']['per_share'], drv['probability_weighted_per_share'])
            bsm_ref=quant.analyze_option_strategy('covered_call','100','110',45,volatility='0.30',cost_basis='95')
            iv_inv=quant.analyze_option_strategy('covered_call','100','110',45,premium=str(bsm_ref['option_premium_per_share']),cost_basis='95')
            record_case('black_scholes_iv_inversion_and_covered_call', abs(iv_inv['solved_iv_from_premium']-0.30)<0.003, iv_inv['solved_iv_from_premium'])
            lev_sim=quant.simulate_leveraged_etf('0.10','0.30','3.0',years=3)
            record_case('leveraged_etf_variance_drain_decomposition', lev_sim['analytical_continuous_approx']['annual_volatility_variance_drain']==0.27, lev_sim['analytical_continuous_approx']['annual_volatility_variance_drain'])
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

def record_scheduled_job(job_name, schedule_window, status, summary=None):
    """v0.6.0 A03: Persist scheduled job execution state for Task Scheduler / CLI."""
    jid=f'{job_name}:{schedule_window}'
    with db('ops') as c:
        schema(c)
        c.execute('INSERT OR REPLACE INTO scheduled_jobs VALUES(?,?,?,?,?,?)',(jid,str(job_name),str(schedule_window),str(status),json.dumps(summary or {},ensure_ascii=False,default=str),now()))
    return {'job_id':jid,'status':status}

def emit_alert(dedup_key, symbol, severity, title, detail, evidence_ids=None):
    """v0.6.0 A03: Deduplicated local alert emission with hit_count tracking."""
    stamp=now(); key=str(dedup_key).strip()
    if not key or not title: raise ValueError('dedup_key and title required')
    with db('ops') as c:
        schema(c)
        old=c.execute('SELECT hit_count FROM alerts WHERE dedup_key=?',(key,)).fetchone()
        if old:
            c.execute('UPDATE alerts SET hit_count=hit_count+1, last_seen_at=? WHERE dedup_key=?',(stamp,key))
            return {'dedup_key':key,'deduplicated':True,'hit_count':old['hit_count']+1}
        c.execute('INSERT INTO alerts VALUES(?,?,?,?,?,?,?,?,?,?)',(key,str(symbol or '').upper(),str(severity),str(title),str(detail),json.dumps(evidence_ids or []),0,1,stamp,stamp))
    return {'dedup_key':key,'deduplicated':False,'hit_count':1}

def list_alerts(include_ack=False, limit=30):
    with db('ops') as c:
        schema(c)
        q='SELECT * FROM alerts'+('' if include_ack else ' WHERE acknowledged=0')+' ORDER BY last_seen_at DESC LIMIT ?'
        rows=[dict(r) for r in c.execute(q,(bounded(limit),))]
    for r in rows: r['evidence_ids']=json.loads(r.pop('evidence_ids_json'))
    return rows

def acknowledge_alert(dedup_key):
    with db('ops') as c:
        schema(c)
        cur=c.execute('UPDATE alerts SET acknowledged=1 WHERE dedup_key=?',(str(dedup_key),))
    return {'dedup_key':dedup_key,'acknowledged':cur.rowcount>0}

def shadow_mode_check():
    """v1.0 P02: Fail-closed operational readiness check across Flex freshness, Broker reconciliation, IPS coverage, workflow health, and Evals."""
    from . import broker
    snap=finance.load_full_portfolio_snapshot()
    rec=broker.reconcile_live_to_flex()
    pol=quant.evaluate_policy(snap, mode='AUDIT')
    ev_runs=list_eval_runs(1)
    with db('ops') as c:
        schema(c)
        pending=c.execute("SELECT count(*) FROM workflow_runs WHERE status='awaiting_approval'").fetchone()[0]
        stuck=c.execute("SELECT count(*) FROM workflow_runs WHERE status='approving'").fetchone()[0]
    brk_snap=broker.latest_broker_snapshot()
    eval_ok=bool(ev_runs and ev_runs[0]['score_pct']==100.0)
    flex_ok=bool(snap.advice_allowed)
    broker_ok=(rec.get('status')=='reconciled') and (brk_snap.get('adapter')=='ibkr_tws_readonly')
    pol_ok=(pol.get('status') not in {'blocked','blocked_policy_violation','invalid_nav'}) and bool(pol.get('risk_coverage_complete'))
    ready=flex_ok and broker_ok and pol_ok and stuck==0 and eval_ok
    blockers=[]
    if not flex_ok: blockers.append(f'flex:{snap.status}')
    if not broker_ok: blockers.append(f"broker:{rec.get('status')}:{brk_snap.get('adapter','none')}")
    if not pol_ok: blockers.append(f"policy:{pol.get('status')}")
    if stuck>0: blockers.append(f'stuck_approving:{stuck}')
    if not eval_ok: blockers.append('eval_not_100pct')
    return {
        'status':'ok' if ready else 'blocked',
        'production_accepted':False,
        'readiness_blockers':blockers,
        'flex_status':snap.status,
        'flex_snapshot_id':snap.snapshot_id,
        'broker_reconciliation_status':rec.get('status'),
        'policy_audit_status':pol.get('status'),
        'risk_coverage_complete':pol.get('risk_coverage_complete'),
        'pending_approvals':pending,
        'stuck_approving_runs':stuck,
        'latest_eval_score_pct':ev_runs[0]['score_pct'] if ev_runs else None,
        'orders':'disabled',
        'limitations':['20-day live shadow-mode window and real IBKR TWS socket permissions must be validated on the operator host before production acceptance.']
    }

def run_daily_monitoring():
    """v0.6.0 A03: Run deterministic opportunity discovery + policy audit and emit deduplicated alerts."""
    disc=decision.discover_opportunities()
    pol=quant.evaluate_policy(mode='AUDIT')
    emitted=[]
    for b in (pol.get('hard_blocks') or []):
        emitted.append(emit_alert(f"policy:{b['rule']}", b.get('symbol',''), 'high', f"IPS Hard Block: {b['rule']}", str(b.get('detail') or b)))
    for c in (disc.get('candidates') or []):
        emitted.append(emit_alert(f"opp:{c['symbol']}", c['symbol'], 'info', f"Opportunity Candidate: {c['symbol']}", f"score={c['composite_score']} roic={c['roic']} disc={c['valuation_discount_pct']}"))
    record_scheduled_job('daily_monitoring', now()[:10], 'ok', {'candidates':len(disc.get('candidates') or []),'alerts':len(emitted)})
    return {'status':'ok','discovery_status':disc.get('status'),'alerts_processed':len(emitted),'orders':'disabled'}
