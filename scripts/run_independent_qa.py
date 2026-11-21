#!/usr/bin/env python3
"""Run baseline + existing contract + additional real-scenario oracles.

Run from IBKR-Agent-harness root; this writes only to validation/evidence/independent/
plus existing validation/ evidence from the preexisting release gate.
This does not connect to a real IBKR account or certify production readiness.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET


def hash_file(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_command(root: Path, out: Path, name: str, command: list[str], timeout: int):
    log = out / f'{name}.log'
    started = datetime.now(timezone.utc).isoformat()
    # Keep OS/home identity for Path.home()/Streamlit AppTest; scrub secrets and API keys.
    allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TMP', 'TEMP',
               'TMPDIR', 'LANG', 'LC_ALL', 'LC_CTYPE', 'SYSTEMDRIVE',
               'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE', 'VIRTUAL_ENV',
               'HOME', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'USERNAME', 'USER',
               'PYTHONPATH', 'PYTHONHOME'}
    safe_env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    safe_env['PYTHONNOUSERSITE'] = '1'
    with log.open('w', encoding='utf-8') as fh:
        fh.write('COMMAND: ' + ' '.join(command) + '\n')
        fh.flush()
        try:
            p = subprocess.run(command, cwd=root, stdout=fh, stderr=subprocess.STDOUT,
                               env=safe_env, timeout=timeout, check=False)
            rc = p.returncode
        except (OSError, subprocess.TimeoutExpired) as e:
            fh.write(f'ERROR: {type(e).__name__}: {e}\n')
            rc = 124
    return {'name': name, 'status': 'PASS' if rc == 0 else 'FAIL',
            'exit_code': rc, 'started_at': started,
            'finished_at': datetime.now(timezone.utc).isoformat(),
            'log': str(log.relative_to(root)), 'sha256': hash_file(log)}


def parse_junit(p: Path):
    if not p.exists():
        return {'total': 0, 'passed': 0, 'failed': 0, 'skipped': 0, 'errors': 0, 'cases': []}
    cases = []
    for node in ET.parse(p).getroot().iter('testcase'):
        status = ('FAIL' if node.find('failure') is not None
                  else ('ERROR' if node.find('error') is not None
                  else ('SKIP' if node.find('skipped') is not None else 'PASS')))
        message_node = node.find('failure')
        if message_node is None: message_node = node.find('error')
        cases.append({'id': node.attrib.get('name', ''), 'status': status,
                      'details': (message_node.attrib.get('message') or '')[:280] if message_node is not None else ''})
    return {'total': len(cases), 'passed': sum(c['status'] == 'PASS' for c in cases),
            'failed': sum(c['status'] == 'FAIL' for c in cases),
            'skipped': sum(c['status'] == 'SKIP' for c in cases),
            'errors': sum(c['status'] == 'ERROR' for c in cases),
            'cases': cases}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', default='.', help='Path to the target repository root')
    ap.add_argument('--timeout', default=900, type=int)
    ap.add_argument('--scenario-only', action='store_true', help='Skip baseline and acceptance contract')
    args = ap.parse_args()
    root = Path(args.root).resolve()
    suite = root / 'tests/independent/test_real_scenarios.py'
    if not (root/'personal_agent/finance.py').exists() or not suite.exists():
        ap.error('Run inside repo with tests/independent/test_real_scenarios.py installed')
    out = root/'validation/evidence/independent'; out.mkdir(parents=True, exist_ok=True)
    py = sys.executable
    steps = []
    if not args.scenario_only:
        steps.append(run_command(root, out, 'baseline', [py, 'scripts/verify.py'], args.timeout))
        steps.append(run_command(root, out, 'original_contract',
                                 [py, 'validation/release_gate.py', '--root', '.'], args.timeout))
    junit = out / 'scenarios.xml'
    steps.append(run_command(root, out, 'independent_scenarios',
                             [py, '-m', 'pytest', str(suite.relative_to(root)), '-vv', '-ra',
                              '-p', 'no:cacheprovider', f'--junitxml={junit}'], args.timeout))
    workflow = root / 'tests/independent/test_offline_workflows.py'
    wf_junit = out / 'workflows.xml'
    if workflow.exists():
        steps.append(run_command(root, out, 'offline_workflows',
                                 [py, '-m', 'pytest', str(workflow.relative_to(root)), '-vv', '-ra',
                                  '-p', 'no:cacheprovider', f'--junitxml={wf_junit}'], args.timeout))
    stats = parse_junit(junit)
    wf_stats = parse_junit(wf_junit) if workflow.exists() else {'total': 0, 'passed': 0, 'failed': 0, 'skipped': 0, 'errors': 0, 'cases': []}
    try:
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        head = None
    expected_ids = [f'test_R{i:02d}_' for i in range(1, 33)]
    missing = [x for x in expected_ids if not any(c['id'].startswith(x) for c in stats['cases'])]
    wf_ok = (wf_stats['total'] >= 7 and wf_stats['failed'] == wf_stats['errors'] == wf_stats['skipped'] == 0)
    is_ok = (all(s['status'] == 'PASS' for s in steps)
             and stats['total'] >= 32 and stats['failed'] == stats['errors'] == stats['skipped'] == 0
             and not missing and wf_ok)
    report = {
        'schema': 'independent_real_scenarios.v1', 'repo_sha': head,
        'result': 'SCENARIO_VERIFIED' if is_ok else 'BLOCKED',
        'offline_only': True, 'live_verified': False, 'production_accepted': False,
        'steps': steps, 'independent_scenarios': stats, 'offline_workflows': wf_stats,
        'missing_scenarios': missing,
        'source_suites': ['existing tests/acceptance', 'tests/independent/test_real_scenarios.py',
                          'tests/independent/test_offline_workflows.py'],
        'unresolved_blockers': [
            {'id': 'G2_LIVE_IBKR', 'status': 'BLOCKED_EXTERNAL',
             'reason': 'Real IBKR Gateway/TWS read-only connectivity must be run on the operator machine.'},
            {'id': 'G3_SHADOW_20', 'status': 'BLOCKED_EXTERNAL',
             'reason': '20 trading-session Shadow Mode has no operator session evidence.'},
        ],
        'limitations': [
            'No authenticated TWS/IBKR connection is made.',
            'Synthetic fixtures validate logic, not entitlement or real-market correctness.',
            'A green run is not evidence of alpha or successful trading.',
        ],
    }
    (out/'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    lines = [f"# Independent scenario QA — {report['result']}", '', f"Git SHA: `{head}`",
             f"Independent scenarios: {stats['passed']}/{stats['total']} passed; {stats['failed']} failed; {stats['errors']} errors; {stats['skipped']} skipped.",
             f"Offline workflows: {wf_stats['passed']}/{wf_stats['total']} passed; {wf_stats['failed']} failed.",
             '', '| ID | Status | Summary |', '|---|---|---|']
    lines += [f"| {c['id']} | {c['status']} | {c['details'].replace('|', '/')} |" for c in stats['cases']]
    lines += ['', '| Workflow | Status |', '|---|---|']
    lines += [f"| {c['id']} | {c['status']} |" for c in wf_stats['cases']]
    lines += ['', '## Unresolved blockers', '']
    lines += [f"- `{b['id']}` ({b['status']}): {b['reason']}" for b in report['unresolved_blockers']]
    lines += ['', '## Commands', ''] + [f"- `{s['name']}`: {s['status']}, log `{s['log']}`" for s in steps]
    lines += ['', 'No live or production acceptance is claimed.']
    (out/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(f"{report['result']}: {stats['passed']}/{stats['total']} real scenarios + {wf_stats['passed']}/{wf_stats['total']} workflows; report={out/'report.json'}")
    return 0 if is_ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
