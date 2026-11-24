from __future__ import annotations
import argparse
import html
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
STYLE = "body{background:#101827;color:#e6edf7;font:16px/1.55 system-ui,sans-serif;margin:0}main{max-width:1000px;margin:auto;padding:44px 24px}h1{font-size:clamp(28px,4vw,44px);margin:0}h2{margin-top:32px}p{color:#aebcd0}section,pre{background:#19263a;border:1px solid #33455f;border-radius:12px;padding:18px;overflow:auto}table{width:100%;border-collapse:collapse;text-align:left}td,th{padding:8px 12px;border-bottom:1px solid #33455f}th{color:#9dbce5}pre{white-space:pre-wrap;overflow-wrap:anywhere}small{color:#a7e3ce;font-weight:700}"
def esc(v):
    return html.escape(str(v), quote=True)
def table(headers, rows):
    return "<section><table><thead><tr>" + "".join("<th>"+esc(x)+"</th>" for x in headers) + "</tr></thead><tbody>" + "".join("<tr>"+"".join("<td>"+esc(x)+"</td>" for x in row)+"</tr>" for row in rows) + "</tbody></table></section>"
def write_report(path, title, summary, body):
    document = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src &apos;none&apos;; style-src &apos;unsafe-inline&apos;; base-uri &apos;none&apos;; form-action &apos;none&apos;"><title>'+esc(title)+'</title><style>'+STYLE+'</style></head><body><main><small>SYNTHETIC RESEARCH DEMO</small><h1>'+esc(title)+'</h1><p>'+esc(summary)+'</p>'+body+'<p>Fixture data only. No investment recommendation or live performance claim.</p></main></body></html>'
    with path.open("x", encoding="utf-8") as handle:
        handle.write(document)
    print(f"Report: {path.resolve()}")
def output_path():
    parser = argparse.ArgumentParser(description="Generate a local fixture-based HTML preview.")
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args().output

import json
import os
import sys
import tempfile
from decimal import Decimal

def main():
    out = output_path()
    # All state is isolated in a temporary directory. No model, broker or market provider is called.
    with tempfile.TemporaryDirectory(prefix="ibkr-harness-demo-") as tmp:
        home = Path(tmp)
        cfg = home / "config.json"
        cfg.write_text(json.dumps({
            "knowledge_roots": [],
            "finance": {"accounts": {"U0000001": "margin", "U0000002": "tfsa"},
                        "base_currency": "CAD", "flex_queries": [], "max_snapshot_age_days": 4000},
            "policy": {"max_file_bytes": 10000000, "max_tool_rows": 50}
        }), encoding="utf-8")
        os.environ["PERSONAL_AGENT_HOME"] = str(home)
        os.environ["PERSONAL_AGENT_CONFIG"] = str(cfg)
        sys.path.insert(0, str(ROOT))
        from personal_agent import finance, decision
        fixture = ROOT / "tests" / "fixtures" / "two_accounts.xml"
        finance.import_xml(fixture)
        repeated = finance.import_xml(fixture)
        snapshot = finance.portfolio()
        perf = finance.performance()
        dcf = decision.dcf("100", "0.10", "0.02", "0", "10")
        multi = decision.multi_stage_dcf("100", "0.10", "0.20", "0.025", "50", "10", years=5, fade=True)
        reverse = decision.reverse_dcf("100", "0.10", "0.025", "50", "10", reference_price=multi["per_share"], years=5, fade=True)
        implied = Decimal(reverse["implied_stage1_fcff_growth"])
        checks = [
            ("Synthetic two-account NAV", snapshot["nav"], snapshot["positions_complete"] and Decimal(snapshot["nav"]) == Decimal("2000.00")),
            ("Repeated import", repeated["status"], repeated["status"] == "already_imported"),
            ("Transfer-neutral contribution", perf["contributions"], Decimal(perf["contributions"]) == 0),
            ("Synthetic time-weighted return", perf["twr"], Decimal(perf["twr"]) == 0),
            ("Illustrative DCF per share", dcf["per_share"], Decimal(dcf["per_share"]) == Decimal("125")),
            ("Reverse DCF round trip", str(implied), abs(implied-Decimal("0.20")) < Decimal("0.0005"))
        ]
        passed = sum(bool(ok) for _, _, ok in checks)
        body = "<h2>Deterministic checks</h2><p>"+str(passed)+" / "+str(len(checks))+" passed.</p>"
        body += table(["Check","Observed fixture value","Result"], [(label,value,"PASS" if ok else "FAIL") for label,value,ok in checks])
        body += "<p>DCF inputs are illustrative units: FCFF 100, WACC 10%, terminal growth 2%, net debt 0, shares 10. This is not an estimate for a real security.</p>"
        write_report(out, "IBKR Harness fixture report", "Actual synthetic ledger and valuation results in temporary local state. No brokerage or provider session.", body)
        return 0 if passed == len(checks) else 2

if __name__ == "__main__":
    raise SystemExit(main())
