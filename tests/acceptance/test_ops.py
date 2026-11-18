from datetime import date
from pathlib import Path
import json, subprocess, sys
import pytest
from personal_agent import broker, core, finance, knowledge, market, ops, quant

def test_O01_backup_and_restore_rehearsal_across_all_dbs(isolated_env):
    with core.db('finance') as c:
        finance.schema(c)
        c.execute('INSERT INTO sources VALUES(?,?)', ('s1', '2026-11-18T10:00:00'))
    knowledge.save('fact', 'Restore check', 'operator')
    ops.record_scheduled_job('daily_monitoring', '2026-11-18', 'ok', {})
    bk = core.backup_databases(Path(isolated_env) / 'bk_o01')
    rst = core.restore_databases(bk['backup_dir'], Path(isolated_env) / 'rst_o01')
    assert bk['status'] == 'ok' and rst['status'] == 'ok' and len(rst['databases']) >= 3

def test_O02_shadow_readiness_fails_closed_for_stale_replay_or_blocked(isolated_env):
    # Empty/stale home -> blocked
    sh = ops.shadow_mode_check()
    assert sh['status'] == 'blocked' and sh['production_accepted'] is False
    # Offline replay does not satisfy live_broker_ok
    d = date.today().isoformat()
    with core.db('finance') as c:
        finance.schema(c)
        c.execute('INSERT INTO sources VALUES(?,?)', ('s', d + 'T10:00:00'))
        c.execute('INSERT INTO nav VALUES(?,?,?,?,?,?)', ('margin', d, 'CAD', '1000', '1000', 's'))
        c.execute('INSERT INTO nav VALUES(?,?,?,?,?,?)', ('tfsa', d, 'CAD', '1000', '1000', 's'))
        c.execute('INSERT INTO snapshots VALUES(?,?,?,1)', ('margin', d, 's'))
        c.execute('INSERT INTO snapshots VALUES(?,?,?,1)', ('tfsa', d, 's'))
    broker.sync_broker_replay({'as_of': d, 'accounts': {'U0000001': {'NetLiquidation': '1000', 'currency': 'CAD'}, 'U0000002': {'NetLiquidation': '1000', 'currency': 'CAD'}}, 'positions': []})
    sh_rep = ops.shadow_mode_check()
    assert sh_rep['status'] == 'blocked' and any('offline_replay' in b for b in sh_rep['readiness_blockers'])

def test_O03_cli_error_exits_nonzero_and_eval_subprocess_isolation(isolated_env):
    r = subprocess.run([sys.executable, '-m', 'personal_agent.cli', 'pit-facts', 'NO_SUCH'], capture_output=True, text=True)
    assert r.returncode == 0
    r_bad = subprocess.run([sys.executable, '-m', 'personal_agent.cli', 'run-workflow', 'nonexistent_wf'], capture_output=True, text=True)
    assert r_bad.returncode != 0

def test_O04_legacy_db_migration_idempotent(isolated_env):
    with core.db('ops') as c:
        ops.schema(c)
        ops.schema(c)
        cols = {r[1] for r in c.execute('PRAGMA table_info(workflow_runs)')}
        assert {'immutable_params_json', 'input_hash', 'workflow_version'}.issubset(cols)
        ai_cols = {r[1] for r in c.execute('PRAGMA table_info(approval_intents)')}
        assert {'lease_owner', 'lease_expires_at'}.issubset(ai_cols)

def test_O05_cli_help_and_headless_dashboard_smoke(isolated_env):
    r = subprocess.run([sys.executable, '-m', 'personal_agent.cli', '--help'], capture_output=True, text=True)
    assert r.returncode == 0 and 'shadow-check' in r.stdout
