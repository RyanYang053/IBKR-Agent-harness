"""Strictly Read-Only Broker Adapter (TWS/IB Gateway + Offline Replay) and Live-to-Flex Reconciliation.
Zero order execution: placeOrder, modifyOrder, and cancelOrder are permanently absent."""
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib, json
from .core import bounded, config, db, now
from .finance import AccountRecord, PortfolioSnapshot, PositionRecord, compute_snapshot_id, dec, load_full_portfolio_snapshot

FORBIDDEN_BROKER_METHODS={'placeOrder','cancelOrder','reqGlobalCancel','exerciseOptions'}

def schema(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS broker_snapshots(
        snapshot_id TEXT PRIMARY KEY,
        adapter TEXT NOT NULL,
        as_of TEXT NOT NULL,
        accounts_json TEXT NOT NULL,
        positions_json TEXT NOT NULL,
        greeks_json TEXT NOT NULL,
        entitlements_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        capture_status TEXT NOT NULL DEFAULT 'partial',
        live_verified INTEGER NOT NULL DEFAULT 0,
        incomplete_callbacks_json TEXT NOT NULL DEFAULT '[]'
    );
    ''')
    cols={r[1] for r in c.execute('PRAGMA table_info(broker_snapshots)')}
    if 'capture_status' not in cols: c.execute("ALTER TABLE broker_snapshots ADD COLUMN capture_status TEXT NOT NULL DEFAULT 'partial'")
    if 'live_verified' not in cols: c.execute('ALTER TABLE broker_snapshots ADD COLUMN live_verified INTEGER NOT NULL DEFAULT 0')
    if 'incomplete_callbacks_json' not in cols: c.execute("ALTER TABLE broker_snapshots ADD COLUMN incomplete_callbacks_json TEXT NOT NULL DEFAULT '[]'")

class BrokerReadOnlyAdapter:
    """Base read-only brokerage interface. Subclasses must never implement order-placement methods."""
    adapter_name='base_readonly'
    def connect(self): raise NotImplementedError
    def health(self): raise NotImplementedError
    def list_managed_accounts(self): raise NotImplementedError
    def account_summary(self, account_id): raise NotImplementedError
    def positions(self): raise NotImplementedError
    def contract_details(self, conid): raise NotImplementedError
    def quotes(self, conids): raise NotImplementedError
    def option_chain(self, symbol): raise NotImplementedError
    def option_greeks(self, conid): raise NotImplementedError
    def disconnect(self): pass

class ReplayBrokerAdapter(BrokerReadOnlyAdapter):
    """Deterministic offline replay adapter backed by sanitized synthetic/recorded broker fixtures."""
    adapter_name='offline_replay'
    def __init__(self, fixture_payload):
        self._data=dict(fixture_payload or {})
        self._connected=False

    def connect(self):
        self._connected=True
        return {'status':'connected','adapter':self.adapter_name,'readonly':True}

    def health(self):
        return {'connected':self._connected,'adapter':self.adapter_name,'readonly':True,'as_of':self._data.get('as_of') or now(),'entitlements':self._data.get('entitlements') or {'equities_live':False,'options_greeks_live':False}}

    def list_managed_accounts(self):
        return list((self._data.get('accounts') or {}).keys())

    def account_summary(self, account_id):
        accts=self._data.get('accounts') or {}
        if account_id not in accts: raise KeyError(f'unknown broker account: {account_id}')
        return dict(accts[account_id])

    def positions(self):
        return [dict(p) for p in (self._data.get('positions') or [])]

    def contract_details(self, conid):
        return dict((self._data.get('contracts') or {}).get(str(conid), {}))

    def quotes(self, conids):
        q_map=self._data.get('quotes') or {}
        return {str(c):dict(q_map[str(c)]) for c in conids if str(c) in q_map}

    def option_chain(self, symbol):
        return list((self._data.get('option_chains') or {}).get(str(symbol).upper(), []))

    def option_greeks(self, conid):
        ent=self._data.get('entitlements') or {}
        if not ent.get('options_greeks_live'):
            return {'conid':str(conid),'status':'entitlement_required','delta':None,'gamma':None,'vega':None,'theta':None,'source':'unavailable'}
        g=(self._data.get('greeks') or {}).get(str(conid))
        return dict(g) if g else {'conid':str(conid),'status':'unavailable','delta':None,'gamma':None,'vega':None,'theta':None}

    def disconnect(self):
        self._connected=False

def _persist_broker_snapshot(adapter_name, as_of, accounts, positions, greeks, entitlements, capture_status='partial', live_verified=False, incomplete_callbacks=None):
    payload={'adapter':adapter_name,'as_of':as_of,'accounts':accounts,'positions':positions,'greeks':greeks,'capture_status':capture_status,'live_verified':bool(live_verified)}
    sid='brk_'+hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode('utf-8')).hexdigest()[:16]
    with db('finance') as c:
        schema(c)
        c.execute('INSERT OR REPLACE INTO broker_snapshots(snapshot_id,adapter,as_of,accounts_json,positions_json,greeks_json,entitlements_json,created_at,capture_status,live_verified,incomplete_callbacks_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(
            sid, adapter_name, as_of,
            json.dumps(accounts, ensure_ascii=False),
            json.dumps(positions, ensure_ascii=False),
            json.dumps(greeks, ensure_ascii=False),
            json.dumps(entitlements, ensure_ascii=False),
            now(),
            str(capture_status),
            1 if (live_verified and capture_status=='ok') else 0,
            json.dumps(incomplete_callbacks or [], ensure_ascii=False)
        ))
    return sid

def sync_broker_replay(fixture_payload):
    """Ingest a read-only broker snapshot from ReplayBrokerAdapter without modifying Flex history."""
    adapter=ReplayBrokerAdapter(fixture_payload)
    adapter.connect()
    try:
        h=adapter.health()
        accts={aid:adapter.account_summary(aid) for aid in adapter.list_managed_accounts()}
        pos=adapter.positions()
        greeks={str(p['conid']):adapter.option_greeks(p['conid']) for p in pos if str(p.get('asset','')).upper()=='OPT'}
        sid=_persist_broker_snapshot(adapter.adapter_name, h['as_of'], accts, pos, greeks, h['entitlements'], capture_status='replay', live_verified=False)
        return {'status':'ok','capture_status':'replay','live_verified':False,'snapshot_id':sid,'adapter':adapter.adapter_name,'as_of':h['as_of'],'accounts':len(accts),'positions':len(pos),'entitlements':h['entitlements'],'orders':'disabled'}
    finally:
        adapter.disconnect()

def latest_broker_snapshot():
    with db('finance') as c:
        schema(c)
        row=c.execute('SELECT * FROM broker_snapshots ORDER BY rowid DESC LIMIT 1').fetchone()
    if not row:
        return {'status':'unavailable','capture_status':'unavailable','live_verified':False,'reason':'no_broker_snapshot_recorded','live_acceptance_status':'offline_only_not_connected'}
    cap_st=row['capture_status'] if 'capture_status' in row.keys() else ('replay' if row['adapter']=='offline_replay' else 'partial')
    lv=bool(row['live_verified']) if 'live_verified' in row.keys() else False
    inc=json.loads(row['incomplete_callbacks_json']) if 'incomplete_callbacks_json' in row.keys() else []
    return {
        'status':'ok' if cap_st in {'ok','replay'} else cap_st,
        'capture_status':cap_st,
        'live_verified':lv and cap_st=='ok' and row['adapter']=='ibkr_tws_readonly',
        'incomplete_callbacks':inc,
        'snapshot_id':row['snapshot_id'],
        'adapter':row['adapter'],
        'as_of':row['as_of'],
        'accounts':json.loads(row['accounts_json']),
        'positions':json.loads(row['positions_json']),
        'greeks':json.loads(row['greeks_json']),
        'entitlements':json.loads(row['entitlements_json']),
        'created_at':row['created_at'],
        'orders':'disabled'
    }

def reconcile_live_to_flex():
    """Compare latest read-only broker snapshot against reconciled Flex PortfolioSnapshot without overwriting Flex."""
    flex_snap=load_full_portfolio_snapshot()
    brk=latest_broker_snapshot()
    if brk.get('status')!='ok':
        return {'status':'unavailable','flex_status':flex_snap.status,'broker_status':brk.get('status'),'reason':brk.get('reason','broker_offline'),'orders':'disabled'}
    if flex_snap.status in {'unavailable','not_configured','missing_accounts','date_mismatch'}:
        return {'status':'flex_unavailable','flex_status':flex_snap.status,'broker_snapshot_id':brk['snapshot_id'],'orders':'disabled'}
    acct_map=config().get('finance',{}).get('accounts') or {}
    brk_accts=brk['accounts']
    brk_nav=Decimal(0); acct_diffs=[]
    flex_by_acct={a.account:a.nav for a in flex_snap.accounts}
    for aid, v in brk_accts.items():
        alias=acct_map.get(aid, aid)
        a_cur=str(v.get('currency') or flex_snap.currency).upper()
        if a_cur==flex_snap.currency:
            fx=Decimal('1')
        elif v.get('fx_rate_to_base') not in (None,'') and dec(v.get('fx_rate_to_base'))>0:
            fx=dec(v['fx_rate_to_base'])
        else:
            return {'status':'missing_fx_rate','account':alias,'currency':a_cur,'flex_status':flex_snap.status,'broker_snapshot_id':brk['snapshot_id'],'orders':'disabled'}
        a_nav=dec(v.get('NetLiquidation') or 0)*fx
        brk_nav+=a_nav
        if alias in flex_by_acct and abs(a_nav-flex_by_acct[alias])>=Decimal('0.01'):
            acct_diffs.append({'account':alias,'flex_nav_base':str(flex_by_acct[alias]),'broker_nav_base':str(a_nav),'nav_delta_base':str(a_nav-flex_by_acct[alias])})
    nav_diff=brk_nav-flex_snap.nav

    flex_by_key={(p.account, p.conid):p.quantity for p in flex_snap.positions}
    brk_by_key={}
    for p in brk['positions']:
        alias=acct_map.get(p.get('accountId'), p.get('account') or p.get('accountId'))
        brk_by_key[(str(alias), str(p['conid']))]=dec(p.get('position') if p.get('position') is not None else p.get('quantity') or 0)

    all_keys=sorted(set(flex_by_key.keys()) | set(brk_by_key.keys()))
    diffs=[]
    for k in all_keys:
        fq=flex_by_key.get(k, Decimal(0)); bq=brk_by_key.get(k, Decimal(0))
        if fq!=bq:
            diffs.append({'account':k[0],'conid':k[1],'flex_qty':str(fq),'broker_qty':str(bq),'qty_delta':str(bq-fq)})
    return {
        'status':'reconciled' if not diffs and not acct_diffs and abs(nav_diff)<Decimal('1.0') else 'timing_or_position_differences',
        'flex_as_of':flex_snap.as_of,
        'flex_snapshot_id':flex_snap.snapshot_id,
        'flex_nav_base':str(flex_snap.nav),
        'broker_as_of':brk['as_of'],
        'broker_snapshot_id':brk['snapshot_id'],
        'broker_nav_base':str(brk_nav),
        'nav_delta_base':str(nav_diff),
        'account_nav_differences':acct_diffs,
        'position_differences':diffs,
        'entitlements':brk['entitlements'],
        'limitations':['Differences between intraday broker state and prior Flex close may reflect new trades, FX, settlement, or corporate actions; Flex history is never overwritten.'],
        'orders':'disabled'
    }

class IBKRTWSAdapter(BrokerReadOnlyAdapter):
    """Concrete Read-Only IBKR TWS / IB Gateway socket adapter using official ibapi with callback completion events. Never implements order methods."""
    adapter_name='ibkr_tws_readonly'
    def __init__(self, host='127.0.0.1', port=7497, client_id=19, timeout_sec=5.0):
        self.host=str(host); self.port=int(port); self.client_id=int(client_id); self.timeout_sec=float(timeout_sec)
        self._client=None; self._connected=False; self._req_seq=9000
        self._accounts=[]; self._summary={}; self._positions=[]; self._contracts={}; self._quotes={}; self._chains={}; self._greeks={}; self._greeks_by_req={}
        self._events={}; self._incomplete_callbacks=[]

    def _next_req_id(self):
        self._req_seq+=1
        return self._req_seq

    def connect(self):
        try:
            from ibapi.client import EClient
            from ibapi.wrapper import EWrapper
        except ImportError:
            return {'status':'unavailable','reason':'ibapi_package_not_installed','live_verified':False,'readonly':True}
        import threading
        done_evt=threading.Event()
        parent=self
        class _ReadOnlyApp(EWrapper, EClient):
            def __init__(self):
                EClient.__init__(self, self)
            def managedAccounts(self, accountsList):
                parent._accounts=[a.strip() for a in str(accountsList).split(',') if a.strip()]
                done_evt.set()
            def accountSummary(self, reqId, account, tag, value, currency):
                acct=parent._summary.setdefault(account, {})
                acct[tag]={'value':value,'currency':currency or ''}
                if tag in {'NetLiquidation','TotalCashValue','AvailableFunds','ExcessLiquidity','BuyingPower','InitMarginReq','MaintMarginReq'}:
                    acct.setdefault('_raw', {})[tag]=value
                if currency and 'currency' not in acct: acct['currency']=currency
            def accountSummaryEnd(self, reqId):
                ev=parent._events.get(('acct', reqId))
                if ev: ev.set()
            def position(self, account, contract, position, avgCost):
                parent._positions.append({'accountId':account,'conid':str(contract.conId),'symbol':contract.symbol,'asset':contract.secType,'currency':contract.currency,'position':str(position),'multiplier':contract.multiplier or '1','exchange':contract.exchange or ''})
            def positionEnd(self):
                ev=parent._events.get(('pos', 0))
                if ev: ev.set()
            def contractDetails(self, reqId, contractDetails):
                c=contractDetails.contract
                parent._contracts[str(c.conId)]={'conid':str(c.conId),'symbol':c.symbol,'secType':c.secType,'currency':c.currency,'exchange':c.exchange,'multiplier':c.multiplier or '1','tradingClass':c.tradingClass or ''}
            def contractDetailsEnd(self, reqId):
                ev=parent._events.get(('cd', reqId))
                if ev: ev.set()
            def securityDefinitionOptionParameter(self, reqId, exchange, underlyingConId, tradingClass, multiplier, expirations, strikes):
                parent._chains.setdefault(reqId, []).append({'exchange':exchange,'underlyingConId':str(underlyingConId),'tradingClass':tradingClass,'multiplier':multiplier,'expirations':sorted(expirations),'strikes':sorted(strikes)})
            def securityDefinitionOptionParameterEnd(self, reqId):
                ev=parent._events.get(('chain', reqId))
                if ev: ev.set()
            def tickPrice(self, reqId, tickType, price, attrib):
                q=parent._quotes.setdefault(str(reqId), {})
                if tickType==1: q['bid']=price
                elif tickType==2: q['ask']=price
                elif tickType==4: q['last']=price
                elif tickType==9: q['close']=price
            def tickOptionComputation(self, reqId, tickType, tickAttrib, impliedVol, delta, optPrice, pvDividend, gamma, vega, theta, undPrice):
                valid_d=(delta is not None and abs(float(delta))<=1.0001)
                parent._greeks_by_req[str(reqId)]={'delta':delta if valid_d else None,'gamma':gamma,'vega':vega,'theta':theta,'underlying_price':undPrice if (undPrice is not None and float(undPrice)>0 and float(undPrice)<1e12) else None,'implied_vol':impliedVol,'status':'live' if valid_d else 'unavailable'}
            def tickSnapshotEnd(self, reqId):
                ev=parent._events.get(('mkt', reqId))
                if ev: ev.set()
            def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=''):
                if errorCode in {502, 504}: done_evt.set()
                if errorCode in {354, 10167, 10168}:
                    parent._incomplete_callbacks.append(f'mkt_entitlement_{errorCode}')
                    ev=parent._events.get(('mkt', reqId))
                    if ev: ev.set()
        app=_ReadOnlyApp()
        try:
            app.connect(self.host, self.port, self.client_id)
            t=threading.Thread(target=app.run, daemon=True); t.start()
            if not done_evt.wait(self.timeout_sec) or not app.isConnected():
                return {'status':'unavailable','reason':'tws_gateway_unreachable','host':self.host,'port':self.port,'live_verified':False,'readonly':True}
            self._client=app; self._connected=True
            return {'status':'connected','adapter':self.adapter_name,'readonly':True,'accounts':self._accounts}
        except Exception as exc:
            return {'status':'unavailable','reason':f'tws_connect_failed:{type(exc).__name__}','live_verified':False,'readonly':True}

    def health(self):
        has_live_greeks=any(g.get('status')=='live' for g in self._greeks.values())
        return {'connected':self._connected,'adapter':self.adapter_name,'readonly':True,'host':self.host,'port':self.port,'partial':bool(self._incomplete_callbacks),'incomplete_callbacks':list(self._incomplete_callbacks),'entitlements':{'equities_live':self._connected and not any('354' in x for x in self._incomplete_callbacks),'options_greeks_live':has_live_greeks}}

    def list_managed_accounts(self):
        return list(self._accounts)

    def account_summary(self, account_id):
        if not self._connected or not self._client: raise RuntimeError('not connected to TWS')
        import threading
        req_id=self._next_req_id()
        ev=threading.Event(); self._events[('acct', req_id)]=ev
        self._client.reqAccountSummary(req_id, 'All', 'NetLiquidation,TotalCashValue,AvailableFunds,ExcessLiquidity,BuyingPower,InitMarginReq,MaintMarginReq')
        completed=ev.wait(self.timeout_sec)
        try: self._client.cancelAccountSummary(req_id)
        except Exception: pass
        if not completed: self._incomplete_callbacks.append(f'accountSummary_timeout_{req_id}')
        raw_acct=dict(self._summary.get(account_id, {}))
        flat={k:(v['value'] if isinstance(v,dict) else v) for k,v in raw_acct.items() if k!='_raw'}
        flat['tag_currencies']={k:v.get('currency','') for k,v in raw_acct.items() if isinstance(v,dict)}
        flat['complete']=completed
        return flat

    def positions(self):
        if not self._connected or not self._client: raise RuntimeError('not connected to TWS')
        import threading
        self._positions.clear()
        ev=threading.Event(); self._events[('pos', 0)]=ev
        self._client.reqPositions()
        completed=ev.wait(self.timeout_sec)
        try: self._client.cancelPositions()
        except Exception: pass
        if not completed: self._incomplete_callbacks.append('positions_timeout')
        return list(self._positions)

    def contract_details(self, conid):
        if str(conid) in self._contracts:
            return dict(self._contracts[str(conid)])
        if not self._connected or not self._client: return {}
        import threading
        from ibapi.contract import Contract
        c=Contract(); c.conId=int(conid)
        req_id=self._next_req_id()
        ev=threading.Event(); self._events[('cd', req_id)]=ev
        self._client.reqContractDetails(req_id, c)
        if not ev.wait(self.timeout_sec): self._incomplete_callbacks.append(f'contractDetails_timeout_{conid}')
        return dict(self._contracts.get(str(conid), {}))

    def quotes(self, conids):
        if not self._connected or not self._client: return {}
        import threading
        from ibapi.contract import Contract
        out={}
        for cid in conids:
            req_id=self._next_req_id()
            c=Contract(); c.conId=int(cid); c.exchange='SMART'
            ev=threading.Event(); self._events[('mkt', req_id)]=ev
            self._client.reqMktData(req_id, c, '', True, False, [])
            if not ev.wait(self.timeout_sec): self._incomplete_callbacks.append(f'quote_timeout_{cid}')
            try: self._client.cancelMktData(req_id)
            except Exception: pass
            if str(req_id) in self._quotes:
                q_d=dict(self._quotes[str(req_id)])
                bid=q_d.get('bid'); ask=q_d.get('ask')
                if (bid is not None and bid<=0) or (ask is not None and ask<=0) or (bid is not None and ask is not None and bid>ask):
                    q_d['status']='invalid_or_crossed'
                else:
                    q_d['status']='live'
                out[str(cid)]=q_d
            if str(req_id) in self._greeks_by_req:
                self._greeks[str(cid)]=dict(self._greeks_by_req[str(req_id)])
        return out

    def option_chain(self, symbol, underlying_conid=0):
        if not self._connected or not self._client or not underlying_conid: return []
        import threading
        req_id=self._next_req_id()
        ev=threading.Event(); self._events[('chain', req_id)]=ev
        self._client.reqSecDefOptParams(req_id, str(symbol).upper(), '', 'STK', int(underlying_conid))
        if not ev.wait(self.timeout_sec): self._incomplete_callbacks.append(f'chain_timeout_{symbol}')
        return list(self._chains.get(req_id, []))

    def option_greeks(self, conid):
        if str(conid) not in self._greeks and self._connected:
            self.quotes([conid])
        return dict(self._greeks.get(str(conid), {'conid':str(conid),'status':'unavailable','delta':None}))

    def disconnect(self):
        if self._client and self._connected:
            try: self._client.disconnect()
            except Exception: pass
        self._connected=False

def sync_broker_live(host='127.0.0.1', port=7497, client_id=19):
    """Attempt read-only live TWS/IB Gateway sync; fails closed and never sets live_verified=True on partial/unreachable state."""
    bcfg=(config().get('finance') or {}).get('broker') or {}
    adapter=IBKRTWSAdapter(bcfg.get('host', host), bcfg.get('port', port), bcfg.get('client_id', client_id))
    conn=adapter.connect()
    if conn.get('status')!='connected':
        return conn
    try:
        accts={aid:adapter.account_summary(aid) for aid in adapter.list_managed_accounts()}
        pos=adapter.positions()
        opt_cids=[p['conid'] for p in pos if str(p.get('asset','')).upper()=='OPT']
        if opt_cids: adapter.quotes(opt_cids)
        greeks={str(cid):adapter.option_greeks(cid) for cid in opt_cids}
        h=adapter.health()
        live_ok=bool(h['connected'] and not h['partial'])
        sid=_persist_broker_snapshot(adapter.adapter_name, now(), accts, pos, greeks, h['entitlements'], capture_status='ok' if live_ok else 'partial', live_verified=live_ok, incomplete_callbacks=h['incomplete_callbacks'])
        return {'status':'ok' if live_ok else 'partial','snapshot_id':sid,'adapter':adapter.adapter_name,'accounts':len(accts),'positions':len(pos),'live_verified':live_ok,'incomplete_callbacks':h['incomplete_callbacks'],'orders':'disabled'}
    finally:
        adapter.disconnect()

def broker_state_for_risk():
    """Return latest broker snapshot metadata & option Greeks for risk enrichment, never overwriting Flex history."""
    brk=latest_broker_snapshot()
    if brk.get('capture_status') not in {'ok','replay'}:
        return {'status':brk.get('capture_status','unavailable'),'live_verified':False,'capture_status':brk.get('capture_status','unavailable'),'greeks':{}}
    return {'status':'ok','capture_status':brk['capture_status'],'snapshot_id':brk['snapshot_id'],'as_of':brk['as_of'],'entitlements':brk['entitlements'],'accounts':brk['accounts'],'greeks':brk['greeks'],'live_verified':bool(brk.get('live_verified'))}

def enrich_snapshot_with_broker(csnap, max_age_seconds=300):
    """Enrich a Flex PortfolioSnapshot ONLY with fresh, validated live broker option Greeks and margin state; never enriches from stale, partial, or replay captures."""
    b_state=broker_state_for_risk()
    if not b_state.get('live_verified') or b_state.get('capture_status')!='ok':
        return csnap
    try:
        obs_dt=datetime.fromisoformat(str(b_state.get('as_of') or ''))
        if obs_dt.tzinfo is None: obs_dt=obs_dt.replace(tzinfo=timezone.utc)
        age=(datetime.now(timezone.utc)-obs_dt.astimezone(timezone.utc)).total_seconds()
        if age< -60.0 or age>float(max_age_seconds):
            return csnap
    except Exception:
        return csnap
    g_map=b_state.get('greeks') or {}
    acct_map=config().get('finance',{}).get('accounts') or {}
    brk_accts={acct_map.get(k,k):v for k,v in (b_state.get('accounts') or {}).items()}
    new_accts=[]
    for a in csnap.accounts:
        ba=brk_accts.get(a.account) or {}
        bp=dec(ba['BuyingPower']) if ba.get('BuyingPower') not in (None,'') else a.buying_power
        new_accts.append(AccountRecord(account=a.account, tax_bucket=a.tax_bucket, nav=a.nav, cash=a.cash, allow_margin=a.allow_margin, buying_power=bp, source=a.source))
    new_pos=[]
    for p in csnap.positions:
        g=g_map.get(p.conid)
        if p.asset=='OPT' and g and g.get('delta') is not None:
            new_pos.append(PositionRecord(
                account=p.account, conid=p.conid, symbol=p.symbol, asset=p.asset, currency=p.currency,
                quantity=p.quantity, multiplier=p.multiplier, market_value_base=p.market_value_base,
                underlying_conid=p.underlying_conid, cost_basis=p.cost_basis,
                underlying_price=dec(g['underlying_price']) if g.get('underlying_price') is not None else p.underlying_price,
                underlying_currency=p.underlying_currency, fx_rate_to_base=p.fx_rate_to_base,
                delta=dec(g['delta']), source=f"{p.source}+broker:{b_state['snapshot_id']}"
            ))
        else:
            new_pos.append(p)
    new_sid=compute_snapshot_id(csnap.as_of, csnap.currency, new_accts, new_pos)
    return PortfolioSnapshot(
        snapshot_id=new_sid, as_of=csnap.as_of, status=csnap.status,
        stale=csnap.stale, advice_allowed=csnap.advice_allowed, positions_complete=csnap.positions_complete,
        currency=csnap.currency, nav=csnap.nav, cash=csnap.cash, accounts=tuple(new_accts),
        positions=tuple(new_pos), limitations=csnap.limitations
    )
