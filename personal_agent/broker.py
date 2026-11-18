"""Strictly Read-Only Broker Adapter (TWS/IB Gateway + Offline Replay) and Live-to-Flex Reconciliation.
Zero order execution: placeOrder, modifyOrder, and cancelOrder are permanently absent."""
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib, json
from .core import bounded, config, db, now
from .finance import AccountRecord, PortfolioSnapshot, PositionRecord, dec, load_full_portfolio_snapshot

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
        created_at TEXT NOT NULL
    );
    ''')

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

def _persist_broker_snapshot(adapter_name, as_of, accounts, positions, greeks, entitlements):
    payload={'adapter':adapter_name,'as_of':as_of,'accounts':accounts,'positions':positions,'greeks':greeks}
    sid='brk_'+hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode('utf-8')).hexdigest()[:16]
    with db('finance') as c:
        schema(c)
        c.execute('INSERT OR REPLACE INTO broker_snapshots VALUES(?,?,?,?,?,?,?,?)',(
            sid, adapter_name, as_of,
            json.dumps(accounts, ensure_ascii=False),
            json.dumps(positions, ensure_ascii=False),
            json.dumps(greeks, ensure_ascii=False),
            json.dumps(entitlements, ensure_ascii=False),
            now()
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
        sid=_persist_broker_snapshot(adapter.adapter_name, h['as_of'], accts, pos, greeks, h['entitlements'])
        return {'status':'ok','snapshot_id':sid,'adapter':adapter.adapter_name,'as_of':h['as_of'],'accounts':len(accts),'positions':len(pos),'entitlements':h['entitlements'],'orders':'disabled'}
    finally:
        adapter.disconnect()

def latest_broker_snapshot():
    with db('finance') as c:
        schema(c)
        row=c.execute('SELECT * FROM broker_snapshots ORDER BY created_at DESC LIMIT 1').fetchone()
    if not row:
        return {'status':'unavailable','reason':'no_broker_snapshot_recorded','live_acceptance_status':'offline_only_not_connected'}
    return {
        'status':'ok',
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
    brk_nav=sum((dec(v.get('NetLiquidation') or 0) for v in brk_accts.values()), Decimal(0))
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
        'status':'reconciled' if not diffs and abs(nav_diff)<Decimal('1.0') else 'timing_or_position_differences',
        'flex_as_of':flex_snap.as_of,
        'flex_snapshot_id':flex_snap.snapshot_id,
        'flex_nav_base':str(flex_snap.nav),
        'broker_as_of':brk['as_of'],
        'broker_snapshot_id':brk['snapshot_id'],
        'broker_nav_base':str(brk_nav),
        'nav_delta_base':str(nav_diff),
        'position_differences':diffs,
        'entitlements':brk['entitlements'],
        'limitations':['Differences between intraday broker state and prior Flex close may reflect new trades, FX, settlement, or corporate actions; Flex history is never overwritten.'],
        'orders':'disabled'
    }

class IBKRTWSAdapter(BrokerReadOnlyAdapter):
    """Concrete Read-Only IBKR TWS / IB Gateway socket adapter using official ibapi when installed. Never implements order methods."""
    adapter_name='ibkr_tws_readonly'
    def __init__(self, host='127.0.0.1', port=7497, client_id=19, timeout_sec=5.0):
        self.host=str(host); self.port=int(port); self.client_id=int(client_id); self.timeout_sec=float(timeout_sec)
        self._client=None; self._connected=False
        self._accounts=[]; self._summary={}; self._positions=[]; self._contracts={}; self._quotes={}; self._greeks={}

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
                parent._summary.setdefault(account, {})[tag]=value
                if currency: parent._summary[account]['currency']=currency
            def position(self, account, contract, position, avgCost):
                parent._positions.append({'accountId':account,'conid':str(contract.conId),'symbol':contract.symbol,'asset':contract.secType,'currency':contract.currency,'position':str(position),'multiplier':contract.multiplier or '1'})
            def tickOptionComputation(self, reqId, tickType, tickAttrib, impliedVol, delta, optPrice, pvDividend, gamma, vega, theta, undPrice):
                parent._greeks[str(reqId)]={'delta':delta,'gamma':gamma,'vega':vega,'theta':theta,'underlying_price':undPrice,'implied_vol':impliedVol,'status':'live' if delta is not None else 'unavailable'}
            def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=''):
                if errorCode in {502, 504}: done_evt.set()
        app=_ReadOnlyApp()
        try:
            app.connect(self.host, self.port, self.client_id)
            t=threading.Thread(target=app.run, daemon=True); t.start()
            done_evt.wait(self.timeout_sec)
            if not app.isConnected():
                return {'status':'unavailable','reason':'tws_gateway_unreachable','host':self.host,'port':self.port,'live_verified':False,'readonly':True}
            self._client=app; self._connected=True
            return {'status':'connected','adapter':self.adapter_name,'readonly':True,'accounts':self._accounts}
        except Exception as exc:
            return {'status':'unavailable','reason':f'tws_connect_failed:{type(exc).__name__}','live_verified':False,'readonly':True}

    def health(self):
        return {'connected':self._connected,'adapter':self.adapter_name,'readonly':True,'host':self.host,'port':self.port,'entitlements':{'equities_live':self._connected,'options_greeks_live':False}}

    def list_managed_accounts(self):
        return list(self._accounts)

    def account_summary(self, account_id):
        if not self._connected or not self._client: raise RuntimeError('not connected to TWS')
        import time as _t
        self._client.reqAccountSummary(9001, 'All', 'NetLiquidation,TotalCashValue,AvailableFunds,ExcessLiquidity,BuyingPower,InitMarginReq,MaintMarginReq')
        _t.sleep(min(1.5, self.timeout_sec))
        return dict(self._summary.get(account_id, {}))

    def positions(self):
        if not self._connected or not self._client: raise RuntimeError('not connected to TWS')
        import time as _t
        self._positions.clear()
        self._client.reqPositions()
        _t.sleep(min(1.5, self.timeout_sec))
        return list(self._positions)

    def contract_details(self, conid):
        return dict(self._contracts.get(str(conid), {}))

    def quotes(self, conids):
        return {str(c):dict(self._quotes[str(c)]) for c in conids if str(c) in self._quotes}

    def option_chain(self, symbol):
        return []

    def option_greeks(self, conid):
        return dict(self._greeks.get(str(conid), {'conid':str(conid),'status':'unavailable','delta':None}))

    def disconnect(self):
        if self._client and self._connected:
            try: self._client.disconnect()
            except Exception: pass
        self._connected=False

def sync_broker_live(host='127.0.0.1', port=7497, client_id=19):
    """Attempt read-only live TWS/IB Gateway sync; fails closed with explicit reason if unreachable."""
    bcfg=(config().get('finance') or {}).get('broker') or {}
    adapter=IBKRTWSAdapter(bcfg.get('host', host), bcfg.get('port', port), bcfg.get('client_id', client_id))
    conn=adapter.connect()
    if conn.get('status')!='connected':
        return conn
    try:
        accts={aid:adapter.account_summary(aid) for aid in adapter.list_managed_accounts()}
        pos=adapter.positions()
        greeks={str(p['conid']):adapter.option_greeks(p['conid']) for p in pos if str(p.get('asset','')).upper()=='OPT'}
        h=adapter.health()
        sid=_persist_broker_snapshot(adapter.adapter_name, now(), accts, pos, greeks, h['entitlements'])
        return {'status':'ok','snapshot_id':sid,'adapter':adapter.adapter_name,'accounts':len(accts),'positions':len(pos),'live_verified':True,'orders':'disabled'}
    finally:
        adapter.disconnect()

def broker_state_for_risk():
    """Return latest broker snapshot metadata & option Greeks for risk enrichment, never overwriting Flex history."""
    brk=latest_broker_snapshot()
    if brk.get('status')!='ok':
        return {'status':'unavailable','live_verified':False,'greeks':{}}
    return {'status':'ok','snapshot_id':brk['snapshot_id'],'as_of':brk['as_of'],'entitlements':brk['entitlements'],'accounts':brk['accounts'],'greeks':brk['greeks'],'live_verified':brk['adapter']=='ibkr_tws_readonly'}
