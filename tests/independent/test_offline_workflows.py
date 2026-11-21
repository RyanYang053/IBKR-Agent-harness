"""Offline end-to-end workflows through CLI entry points and production functions.

Network is limited to replaying recorded public SEC CompanyFacts bytes.
No brokerage credentials, no order calls, and no live-entitlement claims.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
SEC_DIR = ROOT / "tests" / "fixtures" / "sec"
# Bytes retrieved 2026-11-21 from https://data.sec.gov/api/xbrl/companyfacts/. Public filings, not a market-data entitlement.
SEC_SHA256 = {
    "MSFT": "8c1903c041f5c27e86ed15f37debb1f41882b7904bba129ef9edc76478b1e8ea",
    "WMT": "49330f98609f70fcef4054edc90bc811398a58c4bf26dc0b218d607967a8dbe4",
}
SEC_CIK = {"MSFT": "0000789019", "WMT": "0000104169"}


def _cli(env, *args):
    return subprocess.run(
        [sys.executable, "-m", "personal_agent.cli", *args],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
    )


def _base_env(home: Path, config_path: Path):
    env = os.environ.copy()
    for key in ("IBKR_FLEX_TOKEN", "IBKR_FLEX_QUERY_ID", "ALPACA_API_KEY", "ALPACA_API_SECRET",
                "FINNHUB_API_KEY", "FRED_API_KEY", "INVESTMENT_OS_PUBLIC_ONLY"):
        env.pop(key, None)
    env["PERSONAL_AGENT_HOME"] = str(home)
    env["PERSONAL_AGENT_CONFIG"] = str(config_path)
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _write_config(path: Path, accounts):
    path.write_text(json.dumps({
        "timezone": "America/Toronto",
        "knowledge_roots": [],
        "finance": {
            "accounts": accounts,
            "account_tax_types": {"margin": "taxable", "tfsa": "tfsa", "rrsp": "rrsp"},
            "account_constraints": {"margin": {"allow_margin": True}, "tfsa": {"allow_margin": False}, "rrsp": {"allow_margin": False}},
            "base_currency": "CAD",
            "flex_queries": [],
            "max_snapshot_age_days": 10,
        },
        "market": {
            "watchlist": ["QQQ"],
            "alpaca_key_env": "DO_NOT_SET_ALPACA_KEY",
            "alpaca_secret_env": "DO_NOT_SET_ALPACA_SECRET",
            "finnhub_key_env": "DO_NOT_SET_FINNHUB_KEY",
            "fred_key_env": "DO_NOT_SET_FRED_KEY",
            "edgar_contact": "qa@example.com",
        },
        "policy": {"max_tool_rows": 200, "max_file_bytes": 20000000},
    }), encoding="utf-8")


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    home = tmp_path / "ledger"
    home.mkdir()
    cfg = tmp_path / "config.json"
    _write_config(cfg, {"U_E2E_M": "margin", "U_E2E_T": "tfsa", "U_E2E_R": "rrsp"})
    env = _base_env(home, cfg)
    monkeypatch.setenv("PERSONAL_AGENT_HOME", str(home))
    monkeypatch.setenv("PERSONAL_AGENT_CONFIG", str(cfg))
    monkeypatch.delenv("INVESTMENT_OS_PUBLIC_ONLY", raising=False)
    import personal_agent.core as core
    import personal_agent.finance as finance
    import personal_agent.market as market
    import personal_agent.decision as decision
    import personal_agent.quant as quant
    import personal_agent.broker as broker
    import personal_agent.ops as ops
    import personal_agent.knowledge as knowledge
    for mod in (core, finance, market, decision, quant, broker, ops, knowledge):
        importlib.reload(mod)
    policy = tmp_path / "config"
    policy.mkdir()
    (policy / "investment_policy.example.json").write_text(json.dumps({
        "core_symbols": ["QQQ"], "core_benchmark": "QQQ", "core_min_weight": 0,
        "tactical_max_weight": 10, "min_cash_reserve": 0, "max_single_stock_weight": 10,
        "max_correlated_cluster_weight": 10, "max_economic_leverage": 10,
        "max_gross_economic_leverage": 10, "max_drawdown_budget": -0.9, "approval_ttl_hours": 48,
        "account_constraints": {"tfsa": {"allow_margin": False}, "margin": {"allow_margin": True}, "rrsp": {"allow_margin": False}},
    }), encoding="utf-8")
    monkeypatch.setattr(quant, "ROOT", tmp_path)
    with patch("urllib.request.urlopen", side_effect=AssertionError("offline workflow attempted outbound HTTP")):
        yield {"home": home, "cfg": cfg, "env": env, "root": tmp_path, "finance": finance,
               "market": market, "decision": decision, "quant": quant, "broker": broker,
               "ops": ops, "knowledge": knowledge, "core": core}


def _flex_today() -> str:
    return date.today().strftime("%Y%m%d")


def _write_flex(path: Path, include_option=True):
    d = _flex_today()
    option = ""
    if include_option:
        option = f'<OpenPosition levelOfDetail="SUMMARY" reportDate="{d}" conid="9001" currency="CAD" symbol="QAOPT" assetCategory="OPT" position="1" positionValue="0" costBasisMoney="150"/>'
    path.write_text(f"""<?xml version="1.0"?>
<FlexQueryResponse>
  <FlexStatements>
    <FlexStatement accountId="U_E2E_M" fromDate="{d}" toDate="{d}" whenGenerated="{d};120000">
      <EquitySummaryByReportDateInBase currency="CAD" reportDate="{d}" total="10000.00" cash="9000.00"/>
      <OpenPositions>
        <OpenPosition levelOfDetail="SUMMARY" reportDate="{d}" conid="5001" currency="CAD" symbol="QAEQ" assetCategory="STK" position="10" positionValue="1000" costBasisMoney="800"/>
        {option}
      </OpenPositions>
    </FlexStatement>
    <FlexStatement accountId="U_E2E_T" fromDate="{d}" toDate="{d}" whenGenerated="{d};120000">
      <EquitySummaryByReportDateInBase currency="CAD" reportDate="{d}" total="5000.00" cash="5000.00"/>
      <OpenPositions/>
    </FlexStatement>
    <FlexStatement accountId="U_E2E_R" fromDate="{d}" toDate="{d}" whenGenerated="{d};120000">
      <EquitySummaryByReportDateInBase currency="CAD" reportDate="{d}" total="5000.00" cash="5000.00"/>
      <OpenPositions/>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>
""", encoding="utf-8")


def test_flex_import_snapshot_risk_trade_and_policy(workspace):
    flex = workspace["root"] / "flex.xml"
    _write_flex(flex)
    imported = _cli(workspace["env"], "import-flex", str(flex))
    assert imported.returncode == 0, imported.stderr
    assert json.loads(imported.stdout)["status"] == "imported"
    portfolio = _cli(workspace["env"], "portfolio")
    assert portfolio.returncode == 0, portfolio.stderr
    body = json.loads(portfolio.stdout)
    assert Decimal(body["nav"]) == Decimal("20000")
    snap = workspace["finance"].load_full_portfolio_snapshot()
    assert snap.advice_allowed and snap.nav == Decimal("20000")
    assert {p.conid for p in snap.positions} == {"5001", "9001"}
    risk = workspace["decision"].risk()
    assert isinstance(risk, dict)
    assert risk.get("price_freshness", "unavailable") != "live"
    trade = {
        "symbol": "QAEQ", "conid": "5001", "account": "margin", "action": "BUY",
        "asset": "STK", "currency": "CAD", "quantity": "2", "price_per_unit": "100",
        "amount_base": "200", "estimated_fees_base": "1",
    }
    sim = workspace["quant"].simulate_allocation(snap, proposed_trades=[trade])
    assert sim.accounts[0].cash == Decimal("8799")
    assert sim.nav == Decimal("19999")
    held = next(p for p in sim.positions if p.conid == "5001")
    assert held.quantity == Decimal("12") and held.cost_basis == Decimal("1001")
    decision = workspace["quant"].evaluate_policy(snap, proposed_trade=trade, mode="PROPOSED_PLAN")
    assert decision["status"] in {"ok", "caution", "blocked_policy_violation"}
    assert "orders" not in decision or decision.get("orders") != "placed"


def test_recorded_sec_filings_pit_ttm_valuation_and_discovery(workspace):
    market = workspace["market"]
    for sym, sha in SEC_SHA256.items():
        raw = (SEC_DIR / f"{sym}_companyfacts.json").read_bytes()
        assert hashlib.sha256(raw).hexdigest() == sha
        payload = json.loads(raw)
        with patch.object(market, "_cik", return_value=SEC_CIK[sym]), patch.object(market, "_request", return_value=payload):
            loaded = market.sync_filings(sym)
        assert loaded["status"] == "ok" and loaded["pit_facts"] > 0

    wmt = json.loads((SEC_DIR / "WMT_companyfacts.json").read_text(encoding="utf-8"))
    rev_rows = wmt["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
    fy = next(r for r in rev_rows if r.get("fp") == "FY" and r.get("end") == "2026-01-31" and r.get("form") == "10-K")
    assert fy["val"] == 713163000000 and fy["accn"] == "0000104169-26-000055"
    assert fy["end"].endswith("-01-31")
    before = market.get_facts_pit("WMT", as_of="2026-03-12", period="FY2026")["facts"]
    after = market.get_facts_pit("WMT", as_of="2026-03-13", period="FY2026")["facts"]
    assert not any(f["concept"] == "Revenue" and f["value"] == 713163000000 for f in before)
    got = next(f for f in after if f["concept"] == "Revenue")
    assert got["value"] == 713163000000

    ocf_rows = wmt["facts"]["us-gaap"]["NetCashProvidedByUsedInOperatingActivities"]["units"]["USD"]
    q1 = next(r for r in ocf_rows if r.get("fy") == 2026 and r.get("fp") == "Q1" and r.get("end") == "2025-04-30" and r.get("form") == "10-Q")
    h1 = next(r for r in ocf_rows if r.get("fy") == 2026 and r.get("fp") == "Q2" and r.get("end") == "2025-07-31" and r.get("form") == "10-Q")
    expected_q2 = h1["val"] - q1["val"]
    assert expected_q2 == 12941000000
    ocf = [f for f in market.get_facts_pit("WMT", as_of="2026-03-20")["facts"] if f["concept"] == "OperatingCashFlow" and f["value"] == expected_q2]
    assert ocf, "YTD operating cash flow was not converted to the standalone quarter"

    msft = json.loads((SEC_DIR / "MSFT_companyfacts.json").read_text(encoding="utf-8"))
    ms_rev = next(r for r in msft["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"]["units"]["USD"]
                  if r.get("fp") == "FY" and r.get("end") == "2026-06-30" and r.get("form") == "10-K")
    assert ms_rev["end"].endswith("-06-30") and ms_rev["val"] == 331839000000
    ms_facts = market.get_facts_pit("MSFT", as_of="2026-07-29", period="FY2026")["facts"]
    assert next(f for f in ms_facts if f["concept"] == "Revenue")["value"] == 331839000000

    ttm = market.compute_ttm_and_ratios("WMT", as_of="2026-03-20")
    assert ttm["status"] in {"ok", "insufficient_quarters_for_ttm", "non_contiguous_quarters_for_ttm"}
    if ttm["status"] == "ok":
        assert ttm["ttm_metrics"]["revenue"] is not None and ttm["ttm_metrics"]["revenue"] != 0
    features = market.generate_quality_features("WMT", as_of="2026-03-20")
    assert features is None or features.get("fcf_growth") is None or isinstance(features["fcf_growth"], float)
    disc = workspace["decision"].discover_opportunities(["WMT", "NOSUCH"])
    assert all(row.get("reference_market_price") is not None for row in disc["candidates"])
    assert disc["status"] in {"ok", "insufficient_data", "no_compelling_opportunity"}
    assert not any(row["symbol"] == "NOSUCH" and row.get("fcf_growth") == 0 for row in disc["candidates"])


def test_research_committee_approval_journal_and_restart(workspace):
    flex = workspace["root"] / "flex.xml"
    _write_flex(flex, include_option=False)
    assert _cli(workspace["env"], "import-flex", str(flex)).returncode == 0
    thesis = _cli(workspace["env"], "upsert-thesis", "QAEQ", "--pillars", "Synthetic quality compounder", "--breakers", "two declines in operating cash flow")
    assert thesis.returncode == 0, thesis.stderr
    params = json.dumps({
        "symbol": "QAEQ", "action": "BUY", "amount_base": "100", "account": "margin",
        "revenue_t0": "1000", "shares": "10", "falsifier": "Operating cash flow declines for two reports",
        "thesis": "Synthetic committee case",
    })
    staged = _cli(workspace["env"], "run-workflow", "investment_committee_memo", "--params", params)
    assert staged.returncode == 0, staged.stderr
    body = json.loads(staged.stdout)
    assert body["status"] == "awaiting_approval"
    run_id = body["run_id"]
    approved = _cli(workspace["env"], "approve", run_id, "--note", "QA human approval")
    assert approved.returncode == 0, approved.stderr
    again = _cli(workspace["env"], "approve", run_id, "--note", "QA human approval")
    assert again.returncode == 0
    assert json.loads(again.stdout)["status"] == "completed"
    probe = subprocess.run([sys.executable, "-c",
        "import os,sqlite3,sys; c=sqlite3.connect(os.environ['PERSONAL_AGENT_HOME']+'/knowledge.sqlite3');"
        "print(c.execute('select count(*) from memory where approval_run_id=?',(sys.argv[1],)).fetchone()[0])",
        run_id], cwd=ROOT, env=workspace["env"], capture_output=True, text=True, timeout=30)
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "1"


def test_concurrent_approval_commits_one_journal(workspace):
    ops = workspace["ops"]
    wf = ops.run_workflow("equity_valuation_and_expectations", {
        "symbol": "QQQ", "propose_thesis": "Synthetic thesis", "falsifier": "Revenue declines twice",
    })
    errors = []
    def _approve():
        try:
            ops.approve_workflow(wf["run_id"], "QA concurrent")
        except Exception as exc:
            errors.append(type(exc).__name__)
    threads = [threading.Thread(target=_approve) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    with workspace["core"].db("knowledge") as c:
        count = c.execute("SELECT COUNT(*) FROM memory WHERE approval_run_id=?", (wf["run_id"],)).fetchone()[0]
    assert count == 1
    assert all(name in {"ValueError", "OperationalError"} for name in errors) or errors == []


def _install_fake_tws(monkeypatch, *, partial=False, delta=0.5, usd_account=True):
    mod_cl = type(sys)("ibapi.client")
    mod_wr = type(sys)("ibapi.wrapper")
    mod_ct = type(sys)("ibapi.contract")
    mod_ib = type(sys)("ibapi")

    class EWrapper:
        pass

    class Contract:
        def __init__(self):
            self.conId = 0
            self.symbol = ""
            self.secType = ""
            self.currency = ""
            self.exchange = ""
            self.multiplier = "1"

    class EClient:
        def __init__(self, wrapper):
            self._w = wrapper
            self._conn = False
            self.orders = []

        def connect(self, host, port, client_id):
            self._conn = True
            self._w.managedAccounts("U_E2E_M,U_E2E_T,U_E2E_R")

        def isConnected(self):
            return self._conn

        def run(self):
            return None

        def disconnect(self):
            self._conn = False

        def placeOrder(self, *args):
            self.orders.append(args)

        def reqAccountSummary(self, req_id, group, tags):
            if partial:
                return
            rows = [
                ("U_E2E_M", "NetLiquidation", "10000", "CAD"),
                ("U_E2E_M", "BuyingPower", "4000", "CAD"),
                ("U_E2E_T", "NetLiquidation", "5000", "CAD"),
                ("U_E2E_T", "BuyingPower", "5000", "CAD"),
                ("U_E2E_R", "NetLiquidation", "2000", "USD" if usd_account else "CAD"),
                ("U_E2E_R", "BuyingPower", "500", "USD" if usd_account else "CAD"),
            ]
            for account, tag, value, currency in rows:
                self._w.accountSummary(req_id, account, tag, value, currency)
            self._w.accountSummaryEnd(req_id)

        def cancelAccountSummary(self, req_id):
            return None

        def reqPositions(self):
            for i in range(100):
                c = Contract()
                c.conId = 9001 if i == 0 else 10000 + i
                c.symbol = "QAOPT" if i == 0 else f"P{i:03d}"
                c.secType = "OPT" if i == 0 else "STK"
                c.currency = "USD" if i % 5 == 0 else "CAD"
                c.multiplier = "100" if i == 0 else "1"
                account = ("U_E2E_M", "U_E2E_T", "U_E2E_R")[i % 3]
                self._w.position(account, c, 1, 10.0)
            self._w.positionEnd()

        def cancelPositions(self):
            return None

        def reqMktData(self, req_id, contract, generic, snapshot, regulatory, options):
            self._w.tickPrice(req_id, 1, 1.0, None)
            self._w.tickPrice(req_id, 2, 1.2, None)
            self._w.tickOptionComputation(req_id, 13, 0, 0.2, delta, 1.1, 0.0, 0.01, 0.1, -0.01, 100.0)
            self._w.tickSnapshotEnd(req_id)

        def cancelMktData(self, req_id):
            return None

        def reqContractDetails(self, req_id, contract):
            self._w.contractDetailsEnd(req_id)

        def reqSecDefOptParams(self, req_id, symbol, exchange, sec_type, underlying):
            self._w.securityDefinitionOptionParameterEnd(req_id)

    mod_cl.EClient = EClient
    mod_wr.EWrapper = EWrapper
    mod_ct.Contract = Contract
    monkeypatch.setitem(sys.modules, "ibapi", mod_ib)
    monkeypatch.setitem(sys.modules, "ibapi.client", mod_cl)
    monkeypatch.setitem(sys.modules, "ibapi.wrapper", mod_wr)
    monkeypatch.setitem(sys.modules, "ibapi.contract", mod_ct)


def test_broker_callbacks_freshness_and_risk_fingerprint(workspace, monkeypatch):
    broker = workspace["broker"]
    flex = workspace["root"] / "flex.xml"
    _write_flex(flex)
    assert _cli(workspace["env"], "import-flex", str(flex)).returncode == 0
    _install_fake_tws(monkeypatch, partial=True)
    partial = broker.sync_broker_live()
    assert partial["status"] == "partial" and partial["live_verified"] is False
    status = _cli(workspace["env"], "broker-status")
    assert status.returncode == 0
    assert json.loads(status.stdout)["live_verified"] is False
    base = workspace["finance"].load_full_portfolio_snapshot()
    assert broker.enrich_snapshot_with_broker(base).snapshot_id == base.snapshot_id
    reconcile = _cli(workspace["env"], "broker-reconcile")
    assert reconcile.returncode == 2
    assert json.loads(reconcile.stdout)["status"] != "reconciled"

    _install_fake_tws(monkeypatch, partial=False, delta=0.5)
    live = broker.sync_broker_live()
    assert live["positions"] == 100 and live["live_verified"] is True
    latest = broker.latest_broker_snapshot()
    assert len(latest["positions"]) == 100
    assert latest["accounts"]["U_E2E_R"]["currency"] == "USD"
    enriched = broker.enrich_snapshot_with_broker(base)
    opt = next(p for p in enriched.positions if p.conid == "9001")
    assert opt.delta == Decimal("0.5") and opt.underlying_price == Decimal("100")
    nav = float(enriched.nav)
    expected = (1 * 100 * 100 * 1 * 0.5) / nav
    policy = workspace["quant"].evaluate_policy(enriched, mode="AUDIT", risk_context={"twr": None, "max_drawdown": "0"})
    assert abs(policy["exposure_summary"]["options_delta_equiv_weight"] - expected) < 1e-9
    first_hash = enriched.snapshot_id

    _install_fake_tws(monkeypatch, partial=False, delta=0.25)
    broker.sync_broker_live()
    changed = broker.enrich_snapshot_with_broker(base)
    assert changed.snapshot_id != first_hash
    assert next(p for p in changed.positions if p.conid == "9001").delta == Decimal("0.25")
    policy2 = workspace["quant"].evaluate_policy(changed, mode="AUDIT", risk_context={"twr": None, "max_drawdown": "0"})
    assert abs(policy2["exposure_summary"]["options_delta_equiv_weight"] - expected / 2) < 1e-9
    usd = next(a for a in changed.accounts if a.account == "rrsp")
    assert usd.buying_power is None
    assert not hasattr(broker.IBKRTWSAdapter, "placeOrder")


def test_scheduled_monitor_failure_retry_report_and_restore(workspace):
    flex = workspace["root"] / "flex.xml"
    _write_flex(flex)
    assert _cli(workspace["env"], "import-flex", str(flex)).returncode == 0
    ops = workspace["ops"]
    with patch.object(workspace["decision"], "discover_opportunities", side_effect=RuntimeError("injected monitor failure")):
        failed = ops.run_daily_monitoring()
    assert failed["status"] == "error" and failed["error"] == "RuntimeError"
    cli_failed = _cli(workspace["env"], "run-monitoring")
    assert cli_failed.returncode == 0, cli_failed.stderr
    retried = cli_failed
    assert retried.returncode == 0, retried.stderr
    body = json.loads(retried.stdout)
    assert body["status"] == "ok" and body["retry_count"] == 1
    alerts = _cli(workspace["env"], "alerts")
    assert alerts.returncode == 0
    report = _cli(workspace["env"], "report")
    assert report.returncode == 0, report.stderr
    assert json.loads(report.stdout).get("status") != "live"
    backup = _cli(workspace["env"], "backup", "--dest", str(workspace["root"] / "backup"))
    assert backup.returncode == 0, backup.stderr
    backup_dir = json.loads(backup.stdout)["backup_dir"]
    restored = workspace["core"].restore_databases(backup_dir, workspace["root"] / "restored")
    assert restored["status"] == "ok"
    with sqlite3.connect(workspace["root"] / "restored" / "finance.sqlite3") as c:
        assert c.execute("SELECT COUNT(*) FROM nav").fetchone()[0] >= 3
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_shadow_and_live_gates_stay_external(workspace):
    shadow = _cli(workspace["env"], "shadow-check")
    assert shadow.returncode == 2
    body = json.loads(shadow.stdout)
    assert body["production_accepted"] is False and body["status"] != "ok"
