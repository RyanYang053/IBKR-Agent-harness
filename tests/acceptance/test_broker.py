import sys, types
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from personal_agent import broker, core, finance, quant, server

def _install_fake_ibapi(monkeypatch, *, timeout_acct=False, sentinel_delta=False, crossed_quote=False):
    mod_ib = types.ModuleType('ibapi')
    mod_cl = types.ModuleType('ibapi.client')
    mod_wr = types.ModuleType('ibapi.wrapper')
    mod_ct = types.ModuleType('ibapi.contract')

    class EWrapper: pass
    class Contract:
        def __init__(self): self.conId=0; self.symbol=''; self.secType=''; self.currency=''; self.exchange=''; self.multiplier='1'
    class EClient:
        def __init__(self, wrapper): self._w = wrapper; self._conn = False
        def connect(self, host, port, client_id):
            self._conn = True
            self._w.managedAccounts('U0000001,U0000002')
        def isConnected(self): return self._conn
        def run(self): pass
        def disconnect(self): self._conn = False
        def reqAccountSummary(self, req_id, group, tags):
            if timeout_acct: return
            self._w.accountSummary(req_id, 'U0000001', 'NetLiquidation', '5000', 'CAD')
            self._w.accountSummary(req_id, 'U0000002', 'NetLiquidation', '5000', 'CAD')
            self._w.accountSummaryEnd(req_id)
        def cancelAccountSummary(self, req_id): pass
        def reqPositions(self):
            c = Contract(); c.conId = 9001; c.symbol = 'OPT_A'; c.secType = 'OPT'; c.currency = 'CAD'; c.multiplier = '100'
            self._w.position('U0000001', c, 1, 5.0)
            self._w.positionEnd()
        def cancelPositions(self): pass
        def reqMktData(self, req_id, contract, genericTickList, snapshot, regulatorySnapshot, mktDataOptions):
            if crossed_quote:
                self._w.tickPrice(req_id, 1, 10.0, None); self._w.tickPrice(req_id, 2, 9.0, None)
            else:
                self._w.tickPrice(req_id, 1, 4.9, None); self._w.tickPrice(req_id, 2, 5.1, None)
            d = 1.7976931348623157e+308 if sentinel_delta else 0.55
            self._w.tickOptionComputation(req_id, 13, 0, 0.30, d, 5.0, 0.0, 0.02, 0.1, -0.05, 100.0)
            self._w.tickSnapshotEnd(req_id)
        def cancelMktData(self, req_id): pass
        def reqSecDefOptParams(self, req_id, symbol, futFopExchange, underlyingSecType, underlyingConId):
            self._w.securityDefinitionOptionParameter(req_id, 'SMART', underlyingConId, symbol, '100', ['20270115'], [100.0, 110.0])
            self._w.securityDefinitionOptionParameterEnd(req_id)

    mod_cl.EClient = EClient; mod_wr.EWrapper = EWrapper; mod_ct.Contract = Contract
    monkeypatch.setitem(sys.modules, 'ibapi', mod_ib)
    monkeypatch.setitem(sys.modules, 'ibapi.client', mod_cl)
    monkeypatch.setitem(sys.modules, 'ibapi.wrapper', mod_wr)
    monkeypatch.setitem(sys.modules, 'ibapi.contract', mod_ct)

def test_B01_tws_adapter_callbacks_and_timeout(isolated_env, monkeypatch):
    _install_fake_ibapi(monkeypatch, timeout_acct=False)
    ad = broker.IBKRTWSAdapter(timeout_sec=0.3)
    assert ad.connect()['status'] == 'connected'
    s1 = ad.account_summary('U0000001')
    assert s1['complete'] is True and s1['tag_currencies']['NetLiquidation'] == 'CAD'
    pos = ad.positions()
    assert len(pos) == 1 and pos[0]['conid'] == '9001'
    ad.disconnect()

    _install_fake_ibapi(monkeypatch, timeout_acct=True)
    ad_to = broker.IBKRTWSAdapter(timeout_sec=0.05)
    ad_to.connect()
    s_to = ad_to.account_summary('U0000001')
    assert s_to['complete'] is False and len(ad_to.health()['incomplete_callbacks']) >= 1
    ad_to.disconnect()

def test_B02_partial_capture_never_marked_live_verified(isolated_env, monkeypatch):
    from importlib import reload
    _install_fake_ibapi(monkeypatch, timeout_acct=True)
    orig_cls = broker.IBKRTWSAdapter
    with patch.object(broker, 'IBKRTWSAdapter', side_effect=lambda *a, **kw: orig_cls(*a, timeout_sec=0.05)):
        res = broker.sync_broker_live()
    assert res['status'] == 'partial' and res['live_verified'] is False
    # Reload broker module to simulate process restart; persisted state must remain partial & not live_verified
    reload(broker)
    persisted = broker.latest_broker_snapshot()
    b_state = broker.broker_state_for_risk()
    assert persisted['capture_status'] == 'partial' and persisted['live_verified'] is False
    assert b_state['live_verified'] is False and len(persisted['incomplete_callbacks']) >= 1

    # Replay capture also never becomes live_verified
    rep = broker.sync_broker_replay({'as_of': date.today().isoformat(), 'accounts': {}, 'positions': []})
    assert rep['capture_status'] == 'replay' and rep['live_verified'] is False
    assert broker.latest_broker_snapshot()['live_verified'] is False

def test_B03_reconciliation_per_account_and_currency(isolated_env):
    d = date.today().isoformat()
    with core.db('finance') as c:
        finance.schema(c)
        c.execute('INSERT INTO sources VALUES(?,?)', ('s', d + 'T10:00:00'))
        c.execute('INSERT INTO nav VALUES(?,?,?,?,?,?)', ('margin', d, 'CAD', '1000', '1000', 's'))
        c.execute('INSERT INTO nav VALUES(?,?,?,?,?,?)', ('tfsa', d, 'CAD', '1000', '1000', 's'))
        c.execute('INSERT INTO snapshots VALUES(?,?,?,1)', ('margin', d, 's'))
        c.execute('INSERT INTO snapshots VALUES(?,?,?,1)', ('tfsa', d, 's'))
    # Opposite +100/-100 errors with identical combined NAV=2000 must NOT reconcile
    broker.sync_broker_replay({
        'as_of': d + 'T14:00:00',
        'accounts': {'U0000001': {'NetLiquidation': '1100', 'currency': 'CAD'}, 'U0000002': {'NetLiquidation': '900', 'currency': 'CAD'}},
        'positions': []
    })
    rec = broker.reconcile_live_to_flex()
    assert rec['status'] == 'timing_or_position_differences' and len(rec['account_nav_differences']) == 2

    # Missing FX blocks
    broker.sync_broker_replay({
        'as_of': d + 'T14:01:00',
        'accounts': {'U0000001': {'NetLiquidation': '1000', 'currency': 'USD'}},
        'positions': []
    })
    assert broker.reconcile_live_to_flex()['status'] == 'missing_fx_rate'

def test_B04_quote_reqid_9001_vs_conid_9001_and_sentinel_delta(isolated_env, monkeypatch):
    _install_fake_ibapi(monkeypatch, sentinel_delta=True, crossed_quote=True)
    ad = broker.IBKRTWSAdapter(timeout_sec=0.2)
    ad.connect()
    qs = ad.quotes(['9001'])
    assert qs['9001']['status'] == 'invalid_or_crossed'
    assert ad.option_greeks('9001')['status'] == 'unavailable' and ad.option_greeks('9001')['delta'] is None
    ad.disconnect()

def test_B05_broker_enrichment_preserves_flex_and_updates_hash(isolated_env, monkeypatch):
    d = date.today().isoformat()
    with core.db('finance') as c:
        finance.schema(c)
        c.execute('INSERT INTO sources VALUES(?,?)', ('s', d + 'T10:00:00'))
        c.execute('INSERT INTO nav VALUES(?,?,?,?,?,?)', ('margin', d, 'CAD', '10000', '5000', 's'))
        c.execute('INSERT INTO nav VALUES(?,?,?,?,?,?)', ('tfsa', d, 'CAD', '0', '0', 's'))
        c.execute('INSERT INTO snapshots VALUES(?,?,?,1)', ('margin', d, 's'))
        c.execute('INSERT INTO snapshots VALUES(?,?,?,1)', ('tfsa', d, 's'))
        c.execute('INSERT INTO positions VALUES(?,?,?,?,?,?,?,?,?,?,?)', ('margin', d, '9001', 'OPT_A', 'OPT', 'CAD', '1', '500', '1000', '500', '1'))
    base_snap = finance.load_full_portfolio_snapshot()
    # Replay capture must NOT enrich canonical snapshot
    broker.sync_broker_replay({'as_of': d, 'entitlements': {'options_greeks_live': True}, 'accounts': {}, 'positions': [{'accountId': 'U0000001', 'conid': '9001', 'asset': 'OPT', 'position': '1'}], 'greeks': {'9001': {'delta': 0.2, 'underlying_price': 100}}})
    assert broker.enrich_snapshot_with_broker(base_snap).snapshot_id == base_snap.snapshot_id

    # Valid live capture enriches and produces distinct snapshot_ids for delta=0.2 vs delta=0.8
    broker._persist_broker_snapshot('ibkr_tws_readonly', d, {'U0000001': {'BuyingPower': '4000'}}, [], {'9001': {'delta': 0.2, 'underlying_price': 100, 'status': 'live'}}, {'options_greeks_live': True}, capture_status='ok', live_verified=True)
    s1 = broker.enrich_snapshot_with_broker(base_snap)
    broker._persist_broker_snapshot('ibkr_tws_readonly', d, {'U0000001': {'BuyingPower': '4000'}}, [], {'9001': {'delta': 0.8, 'underlying_price': 100, 'status': 'live'}}, {'options_greeks_live': True}, capture_status='ok', live_verified=True)
    s2 = broker.enrich_snapshot_with_broker(base_snap)
    assert s1.positions[0].delta == Decimal('0.2') and s2.positions[0].delta == Decimal('0.8')
    assert s1.snapshot_id != base_snap.snapshot_id and s1.snapshot_id != s2.snapshot_id

def test_B06_option_chain_qualification_and_staleness(isolated_env, monkeypatch):
    _install_fake_ibapi(monkeypatch)
    ad = broker.IBKRTWSAdapter(timeout_sec=0.2); ad.connect()
    ch_tws = ad.option_chain('QQQ', underlying_conid=12345)
    assert len(ch_tws) == 1 and ch_tws[0]['multiplier'] == '100'
    ad.disconnect()
    res = quant.analyze_option_chain('QQQ', [{'right': 'call', 'strike': 110, 'days_to_expiry': 45, 'bid': 1.6, 'ask': 1.8, 'open_interest': 50, 'quote_age_seconds': 600}], spot=100)
    assert len(res['eligible_contracts']) == 0 and res['rejected_contracts'][0]['reject_reason'] == 'stale_quote'

def test_B07_readonly_broker_contract_enforced(isolated_env):
    for cls in (broker.BrokerReadOnlyAdapter, broker.ReplayBrokerAdapter, broker.IBKRTWSAdapter):
        for m in broker.FORBIDDEN_BROKER_METHODS:
            assert not hasattr(cls, m)
    res = broker.sync_broker_live(port=59998)
    assert res['status'] == 'unavailable' and res['live_verified'] is False
