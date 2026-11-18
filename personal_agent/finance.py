"""Conservative Flex snapshot ingestion. Raw statements retained, not exposed to LLM."""
import json, os, time, urllib.request, urllib.parse
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from .core import db, config, digest, private_dir, HOME, now, bounded, operator_env

def xml(data):
    from defusedxml.ElementTree import fromstring
    return fromstring(data)
def dec(x):
    if x is None or x=='': raise ValueError('required numeric field missing')
    d=Decimal(str(x))
    if not d.is_finite(): raise ValueError('nonfinite number')
    return d
def day(x):
    x=str(x).split(';',1)[0].split(' ',1)[0]
    return datetime.strptime(x,'%Y%m%d' if len(x)==8 else '%Y-%m-%d').date().isoformat()
def _stale(as_of,cfg):
    """Stale when the statement is older than the last completed business day, or past the configured calendar limit."""
    reported=date.fromisoformat(as_of)
    if (date.today()-reported).days>int(cfg.get('max_snapshot_age_days',4)): return True
    expected=date.today()-timedelta(days=1)
    while expected.weekday()>=5: expected-=timedelta(days=1)
    return reported<expected
def schema(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS nav(account TEXT, date TEXT, currency TEXT, total TEXT, cash TEXT, source TEXT, PRIMARY KEY(account,date));
    CREATE TABLE IF NOT EXISTS snapshots(account TEXT, date TEXT, source TEXT, complete INTEGER, PRIMARY KEY(account,date));
    CREATE TABLE IF NOT EXISTS positions(account TEXT, date TEXT, conid TEXT, symbol TEXT, asset TEXT, currency TEXT, quantity TEXT, value_base TEXT, PRIMARY KEY(account,date,conid));
    CREATE TABLE IF NOT EXISTS records(account TEXT, category TEXT, record_id TEXT, payload TEXT, source TEXT, PRIMARY KEY(account,category,record_id));
    CREATE TABLE IF NOT EXISTS imports(hash TEXT PRIMARY KEY, imported_at TEXT);
    CREATE TABLE IF NOT EXISTS sources(hash TEXT PRIMARY KEY, generated TEXT);
    CREATE TABLE IF NOT EXISTS nav_change(account TEXT, from_date TEXT, to_date TEXT, currency TEXT, payload TEXT, source TEXT, PRIMARY KEY(account,to_date));
    ''')
    for name, typedef in (('underlying_conid','TEXT'),('cost_basis','TEXT'),('fx_rate','TEXT')):
        have={row[1] for row in c.execute('PRAGMA table_info(positions)')}
        if name not in have: c.execute(f'ALTER TABLE positions ADD COLUMN {name} {typedef}')
def import_xml(path):
    data=Path(path).read_bytes(); h=digest(data); root=xml(data)
    if root.tag!='FlexQueryResponse': raise ValueError('not a Flex activity XML report')
    cfg=config()['finance']; accounts=cfg['accounts']; base=cfg['base_currency']; all_stmts=root.findall('./FlexStatements/FlexStatement')
    if not all_stmts: raise ValueError('Flex report has no statements')
    if not accounts or len(set(accounts.values()))!=len(accounts):
        found=sorted({s.get('accountId') or '' for s in all_stmts})
        raise ValueError('configure unique finance.accounts aliases for: '+', '.join(found))
    raw=private_dir(HOME/'raw'/'ibkr')/(h+'.xml'); raw.write_bytes(data)
    summary=[]
    with db('finance') as c:
        schema(c)
        if c.execute('SELECT 1 FROM imports WHERE hash=?',(h,)).fetchone(): return {'status':'already_imported','source':h}
        generated=max((s.get('whenGenerated') or '') for s in all_stmts)
        newer=c.execute('SELECT generated FROM sources WHERE generated>? LIMIT 1',(generated,)).fetchone() if generated else None
        if newer: return {'status':'kept_newer_snapshot','source':h}
        c.execute('INSERT OR REPLACE INTO sources VALUES(?,?)',(h,generated))
        for s in all_stmts:
            if s.get('accountId') not in accounts: raise ValueError('unexpected account; check configuration')
            a=accounts[s.get('accountId')]; d=day(s.get('toDate',''))
            # Accept only explicitly base-currency NAV; no implicit FX conversion.
            nav_rows=[]
            for n in s.iter('EquitySummaryByReportDateInBase'):
                if n.get('currency')!=base: raise ValueError('NAV currency mismatch')
                nd=day(n.get('reportDate','')); total=dec(n.get('total')); cash=n.get('cash')
                nav_rows.append((a,nd,base,str(total),str(dec(cash)) if cash not in (None,'') else None,h))
            if not nav_rows: raise ValueError('missing required NAV in base section')
            if not any(r[1]==d for r in nav_rows): raise ValueError('missing NAV for statement end date')
            if len({r[1] for r in nav_rows})!=len(nav_rows): raise ValueError('duplicate NAV dates')
            for r in nav_rows: c.execute('INSERT OR REPLACE INTO nav VALUES(?,?,?,?,?,?)',r)
            container=s.find('OpenPositions'); pos=[]; seen=set()
            if container is not None:
                for n in container.findall('OpenPosition'):
                    if n.get('levelOfDetail','').upper()!='SUMMARY': raise ValueError('positions must be SUMMARY only')
                    if day(n.get('reportDate',''))!=d: raise ValueError('position date mismatch')
                    conid=n.get('conid'); cur=n.get('currency'); symbol=n.get('symbol'); asset=n.get('assetCategory')
                    if not conid or conid in seen or not cur or not symbol or not asset: raise ValueError('missing or duplicate position identity')
                    seen.add(conid); rate=Decimal(1) if cur==base else dec(n.get('fxRateToBase'))
                    if rate<=0: raise ValueError('invalid FX rate')
                    cost=n.get('costBasisMoney')
                    pos.append((a,d,conid,symbol,asset,cur,str(dec(n.get('position'))),str(dec(n.get('positionValue'))*rate),n.get('underlyingConid') or conid,None if cost in (None,'') else str(dec(cost)),str(rate)))
                c.execute('DELETE FROM positions WHERE account=? AND date=?',(a,d))
                c.executemany('INSERT INTO positions(account,date,conid,symbol,asset,currency,quantity,value_base,underlying_conid,cost_basis,fx_rate) VALUES(?,?,?,?,?,?,?,?,?,?,?)',pos)
                c.execute('INSERT OR REPLACE INTO snapshots VALUES(?,?,?,1)',(a,d,h))
            else:
                # Never retain an older same-day position snapshot after NAV revision without positions.
                c.execute('DELETE FROM positions WHERE account=? AND date=?',(a,d)); c.execute('INSERT OR REPLACE INTO snapshots VALUES(?,?,?,0)',(a,d,h))
            change=next(iter(s.findall('ChangeInNAV')), None)
            if change is not None and change.get('currency')==base:
                kept=('startingValue','endingValue','depositsWithdrawals','internalCashTransfers','assetTransfers','dividends','interest','commissions','otherFees','withholdingTax','realized','changeInUnrealized','mtm','twr')
                payload={k:change.get(k) for k in kept if change.get(k) not in (None,'')}
                c.execute('INSERT OR REPLACE INTO nav_change VALUES(?,?,?,?,?,?)',(a,day(change.get('fromDate') or s.get('fromDate','')),d,base,json.dumps(payload),h))
            for category, tag, idfield in [('trade','Trade','tradeID'),('cash','CashTransaction','transactionID'),('corporate_action','CorporateAction','transactionID')]:
                for n in s.iter(tag):
                    # Raw remains authoritative; only identified detail rows become queryable.
                    rid=n.get(idfield) or (n.get('actionID') if category=='corporate_action' else None)
                    if not rid: continue
                    payload={k:v for k,v in n.attrib.items() if k.lower() not in {'accountid','acctalias','model','clientaccountid','masteraccountid','traderid','orderreference'}}
                    c.execute('INSERT OR REPLACE INTO records VALUES(?,?,?,?,?)',(a,category,rid,json.dumps(payload),h))
            summary.append({'account':a,'as_of':d,'positions':len(pos),'positions_section_present':container is not None})
        c.execute('INSERT INTO imports VALUES(?,?)',(h,now()))
    return {'status':'imported','source':h,'accounts':summary}
def portfolio():
    cfg=config()['finance']; aliases=set(cfg['accounts'].values())
    if not aliases: return {'status':'not_configured'}
    with db('finance') as c:
        schema(c); latest={a:c.execute('SELECT * FROM nav WHERE account=? ORDER BY date DESC LIMIT 1',(a,)).fetchone() for a in aliases}
        if any(r is None for r in latest.values()): return {'status':'missing_accounts','missing':sorted(a for a,r in latest.items() if r is None)}
        dates={a:r['date'] for a,r in latest.items()}
        if len(set(dates.values()))!=1: return {'status':'date_mismatch','dates':dates}
        d=next(iter(dates.values())); total=sum((dec(r['total']) for r in latest.values()),Decimal(0))
        accounts=[{'account':a,'nav':r['total'],'cash':r['cash'],'source':r['source']} for a,r in sorted(latest.items())]
        stale=_stale(d,cfg)
        complete=all(c.execute('SELECT 1 FROM snapshots WHERE account=? AND date=? AND complete=1 AND source=?',(a,d,latest[a]['source'])).fetchone() for a in aliases)
        positions=[]
        if complete:
            for a in sorted(aliases):
                positions.extend(dict(r) for r in c.execute('SELECT * FROM positions WHERE account=? AND date=?',(a,d)))
            positions.sort(key=lambda r:abs(dec(r['value_base'])),reverse=True)
        changes=[]
        for a in sorted(aliases):
            row=c.execute('SELECT * FROM nav_change WHERE account=? AND to_date=?',(a,d)).fetchone()
            if row: changes.append({'account':a,'from_date':row['from_date'],'to_date':row['to_date'],'currency':row['currency'],'fields':json.loads(row['payload'])})
        return {'status':'ok' if complete and not stale else 'partial_or_stale','as_of':d,'stale':stale,'advice_allowed':complete and not stale,'positions_complete':complete,'currency':cfg['base_currency'],'nav':str(total),'accounts':accounts,'positions':positions[:bounded(100)],'position_count':len(positions),'nav_change':changes,'benchmark':'unavailable','sources':{'nav':'EquitySummaryInBase/EquitySummaryByReportDateInBase','positions':'OpenPositions/OpenPosition SUMMARY','nav_change':'ChangeInNAV'},'note':'Signed position values; not delta-adjusted option exposure. Raw report NAV is authoritative; not live. No benchmark series is configured.'}
def history(limit=30):
    cfg=config()['finance']; aliases=set(cfg['accounts'].values()); out=[]
    if not aliases: return []
    with db('finance') as c:
        schema(c); by={}
        for r in c.execute('SELECT * FROM nav ORDER BY date DESC'):
            if r['account'] in aliases: by.setdefault(r['date'],{})[r['account']]=r
        for d,rows in sorted(by.items(),reverse=True):
            if set(rows)==aliases: out.append({'date':d,'nav':str(sum((dec(r['total']) for r in rows.values()),Decimal(0))),'currency':cfg['base_currency']})
        return list(reversed(out if limit is None else out[:bounded(limit)]))
def records(category,limit=20,offset=0):
    return transactions(category=category,limit=limit,offset=offset)
def transactions(category=None,account=None,conid=None,start=None,end=None,limit=20,offset=0):
    if category is not None and category not in {'trade','cash','corporate_action'}: raise ValueError('invalid category')
    clauses=['1=1']; params=[]
    if category: clauses.append('category=?'); params.append(category)
    if account: clauses.append('account=?'); params.append(account)
    if conid: clauses.append("json_extract(payload,'$.conid')=?"); params.append(str(conid))
    if start: clauses.append("substr(json_extract(payload,'$.reportDate'),1,8)>=?"); params.append(day(start).replace('-',''))
    if end: clauses.append("substr(json_extract(payload,'$.reportDate'),1,8)<=?"); params.append(day(end).replace('-',''))
    params.extend([bounded(limit),max(0,int(offset))])
    with db('finance') as c:
        schema(c)
        rows=c.execute(f"SELECT * FROM records WHERE {' AND '.join(clauses)} ORDER BY rowid LIMIT ? OFFSET ?",params)
        return [{'account':r['account'],'category':r['category'],'record_id':r['record_id'],'fields':json.loads(r['payload']),'source':r['source'],'offset':max(0,int(offset))} for r in rows]
def fetch_flex(query_id, token, start=None, end=None):
    """Operator only. Never log tokens or URLs; no untrusted redirect."""
    if bool(start)!=bool(end): raise ValueError('provide both from/to dates')
    base='https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/'
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs): return None
    opener=urllib.request.build_opener(NoRedirect)
    def request(endpoint, q, extra=None):
        params={'t':token,'q':q,'v':'3',**(extra or {})}
        req=urllib.request.Request(base+endpoint+'?'+urllib.parse.urlencode(params),headers={'User-Agent':'Python/3.13'})
        try:
            with opener.open(req,timeout=45) as r: return r.read()
        except Exception: raise RuntimeError('Flex HTTP request failed; check network/credentials') from None
    extra={'fd':day(start).replace('-',''),'td':day(end).replace('-','')} if start else {}
    response=xml(request('SendRequest',query_id,extra)); ref=response.findtext('ReferenceCode')
    if response.findtext('Status')!='Success' or not ref: raise RuntimeError('Flex SendRequest error '+str(response.findtext('ErrorCode')))
    for _ in range(12):
        time.sleep(7); data=request('GetStatement',ref); r=xml(data)
        if r.tag=='FlexQueryResponse':
            p=private_dir(HOME/'raw'/'ibkr')/(digest(data)+'.xml'); p.write_bytes(data); return p
        if r.findtext('ErrorCode') not in {'1001','1003','1004','1005','1006','1007','1008','1009','1018','1019','1021'}: raise RuntimeError('Flex retrieval error '+str(r.findtext('ErrorCode')))
    raise TimeoutError('Flex report not ready; retry later')
EXTERNAL_IN={'deposit','deposits','contribution','contributions'}
EXTERNAL_OUT={'withdrawal','withdrawals','distribution','distributions'}
TRANSFER_TYPES={'transfer','transfers','internal transfer','internal cash transfer'}
def _cash_rows():
    with db('finance') as c:
        schema(c)
        return [dict(r) | {'fields':json.loads(r['payload'])} for r in c.execute("SELECT * FROM records WHERE category='cash' ORDER BY rowid")]
def _flow_amount(fields):
    if fields.get('amountInBase') not in (None,''):
        return dec(fields['amountInBase'])
    amt=dec(fields.get('amount') or 0)
    fx=fields.get('fxRateToBase')
    if fx not in (None,''):
        return amt*dec(fx)
    return amt
def matched_transfers(rows):
    """Pair opposite cash transfers across configured accounts using normalized Decimal amounts. Unmatched rows stay unmatched."""
    open_rows=[]
    matched=set(); unmatched=[]
    for row in rows:
        kind=(row['fields'].get('type') or '').strip().casefold()
        if kind not in TRANSFER_TYPES: continue
        amount=_flow_amount(row['fields']); when=day(row['fields'].get('dateTime') or row['fields'].get('reportDate') or row['fields'].get('date') or '')
        cur=(row['fields'].get('currency') or '').upper()
        partner=next((i for i,cand in enumerate(open_rows) if cand[0]==when and cand[1]==cur and cand[3]['account']!=row['account'] and cand[2]==-amount), None)
        ident=(row['account'],row['record_id'])
        if partner is None:
            open_rows.append((when,cur,amount,row))
        else:
            other=open_rows.pop(partner)
            matched.add(ident); matched.add((other[3]['account'],other[3]['record_id']))
    for _,_,_,row in open_rows:
        unmatched.append({'account':row['account'],'record_id':row['record_id'],'amount':str(_flow_amount(row['fields']))})
    return matched,unmatched
def _external_by_date(rows,matched):
    flows={}
    contributions=Decimal(0); withdrawals=Decimal(0)
    for row in rows:
        if (row['account'],row['record_id']) in matched: continue
        kind=(row['fields'].get('type') or '').strip().casefold()
        amount=_flow_amount(row['fields'])
        when=day(row['fields'].get('dateTime') or row['fields'].get('reportDate') or row['fields'].get('date') or '')
        if kind in {'deposits/withdrawals','deposit/withdrawal'}:
            if amount>0: flows[when]=flows.get(when,Decimal(0))+amount; contributions+=amount
            elif amount<0: flows[when]=flows.get(when,Decimal(0))+amount; withdrawals-=amount
            continue
        if kind in EXTERNAL_IN:
            flows[when]=flows.get(when,Decimal(0))+amount; contributions+=amount
        elif kind in EXTERNAL_OUT:
            flows[when]=flows.get(when,Decimal(0))-abs(amount); withdrawals+=abs(amount)
    return flows,contributions,withdrawals
def _xnpv(rate,flows):
    base=flows[0][0].toordinal()
    total=Decimal(0)
    for flow_date,amount in flows:
        years=Decimal(flow_date.toordinal()-base)/Decimal(365)
        total+=amount/((Decimal(1)+rate)**years)
    return total
def xirr(cash_flows):
    """Investor XIRR. Contributions are negative, withdrawals and terminal value positive. Returns None if no sign change."""
    if len(cash_flows)<2 or not any(a<0 for _,a in cash_flows) or not any(a>0 for _,a in cash_flows): return None
    ordered=sorted(cash_flows,key=lambda item:item[0])
    low,high=Decimal('-0.9'),Decimal('10')
    lo,hi=_xnpv(low,ordered),_xnpv(high,ordered)
    if lo==0: return low
    if lo*hi>0: return None
    for _ in range(80):
        mid=(low+high)/2
        value=_xnpv(mid,ordered)
        if abs(value)<Decimal('0.0000001'): return mid
        if lo*value<=0: high,hi=mid,value
        else: low,lo=mid,value
    return (low+high)/2
def performance():
    """Daily-boundary approximation. Exact TWR is refused because Flex flow time-of-day is not used."""
    snap=portfolio()
    if snap.get('status') not in {'ok','partial_or_stale'}: return {'status':snap.get('status','unavailable'),'twr':None,'xirr':None,'limitations':['combined performance withheld until every configured account shares one statement date']}
    cfg=config()['finance']; aliases=set(cfg['accounts'].values())
    with db('finance') as c:
        schema(c)
        series=[]
        by={}
        for r in c.execute('SELECT * FROM nav ORDER BY date'):
            if r['account'] in aliases: by.setdefault(r['date'],{})[r['account']]=dec(r['total'])
        for d,rows in by.items():
            if set(rows)==aliases: series.append((date.fromisoformat(d),sum(rows.values(),Decimal(0))))
    rows=_cash_rows()
    matched,unmatched=matched_transfers(rows)
    flows,contributions,withdrawals=_external_by_date(rows,matched)
    limitations=['Daily NAV approximation only. Exact TWR is not claimed because external-flow time within the day is not in the calculation.','A change in NAV is not investment return.','This store does not replace the Portfolio Analyzer ledger.']
    if unmatched:
        return {'status':'unmatched_transfers','as_of':snap.get('as_of'),'currency':cfg['base_currency'],'nav':snap.get('nav'),'contributions':str(contributions),'withdrawals':str(withdrawals),'unmatched_transfers':unmatched,'twr':None,'xirr':None,'limitations':limitations+['Combined return is blocked until every internal transfer has its opposite leg.']}
    chain=Decimal(1); intervals=[]
    for (d0,v0),(d1,v1) in zip(series,series[1:]):
        d0_iso, d1_iso = d0.isoformat(), d1.isoformat()
        external=sum((amt for f_d,amt in flows.items() if d0_iso<f_d<=d1_iso), Decimal(0))
        if v0<=0: return {'status':'invalid_nav','twr':None,'xirr':None,'limitations':limitations+['A nonpositive opening NAV blocks the daily approximation.']}
        growth=(v1-external)/v0-1
        if growth<=Decimal('-1'): return {'status':'invalid_interval','twr':None,'xirr':None,'limitations':limitations}
        chain*=(1+growth); intervals.append({'from':d0.isoformat(),'to':d1.isoformat(),'external_flow':str(external),'interval_return':str(growth)})
    twr=None if len(series)<2 else chain-1
    investor=[]
    if series:
        investor.append((series[0][0],-series[0][1]))
        for when,amount in flows.items():
            flow_day=date.fromisoformat(when)
            if series[0][0]<flow_day<=series[-1][0]: investor.append((flow_day,-amount))
        investor.append((series[-1][0],series[-1][1]))
    rate=xirr(investor) if series else None
    return {'status':'ok' if snap['status']=='ok' else snap['status'],'as_of':snap.get('as_of'),'currency':cfg['base_currency'],'nav':snap.get('nav'),'contributions':str(contributions),'withdrawals':str(withdrawals),'unmatched_transfers':[],'twr':None if twr is None else str(twr),'twr_exact':False,'twr_method':'daily_approximation','intervals':intervals,'xirr':None if rate is None else str(rate),'xirr_method':'bisection_365','limitations':limitations}
def import_bank(path):
    """Manual bank export. Rules only; unmatched rows stay pending and are not guessed."""
    import csv
    p=Path(path)
    with p.open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        required={'date','amount','description','account','currency'}
        if not required.issubset(set(reader.fieldnames or [])): raise ValueError('bank CSV needs date,amount,description,account,currency')
        body=list(reader)
    counts={'expense':0,'income':0,'transfer':0,'refund':0,'interest':0,'pending':0}
    saved=[]
    for i,row in enumerate(body,start=2):
        amount=dec(row['amount']); text=(row.get('description') or '').casefold()
        if any(word in text for word in ('transfer','card payment','payment thank you')): kind='transfer'
        elif 'refund' in text or 'reversal' in text: kind='refund'
        elif 'interest' in text: kind='interest'
        elif amount<0: kind='expense'
        elif amount>0: kind='income'
        else: kind='pending'
        counts[kind]+=1
        saved.append({'line':i,'date':day(row['date']),'account':row['account'],'currency':row['currency'].upper(),'amount':str(amount),'kind':kind,'description':row['description'][:180]})
    with db('finance') as c:
        schema(c)
        c.execute('CREATE TABLE IF NOT EXISTS bank(line INTEGER, source TEXT, payload TEXT, PRIMARY KEY(source,line))')
        h=digest(p.read_bytes())
        c.execute('DELETE FROM bank WHERE source=?',(h,))
        for row in saved: c.execute('INSERT INTO bank VALUES(?,?,?)',(row['line'],h,json.dumps(row)))
    return {'status':'imported','source':h,'counts':counts,'pending':counts['pending'],'rows':saved[:bounded(50)]}
def sync(start=None,end=None):
    queries=config()['finance']['flex_queries']; out=[]
    if not queries: raise ValueError('configure flex_queries')
    for q in queries:
        token=operator_env(q['token_env']); qid=operator_env(q['query_env'])
        if not token or not qid: raise ValueError('missing configured Flex environment variables')
        out.append(import_xml(fetch_flex(qid,token,start,end))); time.sleep(7)
    return out
