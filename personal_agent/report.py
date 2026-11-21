"""Deterministic local report and token-optimal daily brief. A Flex snapshot is not a market update."""
from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path
from .core import home, private_dir
from . import market
from .decision import exposure, improvement_loop, risk, tax_positions
from .finance import dec, performance, portfolio

def daily_brief():
    """Compact ~900-token morning digest for 32K local LLM context efficiency."""
    snap=portfolio()
    if 'nav' not in snap:
        return {'status':'blocked','reason':snap.get('status','unavailable'),'orders':'disabled'}
    perf=performance()
    measured=risk()
    tax=tax_positions()
    loop=improvement_loop()
    quotes=market.quote_board()
    nav=dec(snap['nav'])
    cash_sum=sum((dec(a['cash']) for a in snap.get('accounts',[]) if a.get('cash') not in (None,'')), Decimal(0))
    top_holdings=[{'symbol':p['symbol'],'conid':p['conid'],'weight_of_nav':str(round(dec(p['weight_of_nav']),4)) if p.get('weight_of_nav') else None,'value_base':str(round(dec(p['total_value_base']),2)),'unrealized_pl_base':str(round(dec(p['unrealized_pl_base_statement_fx']),2)) if p.get('unrealized_pl_base_statement_fx') is not None else None,'superficial_loss_watch':p['superficial_loss_watch']} for p in (tax.get('positions') or [])[:6]]
    harvest=[]
    for p in tax.get('positions') or []:
        for lot in p.get('lots') or []:
            if lot.get('tax_bucket')=='taxable' and lot.get('unrealized_pl_base_statement_fx') is not None and dec(lot['unrealized_pl_base_statement_fx'])<0:
                harvest.append({'symbol':p['symbol'],'account':lot['account'],'unrealized_pl_base':str(round(dec(lot['unrealized_pl_base_statement_fx']),2)),'superficial_loss_watch':p['superficial_loss_watch']})
    harvest.sort(key=lambda r:dec(r['unrealized_pl_base']))
    today=date.today(); horizon=(today+timedelta(days=14)).isoformat(); today_iso=today.isoformat()
    upcoming_earnings=[]
    for sym in market.universe():
        for ev in market.earnings(sym):
            if today_iso<=(ev.get('report_date') or '')<=horizon:
                upcoming_earnings.append({'symbol':sym,'report_date':ev['report_date'],'hour':ev.get('hour')})
    upcoming_earnings.sort(key=lambda r:r['report_date'])
    return {
        'status':snap['status'],
        'as_of':snap['as_of'],
        'advice_allowed':snap.get('advice_allowed'),
        'currency':snap['currency'],
        'capital':{'combined_nav':snap['nav'],'cash_base':str(round(cash_sum,2)),'cash_weight_of_nav':str(round(cash_sum/nav,4)) if nav>0 else None,'accounts':[{'account':a['account'],'nav':a['nav'],'cash':a['cash']} for a in snap['accounts']],'twr_daily_approx':perf.get('twr'),'xirr':perf.get('xirr')},
        'risk_summary':{'max_drawdown':measured.get('max_drawdown'),'var_95':measured.get('var_95'),'sharpe':measured.get('sharpe'),'sharpe_source':measured.get('sharpe_source'),'fx_usdcad':(market.fx() or {}).get('rate'),'macro_dgs3mo':(market.macro() or {}).get('value')},
        'top_holdings':top_holdings,
        'tax_watchlist':{'top_loss_harvest_candidates':harvest[:5],'superficial_loss_watch_symbols':[p['symbol'] for p in (tax.get('positions') or []) if p.get('superficial_loss_watch')]},
        'market_radar':{'monitored_symbols':len(quotes),'live_quotes':sum(1 for q in quotes if q.get('freshness')=='live'),'non_live_quotes':sum(1 for q in quotes if q.get('freshness')!='live'),'upcoming_earnings_14d':upcoming_earnings[:10]},
        'recursive_coaching':{'next_focus':(loop.get('recursive_next_focus') or [])[:6],'active_goals_count':len(loop.get('active_goals') or []),'past_mistakes_count':len(loop.get('past_mistakes') or [])},
        'orders':'disabled',
        'limitations':['Pre-computed digest for fast LLM grounding. Non-live quotes are never substituted for live prices. Daily-approximation TWR is not exact TWR.']
    }

def daily_report():
    snap=portfolio()
    if snap.get('status')!='ok':
        return {'status':'blocked','reason':snap.get('status','unavailable'),'as_of':snap.get('as_of'),'kept_prior_snapshot':True,'market_update':None,'orders':'disabled','limitations':['The report keeps the last snapshot and does not infer a market move from missing Flex data.']}
    quotes=market.quote_board()
    body={'status':'ok','as_of':snap['as_of'],'currency':snap['currency'],'advice_allowed':snap.get('advice_allowed'),'accounts':[a['account'] for a in snap['accounts']],'nav':snap['nav'],'brief':daily_brief(),'exposure':exposure(),'risk':risk(),'quotes':quotes,'quote_freshness':{'live':sum(1 for row in quotes if row.get('freshness')=='live'),'not_live':sum(1 for row in quotes if row.get('freshness')!='live')},'macro':market.macro(),'fx':market.fx(),'benchmark':'unavailable','market_events':'cached_only','orders':'disabled','limitations':['Flex facts and cached market data are separate. A quote that is not live is not treated as the current price. No order is created.']}
    return body

def write_report():
    body=daily_report()
    folder=private_dir(Path(home())/'reports')
    stamp=body.get('as_of') or 'blocked'
    path=folder/(stamp+'.json')
    path.write_text(json.dumps(body,ensure_ascii=False,indent=2),encoding='utf-8')
    if body.get('brief'):
        (folder/(stamp+'.brief.json')).write_text(json.dumps(body['brief'],ensure_ascii=False,indent=2),encoding='utf-8')
    return {'status':body['status'],'path':str(path),'as_of':body.get('as_of'),'orders':'disabled','benchmark':'unavailable'}
