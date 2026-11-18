"""Conservative Flex snapshot ingestion. Raw statements retained, not exposed to LLM."""
import hashlib, json, os, time, urllib.request, urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from .core import db, config, digest, private_dir, HOME, now, bounded, operator_env

class MissingFxRateError(ValueError):
    pass

@dataclass(frozen=True)
class AccountRecord:
    account: str
    tax_bucket: str
    nav: Decimal
    cash: Decimal | None
    allow_margin: bool = True
    buying_power: Decimal | None = None
    source: str = ''

@dataclass(frozen=True)
class PositionRecord:
    account: str
    conid: str
    symbol: str
    asset: str
    currency: str
    quantity: Decimal
    multiplier: Decimal
    market_value_base: Decimal
    underlying_conid: str | None = None
    cost_basis: Decimal | None = None
    underlying_price: Decimal | None = None
    underlying_currency: str | None = None
    fx_rate_to_base: Decimal | None = None
    delta: Decimal | None = None
    source: str = ''

def compute_snapshot_id(as_of, currency, accounts, positions):
    """Canonical SHA-256 snapshot fingerprint covering all account margin/cash fields and position/Greek/FX fields."""
    sid_payload={
        'schema':'v0.3.3',
        'as_of':as_of,
        'currency':currency,
        'accts':sorted((a.account,a.tax_bucket,str(a.nav.normalize()),str(a.cash.normalize()) if a.cash is not None else None,str(a.buying_power.normalize()) if a.buying_power is not None else None,a.allow_margin) for a in accounts),
        'pos':sorted((p.account,p.conid,p.symbol,p.asset,str(p.quantity.normalize()),str(p.multiplier.normalize()),str(p.market_value_base.normalize()),str(p.underlying_price.normalize()) if p.underlying_price is not None else None,str(p.fx_rate_to_base.normalize()) if p.fx_rate_to_base is not None else None,str(p.delta.normalize()) if p.delta is not None else None) for p in positions)
    }
    return hashlib.sha256(json.dumps(sid_payload, sort_keys=True).encode('utf-8')).hexdigest()[:16]

@dataclass(frozen=True)
class PortfolioSnapshot:
    snapshot_id: str
    as_of: str | None
    status: str
    stale: bool
    advice_allowed: bool
    positions_complete: bool
    currency: str
    nav: Decimal
    cash: Decimal
    accounts: tuple[AccountRecord, ...] = field(default_factory=tuple)
    positions: tuple[PositionRecord, ...] = field(default_factory=tuple)
    limitations: tuple[str, ...] = field(default_factory=tuple)

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
    """Stale when the statement is in the future, older than the last completed business day, or past the configured calendar limit."""
    reported=date.fromisoformat(as_of)
    if reported>date.today(): return True
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
        c.execute('INSERT OR REPLACE INTO sources VALUES(?,?)',(h,generated))
        imported_any=False
        for s in all_stmts:
            if s.get('accountId') not in accounts: raise ValueError('unexpected account; check configuration')
            a=accounts[s.get('accountId')]; d=day(s.get('toDate',''))
            existing_snap=c.execute('SELECT sn.source, sr.generated FROM snapshots sn LEFT JOIN sources sr ON sn.source=sr.hash WHERE sn.account=? AND sn.date=?',(a,d)).fetchone()
            stmt_gen=s.get('whenGenerated') or generated
            if existing_snap and existing_snap['generated'] and stmt_gen and existing_snap['generated']>stmt_gen:
                continue
            imported_any=True
            # Accept only explicitly base-currency NAV; no implicit FX conversion.
            nav_rows=[]
            for n in s.iter('EquitySummaryByReportDateInBase'):
                if n.get('currency')!=base: raise ValueError('NAV currency mismatch')
                nd=day(n.get('reportDate','')); total=dec(n.get('total')); cash=n.get('cash')
                nav_rows.append((a,nd,base,str(total),str(dec(cash)) if cash not in (None,'') else None,h))
            if not nav_rows: raise ValueError('missing required NAV in base section')
            if not any(r[1]==d for r in nav_rows): raise ValueError('missing NAV for statement end date')
            if len({r[1] for r in nav_rows})!=len(nav_rows): raise ValueError('duplicate NAV dates')
            for r in nav_rows:
                ex_nav=c.execute('SELECT sr.generated FROM nav n LEFT JOIN sources sr ON n.source=sr.hash WHERE n.account=? AND n.date=?',(r[0],r[1])).fetchone()
                if ex_nav and ex_nav['generated'] and stmt_gen and ex_nav['generated']>stmt_gen:
                    continue
                c.execute('INSERT OR REPLACE INTO nav VALUES(?,?,?,?,?,?)',r)
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
        if not imported_any:
            return {'status':'kept_newer_snapshot','source':h}
        c.execute('INSERT INTO imports VALUES(?,?)',(h,now()))
    return {'status':'imported','source':h,'accounts':summary}

def load_full_portfolio_snapshot():
    """Load the full, untruncated canonical PortfolioSnapshot in a single consistent SQLite read transaction."""
    cfg=config()['finance']; aliases=set(cfg['accounts'].values())
    base_cur=str(cfg.get('base_currency') or 'CAD').upper()
    if not aliases:
        return PortfolioSnapshot(snapshot_id='not_configured',as_of=None,status='not_configured',stale=True,advice_allowed=False,positions_complete=False,currency=base_cur,nav=Decimal(0),cash=Decimal(0))
    acct_tax_cfg=cfg.get('account_tax_types') or {}
    acct_cons_cfg=cfg.get('account_constraints') or {}
    with db('finance') as c:
        schema(c)
        c.execute('BEGIN')
        latest={a:c.execute('SELECT * FROM nav WHERE account=? ORDER BY date DESC LIMIT 1',(a,)).fetchone() for a in sorted(aliases)}
        if any(r is None for r in latest.values()):
            return PortfolioSnapshot(snapshot_id='missing_accounts',as_of=None,status='missing_accounts',stale=True,advice_allowed=False,positions_complete=False,currency=base_cur,nav=Decimal(0),cash=Decimal(0))
        dates={a:r['date'] for a,r in latest.items()}
        if len(set(dates.values()))!=1:
            return PortfolioSnapshot(snapshot_id='date_mismatch',as_of=None,status='date_mismatch',stale=True,advice_allowed=False,positions_complete=False,currency=base_cur,nav=Decimal(0),cash=Decimal(0))
        d=next(iter(dates.values()))
        stale=_stale(d,cfg)
        complete=all(c.execute('SELECT 1 FROM snapshots WHERE account=? AND date=? AND complete=1 AND source=?',(a,d,latest[a]['source'])).fetchone() for a in aliases)
        accts=[]; tot_nav=Decimal(0); tot_cash=Decimal(0)
        for a in sorted(aliases):
            r=latest[a]
            n_val=dec(r['total']); c_val=dec(r['cash']) if r['cash'] not in (None,'') else None
            tot_nav+=n_val
            if c_val is not None: tot_cash+=c_val
            bucket=acct_tax_cfg.get(a, a.lower() if a.lower() in {'tfsa','rrsp','fhsa','taxable','margin'} else 'taxable')
            allow_m=(acct_cons_cfg.get(a) or acct_cons_cfg.get(bucket) or {}).get('allow_margin', bucket not in {'tfsa','rrsp','fhsa'})
            accts.append(AccountRecord(account=a,tax_bucket=bucket,nav=n_val,cash=c_val,allow_margin=bool(allow_m),source=str(r['source'] or '')))
        pos_recs=[]
        if complete:
            for a in sorted(aliases):
                for r in c.execute('SELECT * FROM positions WHERE account=? AND date=? ORDER BY conid',(a,d)):
                    p=dict(r)
                    cur=str(p.get('currency') or base_cur).upper()
                    asset=str(p.get('asset') or 'STK').upper()
                    if asset=='CASH': continue
                    fx_v=dec(p['fx_rate']) if p.get('fx_rate') not in (None,'') else (Decimal(1) if cur==base_cur else None)
                    cb_v=dec(p['cost_basis']) if p.get('cost_basis') not in (None,'') else None
                    pos_recs.append(PositionRecord(
                        account=a, conid=str(p['conid']), symbol=str(p['symbol']).upper(), asset=asset,
                        currency=cur, quantity=dec(p['quantity']), multiplier=Decimal(100) if asset=='OPT' else Decimal(1),
                        market_value_base=dec(p['value_base']), underlying_conid=str(p.get('underlying_conid') or p['conid']),
                        cost_basis=cb_v, underlying_currency=cur, fx_rate_to_base=fx_v, source=str(latest[a]['source'] or '')
                    ))
    sid=compute_snapshot_id(d, base_cur, accts, pos_recs)
    is_future=date.fromisoformat(d)>date.today()
    missing_cash=any(a.cash is None for a in accts)
    advice_ok=complete and not stale and not is_future and not missing_cash
    st='future_statement' if is_future else ('missing_cash' if missing_cash else ('ok' if advice_ok else 'partial_or_stale'))
    return PortfolioSnapshot(
        snapshot_id=sid, as_of=d, status=st,
        stale=stale, advice_allowed=advice_ok, positions_complete=complete,
        currency=base_cur, nav=tot_nav, cash=tot_cash, accounts=tuple(accts), positions=tuple(pos_recs),
        limitations=('Reconciled Flex daily statement; not live broker state.',)
    )

def portfolio(limit=None, offset=0):
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
        lim=bounded(limit if limit is not None else 100); off=max(0, int(offset))
        page=positions[off:off+lim]
        return {'status':'ok' if complete and not stale else 'partial_or_stale','as_of':d,'stale':stale,'advice_allowed':complete and not stale,'positions_complete':complete,'currency':cfg['base_currency'],'nav':str(total),'accounts':accounts,'positions':page,'position_count':len(positions),'limit':lim,'offset':off,'has_more':(off+lim)<len(positions),'nav_change':changes,'benchmark':'unavailable','sources':{'nav':'EquitySummaryInBase/EquitySummaryByReportDateInBase','positions':'OpenPositions/OpenPosition SUMMARY','nav_change':'ChangeInNAV'},'note':'Signed position values; not delta-adjusted option exposure. Raw report NAV is authoritative; not live. No benchmark series is configured.'}
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
def _flow_amount(fields, base_currency=None):
    base=str(base_currency or config()['finance'].get('base_currency') or 'CAD').upper()
    if fields.get('amountInBase') not in (None,''):
        return dec(fields['amountInBase'])
    amt=dec(fields.get('amount') or 0)
    cur=str(fields.get('currency') or '').strip().upper()
    if not cur or cur==base:
        return amt
    fx=fields.get('fxRateToBase')
    if fx not in (None,'') and dec(fx)>0:
        return amt*dec(fx)
    raise MissingFxRateError(f'missing amountInBase/fxRateToBase for {cur} cash flow in {base} ledger')
def matched_transfers(rows):
    """Pair opposite cash transfers across configured accounts using normalized Decimal amounts; ambiguous multi-matches stay unmatched."""
    t_rows=[]
    for row in rows:
        kind=(row['fields'].get('type') or '').strip().casefold()
        if kind not in TRANSFER_TYPES: continue
        try: amount=_flow_amount(row['fields'])
        except MissingFxRateError: amount=dec(row['fields'].get('amount') or 0)
        when=day(row['fields'].get('dateTime') or row['fields'].get('reportDate') or row['fields'].get('date') or '')
        cur=(row['fields'].get('currency') or '').upper()
        tid=str(row['fields'].get('transferID') or row['fields'].get('actionID') or '').strip()
        t_rows.append({'when':when,'cur':cur,'amount':amount,'tid':tid,'row':row})
    # Count exact key occurrences to detect ambiguous same-day/amount/currency duplicates
    from collections import Counter
    key_counts=Counter((t['when'],t['cur'],abs(t['amount'])) for t in t_rows if not t['tid'])
    open_rows=[]; matched=set(); unmatched=[]
    for t in t_rows:
        row=t['row']; ident=(row['account'],row['record_id'])
        if not t['tid'] and key_counts[(t['when'],t['cur'],abs(t['amount']))]>2:
            unmatched.append({'account':row['account'],'record_id':row['record_id'],'amount':str(t['amount']),'reason':'ambiguous_transfer'})
            continue
        partner=next((i for i,c in enumerate(open_rows) if c['when']==t['when'] and c['cur']==t['cur'] and c['row']['account']!=row['account'] and c['amount']==-t['amount'] and (not t['tid'] or not c['tid'] or c['tid']==t['tid'])), None)
        if partner is None:
            open_rows.append(t)
        else:
            other=open_rows.pop(partner)
            matched.add(ident); matched.add((other['row']['account'],other['row']['record_id']))
    for c in open_rows:
        row=c['row']
        unmatched.append({'account':row['account'],'record_id':row['record_id'],'amount':str(c['amount'])})
    return matched,unmatched
def _external_by_date(rows,matched,base_currency=None):
    flows={}
    contributions=Decimal(0); withdrawals=Decimal(0)
    for row in rows:
        if (row['account'],row['record_id']) in matched: continue
        kind=(row['fields'].get('type') or '').strip().casefold()
        if kind not in ({'deposits/withdrawals','deposit/withdrawal'} | EXTERNAL_IN | EXTERNAL_OUT):
            continue
        amount=_flow_amount(row['fields'], base_currency=base_currency)
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
    """Investor XIRR with fine-grid bracketing and multiple-root detection. Returns None if same-day, no sign change, or multiple roots."""
    if len(cash_flows)<2 or not any(a<0 for _,a in cash_flows) or not any(a>0 for _,a in cash_flows): return None
    ordered=sorted(cash_flows,key=lambda item:item[0])
    if len({d for d,_ in ordered})<2: return None
    grid=[Decimal('-0.99')]+[Decimal(str(round(-0.95+0.05*i, 4))) for i in range(60)]+[Decimal(str(x)) for x in (2.5,5.0,10.0,50.0,100.0)]
    vals=[_xnpv(g,ordered) for g in grid]
    exact_roots=[g for g,v in zip(grid,vals) if v==0]
    brackets=[(grid[i],grid[i+1],vals[i],vals[i+1]) for i in range(len(grid)-1) if vals[i]*vals[i+1]<0]
    if len(exact_roots)==1 and not brackets: return exact_roots[0]
    if exact_roots or len(brackets)!=1: return None
    low,high,lo,hi=brackets[0]
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
    limitations=['Daily NAV approximation only. Exact TWR is not claimed because external-flow time within the day is not in the calculation.','A change in NAV is not investment return.','This store does not replace the Portfolio Analyzer ledger.']
    try:
        flows,contributions,withdrawals=_external_by_date(rows,matched,base_currency=cfg['base_currency'])
    except MissingFxRateError as exc:
        return {'status':'missing_fx_rate','as_of':snap.get('as_of'),'currency':cfg['base_currency'],'nav':snap.get('nav'),'unmatched_transfers':unmatched,'twr':None,'xirr':None,'limitations':limitations+[str(exc)]}
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

def canonical_snapshot(portfolio_dict=None, tax_positions_fallback=None):
    """Build an immutable canonical PortfolioSnapshot with per-account, per-contract positions (never truncated)."""
    if isinstance(portfolio_dict, PortfolioSnapshot):
        return portfolio_dict
    if portfolio_dict is None:
        return load_full_portfolio_snapshot()
    snap=portfolio_dict
    if 'nav' not in snap:
        return PortfolioSnapshot(
            snapshot_id='unavailable',
            as_of=snap.get('as_of'),
            status=str(snap.get('status') or 'unavailable'),
            stale=True,
            advice_allowed=False,
            positions_complete=False,
            currency=str(snap.get('currency') or config()['finance'].get('base_currency') or 'CAD').upper(),
            nav=Decimal(0),
            cash=Decimal(0),
        )
    base_cur=str(snap.get('currency') or config()['finance'].get('base_currency') or 'CAD').upper()
    acct_map_cfg=config().get('finance',{}).get('account_tax_types') or config().get('finance',{}).get('accounts') or {}
    accts=[]
    tot_cash=Decimal(0)
    for a in (snap.get('accounts') or []):
        name=str(a.get('account') or '').strip()
        bucket=acct_map_cfg.get(name, name.lower() if name.lower() in {'tfsa','rrsp','fhsa','taxable','margin'} else 'taxable')
        c_val=dec(a['cash']) if a.get('cash') not in (None,'') else None
        if c_val is not None: tot_cash+=c_val
        allow_m=bool(a.get('allow_margin')) if 'allow_margin' in a else (bucket not in {'tfsa','rrsp','fhsa'})
        bp_val=dec(a['buying_power']) if a.get('buying_power') not in (None,'') else None
        accts.append(AccountRecord(account=name, tax_bucket=bucket, nav=dec(a['nav']), cash=c_val, allow_margin=allow_m, buying_power=bp_val, source=str(a.get('source') or '')))
    raw_pos=snap.get('positions')
    declared_cnt=snap.get('position_count')
    if raw_pos is not None and declared_cnt is not None and int(declared_cnt)>len(raw_pos):
        return PortfolioSnapshot(snapshot_id='truncated_snapshot',as_of=snap.get('as_of'),status='truncated_snapshot',stale=True,advice_allowed=False,positions_complete=False,currency=base_cur,nav=dec(snap['nav']),cash=tot_cash,accounts=tuple(accts),positions=())
    if raw_pos is None and tax_positions_fallback is not None:
        raw_pos=tax_positions_fallback
    pos_recs=[]
    for p in (raw_pos or []):
        sym=str(p.get('symbol') or '').strip().upper()
        if not sym: continue
        asset=str(p.get('asset') or 'STK').upper()
        if asset=='CASH': continue
        acct_p=str(p.get('account') or '').strip()
        lots=p.get('lots') or []
        qty_raw=p.get('quantity')
        if qty_raw in (None,'') and lots:
            qty_raw=sum((dec(l.get('quantity') or 0) for l in lots), Decimal(0))
        qty=dec(qty_raw) if qty_raw not in (None,'') else Decimal(0)
        val_raw=p.get('value_base') if p.get('value_base') not in (None,'') else p.get('total_value_base')
        val_b=dec(val_raw) if val_raw not in (None,'') else Decimal(0)
        mult=dec(p['multiplier']) if p.get('multiplier') not in (None,'') else (Decimal(100) if asset=='OPT' else Decimal(1))
        und_px=dec(p['underlying_price']) if p.get('underlying_price') not in (None,'') else None
        und_cur=str(p.get('underlying_currency') or p.get('currency') or base_cur).upper()
        fx_val=dec(p['fx_rate_to_base']) if p.get('fx_rate_to_base') not in (None,'') else (dec(p['fx_rate']) if p.get('fx_rate') not in (None,'') else (Decimal(1) if und_cur==base_cur else None))
        d_val=dec(p['delta']) if p.get('delta') not in (None,'') else None
        pos_recs.append(PositionRecord(
            account=acct_p, conid=str(p.get('conid') or sym), symbol=sym, asset=asset,
            currency=str(p.get('currency') or base_cur).upper(), quantity=qty, multiplier=mult,
            market_value_base=val_b, underlying_price=und_px, underlying_currency=und_cur,
            fx_rate_to_base=fx_val, delta=d_val, source=str(p.get('source') or '')
        ))
    sid=compute_snapshot_id(snap.get('as_of'), base_cur, accts, pos_recs)
    return PortfolioSnapshot(
        snapshot_id=sid,
        as_of=snap.get('as_of'),
        status=str(snap.get('status') or 'ok'),
        stale=bool(snap.get('stale')),
        advice_allowed=bool(snap.get('advice_allowed')),
        positions_complete=bool(snap.get('positions_complete', True)),
        currency=base_cur,
        nav=dec(snap['nav']),
        cash=tot_cash,
        accounts=tuple(accts),
        positions=tuple(pos_recs),
        limitations=tuple(snap.get('limitations') or ()),
    )
