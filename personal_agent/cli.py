import argparse,json,sys
from . import decision,finance,knowledge,market,ops,report
from .core import config

def main():
    p=argparse.ArgumentParser(); s=p.add_subparsers(dest='command',required=True)
    s.add_parser('index'); s.add_parser('portfolio'); s.add_parser('check-config')
    i=s.add_parser('import-flex'); i.add_argument('path')
    f=s.add_parser('sync-flex'); f.add_argument('--start'); f.add_argument('--end')
    m=s.add_parser('remember'); m.add_argument('kind',choices=sorted(knowledge.MEMORY_KINDS)); m.add_argument('content'); m.add_argument('--source',required=True); m.add_argument('--symbol',default=''); m.add_argument('--tags',default=''); m.add_argument('--supersedes',type=int)
    l=s.add_parser('lesson'); l.add_argument('kind',choices=['mistake','lesson','goal']); l.add_argument('title'); l.add_argument('detail'); l.add_argument('--source',default='operator'); l.add_argument('--symbol',default=''); l.add_argument('--tags',default=''); l.add_argument('--rule',default=''); l.add_argument('--supersedes',type=int)
    rm=s.add_parser('resolve-memory'); rm.add_argument('id',type=int); rm.add_argument('--note',default=''); rm.add_argument('--source',default='operator')
    s.add_parser('forget').add_argument('source')
    s.add_parser('performance')
    s.add_parser('brief')
    s.add_parser('habits')
    s.add_parser('factor-risk')
    at=s.add_parser('attribution'); at.add_argument('--days',type=int,default=90); at.add_argument('--account',default='')
    fd=s.add_parser('filing-delta'); fd.add_argument('symbol'); fd.add_argument('--sync',action='store_true')
    s.add_parser('health')
    s.add_parser('telemetry')
    s.add_parser('eval')
    s.add_parser('workflows')
    rw=s.add_parser('run-workflow'); rw.add_argument('workflow_id',choices=sorted(ops.WORKFLOW_SPECS)); rw.add_argument('--params',default='{}')
    ap=s.add_parser('approvals'); ap.add_argument('--status',default='awaiting_approval')
    aw=s.add_parser('approve'); aw.add_argument('run_id'); aw.add_argument('--note',default='')
    rjw=s.add_parser('reject'); rjw.add_argument('run_id'); rjw.add_argument('--reason',default='rejected_by_operator')
    ws=s.add_parser('web-search'); ws.add_argument('query'); ws.add_argument('--mode',default='auto',choices=['auto','web','news'])
    rv=s.add_parser('review'); rv.add_argument('--symbol',default='')
    s.add_parser('report')
    s.add_parser('market-sync')
    s.add_parser('market-stream')
    s.add_parser('import-bank').add_argument('path')
    j=s.add_parser('journal'); j.add_argument('instrument'); j.add_argument('thesis'); j.add_argument('--source',required=True); j.add_argument('--alternatives',default=''); j.add_argument('--risk',default=''); j.add_argument('--outcome',default=''); j.add_argument('--confidence',default=''); j.add_argument('--outcome-hit',default='')
    a=p.parse_args()
    try:
        if a.command=='index': result=knowledge.index()
        elif a.command=='portfolio': result=finance.portfolio()
        elif a.command=='import-flex': result=finance.import_xml(a.path)
        elif a.command=='sync-flex': result=finance.sync(a.start,a.end)
        elif a.command=='remember': result={'id':knowledge.save(a.kind,a.content,a.source,a.supersedes,a.symbol,a.tags)}
        elif a.command=='lesson': result={'id':knowledge.save_lesson(a.kind,a.title,a.detail,a.source,a.symbol,a.tags,a.rule,a.supersedes)}
        elif a.command=='resolve-memory': result=knowledge.resolve_memory(a.id,a.note,a.source)
        elif a.command=='forget': result=knowledge.forget_source(a.source)
        elif a.command=='performance': result=finance.performance()
        elif a.command=='brief': result=report.daily_brief()
        elif a.command=='habits': result=decision.trading_habits()
        elif a.command=='factor-risk': result=decision.portfolio_factor_risk()
        elif a.command=='attribution': result=decision.counterfactual_attribution(a.days, a.account or None)
        elif a.command=='filing-delta':
            if a.sync:
                market.sync_filings(a.symbol); market.sync_filing_sections(a.symbol)
            result=decision.filing_red_flag_audit(a.symbol, include_private=True)
        elif a.command=='health': result=market.system_health()
        elif a.command=='telemetry': result=ops.telemetry_summary()
        elif a.command=='eval': result=ops.run_eval_benchmark()
        elif a.command=='workflows': result=ops.list_workflows()
        elif a.command=='run-workflow': result=ops.run_workflow(a.workflow_id, a.params)
        elif a.command=='approvals': result=ops.list_workflow_runs(a.status or None)
        elif a.command=='approve': result=ops.approve_workflow(a.run_id, a.note)
        elif a.command=='reject': result=ops.reject_workflow(a.run_id, a.reason)
        elif a.command=='web-search': result=market.search_public_web(a.query, a.mode)
        elif a.command=='review': result=decision.improvement_loop(a.symbol or None)
        elif a.command=='report': result=report.write_report()
        elif a.command=='market-sync':
            raw=market.sync_all()
            result={'quotes':raw['quotes'],'symbols':len(raw['bars']),'macro':raw['macro'].get('status'),'fx':raw['fx'].get('status'),'filings':len(raw['filings']),'orders':'disabled'}
        elif a.command=='market-stream': result=market.stream()
        elif a.command=='journal': result={'id':knowledge.save_journal(a.instrument,a.thesis,a.source,a.alternatives,a.risk,a.outcome,a.confidence,a.outcome_hit)}
        elif a.command=='import-bank': result=finance.import_bank(a.path)
        elif a.command=='check-config':
            cfg=config(); result={'status':'readable','configured_accounts':len(cfg['finance']['accounts']),'knowledge_roots':len(cfg['knowledge_roots']),'memory':'explicit_cli'}
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except Exception as e:
        print(f'{type(e).__name__}: {e}',file=sys.stderr); return 1
    return 0
if __name__=='__main__': raise SystemExit(main())
