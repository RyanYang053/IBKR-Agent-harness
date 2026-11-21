#!/usr/bin/env python3
"""Independent local OFFLINE gate. Place with acceptance_manifest.json in validation/.
Run from a fresh venv: python validation/release_gate.py --root .
After commit (before push): python validation/release_gate.py --root . --verify-report
This runner does not implement the required tests or prove their assertions are adequate.
"""
from __future__ import annotations
import argparse, hashlib, json, os, platform, re, subprocess, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

ID_RE = re.compile(r'^test_([A-Z]\d{2})(?:_|\[|$)')
EXCLUDED = {'validation/report.json', 'validation/report.md'}

def sha(data): return hashlib.sha256(data).hexdigest()
def utc(): return datetime.now(timezone.utc).isoformat()
def git(root, *args): return subprocess.check_output(['git', '-C', str(root), *args])
def source_hash(root):
    paths = set(git(root,'ls-files','-z','--cached','--others','--exclude-standard').decode('utf-8').split('\0'))
    h=hashlib.sha256(); count=0
    for name in sorted(paths):
        if not name or name in EXCLUDED or name.startswith('validation/evidence/'): continue
        p=root/name
        if p.is_symlink(): blob=b'symlink\0'+os.readlink(p).encode('utf-8')
        elif p.is_file(): blob=p.read_bytes()
        elif not p.exists(): continue  # deleted files are absent from the candidate tree
        else: raise ValueError(f'unsupported candidate entry: {name}')
        h.update(name.encode('utf-8')+b'\0'+sha(blob).encode('ascii')+b'\n'); count+=1
    return h.hexdigest(), count

def load_manifest(path):
    raw=path.read_bytes(); m=json.loads(raw)
    cases=m['cases']; ids=[c['id'] for c in cases if c['mandatory'] and c['gate']=='G1']
    if not ids or len(ids)!=len(set(ids)) or any(not re.fullmatch(r'[A-Z]\d{2}', i) for i in ids):
        raise ValueError('invalid/missing mandatory acceptance IDs')
    return m,ids,sha(raw)

def inspect_junit(path, ids):
    records={i:[] for i in ids}; unknown=[]
    if path.is_file():
        for node in ET.parse(path).getroot().iter('testcase'):
            name=node.get('name',''); match=ID_RE.match(name)
            state='FAIL' if node.find('failure') is not None or node.find('error') is not None else ('SKIPPED' if node.find('skipped') is not None else 'PASS')
            rec={'node':node.get('classname','')+'::'+name,'status':state,'seconds':node.get('time','')}
            if match and match[1] in records: records[match[1]].append(rec)
            else: unknown.append(rec)
    results=[]
    for i, rr in records.items():
        state='NOT_RUN' if not rr else ('FAIL' if any(r['status']!='PASS' for r in rr) else 'PASS')
        results.append({'id':i,'status':state,'executed_cases':rr})
    return results,unknown

def run_step(root, out, label, command, env, timeout=900):
    path=out/f'{label}.log'; start=utc(); code=1
    with path.open('wb') as f:
        f.write(('COMMAND: '+subprocess.list2cmdline(command)+'\n').encode()); f.flush()
        try: code=subprocess.run(command,cwd=root,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=timeout,check=False).returncode
        except (OSError,subprocess.TimeoutExpired) as exc: f.write(('\nRUNNER ERROR: '+type(exc).__name__+'\n').encode())
    return {'name':label,'command':command,'exit_code':code,'started_at':start,'finished_at':utc(),'log':f'validation/evidence/{path.name}','sha256':sha(path.read_bytes())}

def verify_report(root,m,ids,mhash):
    p=root/'validation/report.json'; report=json.loads(p.read_text(encoding='utf-8'))
    digest,count=source_hash(root)
    if report.get('result')!='OFFLINE_VERIFIED' or report.get('manifest_sha256')!=mhash or report.get('source_sha256')!=digest or report.get('source_file_count')!=count: raise ValueError('report missing, failing, or stale source/manifest fingerprint')
    cases=report.get('cases',[])
    if sorted(c['id'] for c in cases)!=sorted(ids) or any(c['status']!='PASS' or not c.get('executed_cases') or any(r['status']!='PASS' for r in c['executed_cases']) for c in cases): raise ValueError('mandatory case failures or omissions')
    if report.get('production_accepted') is not False: raise ValueError('offline gate cannot claim production acceptance')
    required={'compile','dependencies','baseline','acceptance'}
    steps=report.get('steps',[])
    if len(steps)!=4 or {s['name'] for s in steps}!=required or any(s['exit_code']!=0 for s in steps): raise ValueError('required actual execution evidence missing')
    for s in steps:
        log=root/s['log']
        if not log.is_file() or sha(log.read_bytes())!=s['sha256']: raise ValueError('log missing or modified: '+s['name'])
    junit=root/'validation/evidence/acceptance.xml'
    if not junit.is_file() or sha(junit.read_bytes())!=report.get('junit_sha256'): raise ValueError('JUnit evidence missing or changed')
    actual,extra=inspect_junit(junit,ids)
    if actual!=cases or any(r['status']!='PASS' for r in extra): raise ValueError('JUnit and report mismatch or skipped extra test')
    print('OFFLINE_VERIFIED: report matches current source tree and evidence.'); return 0

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--root',default='.'); ap.add_argument('--manifest'); ap.add_argument('--verify-report',action='store_true'); ap.add_argument('--timeout',type=int,default=900); a=ap.parse_args()
    root=Path(a.root).resolve(); manifest=Path(a.manifest).resolve() if a.manifest else root/'validation/acceptance_manifest.json'
    m,ids,mhash=load_manifest(manifest)
    if a.verify_report: return verify_report(root,m,ids,mhash)
    out=root/'validation/evidence'; out.mkdir(parents=True,exist_ok=True)
    xml=out/'acceptance.xml'; xml.unlink(missing_ok=True)
    digest,count=source_hash(root); head=git(root,'rev-parse','HEAD').decode().strip()
    started=utc(); py=sys.executable; steps=[]
    # Do not inherit API tokens or operator configuration. Test code must independently
    # prevent outbound traffic (transport fakes/network guard) and avoid real account data.
    keys={'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TMP','TEMP','TMPDIR','LANG','LC_ALL','LC_CTYPE','SYSTEMDRIVE','NUMBER_OF_PROCESSORS','PROCESSOR_ARCHITECTURE','VIRTUAL_ENV'}
    env={k:v for k,v in os.environ.items() if k.upper() in keys}
    with tempfile.TemporaryDirectory(prefix='investment-os-gate-') as temp:
        tmp=Path(temp); cfg=tmp/'config.json'; cfg.write_text(json.dumps({'knowledge_roots':[],'finance':{'accounts':{},'base_currency':'CAD'},'policy':{'max_tool_rows':50},'market':{}}),encoding='utf-8')
        env.update({'PERSONAL_AGENT_HOME':str(tmp/'ledger'),'PERSONAL_AGENT_CONFIG':str(cfg),'HOME':temp,'USERPROFILE':temp,'PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1','PYTHONPYCACHEPREFIX':str(tmp/'pycache'),'INVESTMENT_OS_OFFLINE_TEST':'1'})
        steps.append(run_step(root,out,'compile',[py,'-m','compileall','-q','personal_agent','dashboard.py','scripts/verify.py','tests/acceptance'],env,a.timeout))
        steps.append(run_step(root,out,'dependencies',[py,'-m','pip','check'],env,a.timeout))
        steps.append(run_step(root,out,'baseline',[py,'scripts/verify.py'],env,a.timeout))
        suite=m.get('acceptance_suite','tests/acceptance')
        steps.append(run_step(root,out,'acceptance',[py,'-m','pytest',suite,'-q','--strict-markers','--strict-config','-p','no:cacheprovider',f'--junitxml={xml}'],env,a.timeout))
    cases,extra=inspect_junit(xml,ids)
    end_digest,end_count=source_hash(root)
    baseline_log=(out/'baseline.log').read_text(encoding='utf-8',errors='replace')
    unchanged=(digest,count)==(end_digest,end_count)
    all_pass=all(c['status']=='PASS' for c in cases) and all(c['status']=='PASS' for c in extra)
    pass_gate=unchanged and all_pass and all(s['exit_code']==0 for s in steps) and 'ALL ESSENTIAL CHECKS PASSED' in baseline_log
    report={'schema_version':1,'result':'OFFLINE_VERIFIED' if pass_gate else 'BLOCKED','production_accepted':False,'live_verified':False,'baseline_sha':m['baseline_sha'],'parent_head':head,'source_sha256':digest,'source_file_count':count,'source_unchanged_during_run':unchanged,'manifest_sha256':mhash,'started_at':started,'finished_at':utc(),'python':sys.version.splitlines()[0],'platform':platform.platform(),'steps':steps,'cases':cases,'extra_tests':extra,'junit_sha256':sha(xml.read_bytes()) if xml.is_file() else None,'mandatory_count':len(ids),'passed_count':sum(c['status']=='PASS' for c in cases),'baseline_success_marker': 'ALL ESSENTIAL CHECKS PASSED' in baseline_log,'operator_gates':['G2 real IBKR/SEC/Windows permissions not verified by offline gate','G3 documented shadow operation not verified by offline gate']}
    (root/'validation/report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    lines=['# Offline validation result',f"**{report['result']}** — {report['passed_count']}/{len(ids)} mandatory IDs passed.",f'Source fingerprint: `{digest}`',f'Parent HEAD: `{head}`','Production accepted: **false**.','', '| ID | Status | Test count |','|---|---|---:|']
    lines += [f"| {c['id']} | {c['status']} | {len(c['executed_cases'])} |" for c in cases]
    lines += ['', '| Step | Exit code |', '|---|---:|']+[f"| {s['name']} | {s['exit_code']} |" for s in steps]
    lines+=['',f'Source unchanged during run: {unchanged}.','See JSON, JUnit and raw logs for evidence. No live or production certification is claimed.']
    (root/'validation/report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(f"{report['result']}: {report['passed_count']}/{len(ids)} mandatory IDs passed. See validation/report.json")
    return 0 if pass_gate else 1

if __name__=='__main__':
    try: raise SystemExit(main())
    except (ValueError,OSError,KeyError,subprocess.CalledProcessError,ET.ParseError) as exc:
        print(f'GATE BLOCKED: {exc}',file=sys.stderr); raise SystemExit(1)
