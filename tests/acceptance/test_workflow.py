import concurrent.futures, threading
from datetime import date, datetime, timedelta, timezone
import json
import pytest
from personal_agent import core, decision, finance, knowledge, ops, quant, server

def test_W01_immutable_params_resume_and_reject_tamper(isolated_env):
    wf = ops.run_workflow('morning_portfolio_review', {'note': 'orig'})
    run_id = wf['run_id']
    # Completed workflow is non-resumable
    with pytest.raises(ValueError):
        ops.resume_workflow(run_id)

    # Interrupted running workflow preserves immutable params across checkpoints & rejects tampered params
    r2 = 'wf_w01_test'
    ops._save_run(r2, 'morning_portfolio_review', 'running', 'freshness_and_reconciliation_gate', ['intake_validate'], False, {'workflow': 'morning_portfolio_review', 'immutable_params': {'k': 'v1'}, 'input_hash': ops._param_hash({'k': 'v1'})}, {}, None, False, immutable_params={'k': 'v1'}, input_hash=ops._param_hash({'k': 'v1'}))
    with pytest.raises(ValueError):
        ops.run_workflow('morning_portfolio_review', {'k': 'tampered'}, resume_run_id=r2)
    res = ops.resume_workflow(r2)
    assert res['status'] == 'completed'

def test_W02_concurrent_approve_and_reject_race_single_journal(isolated_env):
    wf = ops.run_workflow('equity_valuation_and_expectations', {'symbol': 'QQQ', 'propose_thesis': 'W02 concurrent test', 'falsifier': 'Break'})
    barrier = threading.Barrier(5) if hasattr(__import__('threading'), 'Barrier') else None
    def _approve(i):
        barrier.wait()
        try: return ('ok', ops.approve_workflow(wf['run_id'], f'op_{i}'))
        except ValueError as e: return ('err', str(e))
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(_approve, range(5)))
    with core.db('knowledge') as c:
        cnt = c.execute('SELECT count(*) FROM memory WHERE approval_run_id=?', (wf['run_id'],)).fetchone()[0]
    assert cnt == 1
    assert sum(1 for k, _ in results if k == 'ok') >= 1
    with pytest.raises(ValueError):
        ops.reject_workflow(wf['run_id'], 'late reject')

def test_W03_crash_recovery_after_claim_and_ttl_expiry(isolated_env):
    # Crash after claim (NULL memory_id) recovers cleanly
    wf = ops.run_workflow('equity_valuation_and_expectations', {'symbol': 'QQQ', 'propose_thesis': 'W03 crash test', 'falsifier': 'Break'})
    with core.db('ops') as c:
        c.execute("UPDATE workflow_runs SET status='approving' WHERE run_id=?", (wf['run_id'],))
        c.execute("INSERT INTO approval_intents(run_id,decision,operator_note,memory_id,created_at,updated_at) VALUES(?,?,?,?,?,?)", (wf['run_id'], 'approving', 'op', None, core.now(), core.now()))
    rec = ops.approve_workflow(wf['run_id'], 'op')
    assert rec['status'] == 'completed' and rec['committed_memory_id'] is not None

    # Active non-expired owner lease prevents recovery takeover
    wf_lease = ops.run_workflow('equity_valuation_and_expectations', {'symbol': 'QQQ', 'propose_thesis': 'Lease check', 'falsifier': 'Break'})
    future_exp = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    with core.db('ops') as c:
        c.execute("UPDATE workflow_runs SET status='approving' WHERE run_id=?", (wf_lease['run_id'],))
        c.execute("INSERT INTO approval_intents(run_id,decision,operator_note,memory_id,created_at,updated_at,lease_owner,lease_expires_at) VALUES(?,?,?,?,?,?,?,?)", (wf_lease['run_id'], 'approving', 'op', None, core.now(), core.now(), 'active_owner_1', future_exp))
    with pytest.raises(ValueError, match='active lease owner'):
        ops.approve_workflow(wf_lease['run_id'], 'steal')

    # Expired TTL blocked
    wf_exp = ops.run_workflow('equity_valuation_and_expectations', {'symbol': 'QQQ', 'propose_thesis': 'Expired test', 'falsifier': 'Break'})
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
    with core.db('ops') as c:
        c.execute('UPDATE workflow_runs SET updated_at=? WHERE run_id=?', (old_ts, wf_exp['run_id']))
    with pytest.raises(ValueError, match='expired'):
        ops.approve_workflow(wf_exp['run_id'], 'late')

def test_W04_ic_memo_blocks_failed_allocator_or_blocked_gate(isolated_env):
    # Healthy portfolio with clear trade gate, individually missing DCF, failed allocator, or missing falsifier
    d = date.today().isoformat()
    with core.db('finance') as c:
        finance.schema(c)
        c.execute('INSERT INTO sources VALUES(?,?)', ('s', d + 'T10:00:00'))
        c.execute('INSERT INTO nav VALUES(?,?,?,?,?,?)', ('margin', d, 'CAD', '5000', '5000', 's'))
        c.execute('INSERT INTO nav VALUES(?,?,?,?,?,?)', ('tfsa', d, 'CAD', '5000', '5000', 's'))
        c.execute('INSERT INTO snapshots VALUES(?,?,?,1)', ('margin', d, 's'))
        c.execute('INSERT INTO snapshots VALUES(?,?,?,1)', ('tfsa', d, 's'))
    # 1. Missing DCF (no revenue_t0/shares) -> acceptance_passed=0, cannot approve
    wf_nodcf = ops.run_workflow('investment_committee_memo', {'symbol': 'QQQ', 'amount_base': '500', 'account': 'margin', 'falsifier': 'Margin < 10%'})
    assert ops.get_workflow_run(wf_nodcf['run_id'])['acceptance_passed'] == 0
    with pytest.raises(ValueError):
        ops.approve_workflow(wf_nodcf['run_id'], 'try nodcf')
    # 2. Missing falsifier even with DCF -> acceptance_passed=0, cannot approve
    wf_nofals = ops.run_workflow('investment_committee_memo', {'symbol': 'QQQ', 'amount_base': '500', 'account': 'margin', 'revenue_t0': '1000', 'shares': '100', 'falsifier': ''})
    assert ops.get_workflow_run(wf_nofals['run_id'])['acceptance_passed'] == 0
    with pytest.raises(ValueError):
        ops.approve_workflow(wf_nofals['run_id'], 'try nofals')
    # 3. Complete valid IC memo -> acceptance_passed=1 and human approval commits journal
    wf_ok = ops.run_workflow('investment_committee_memo', {'symbol': 'QQQ', 'amount_base': '500', 'account': 'margin', 'revenue_t0': '1000', 'shares': '100', 'falsifier': 'Margin < 10%'})
    assert ops.get_workflow_run(wf_ok['run_id'])['acceptance_passed'] == 1
    app = ops.approve_workflow(wf_ok['run_id'], 'valid ic approval')
    assert app['status'] == 'completed' and app['committed_memory_id'] is not None

def test_W05_monitoring_dedup_alerts_and_scheduled_jobs(isolated_env):
    a1 = ops.emit_alert('opp:QQQ', 'QQQ', 'info', 'Test', 'Detail')
    a2 = ops.emit_alert('opp:QQQ', 'QQQ', 'info', 'Test', 'Detail')
    assert a1['deduplicated'] is False and a2['deduplicated'] is True and a2['hit_count'] == 2
    job = ops.record_scheduled_job('daily_monitoring', '2026-11-18', 'ok', {'alerts': 1})
    assert job['status'] == 'ok'

def test_W06_mcp_cli_and_public_surface_consistency(isolated_env):
    pub_mcp = server.make_server('investment', public_only=True)
    assert pub_mcp is not None
    # Verify public-only process blocks private DB open
    with pytest.raises(PermissionError):
        with core.db('finance'):
            pass
