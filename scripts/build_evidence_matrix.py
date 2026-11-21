#!/usr/bin/env python3
"""Build an assertion-level matrix from the acceptance manifest and JUnit XML.

This script does not rerun tests and does not edit validation/report.md.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

R_ORACLES = {
    "R01": "120 holdings remain after max_tool_rows=1; advice uses full snapshot",
    "R02": "date_mismatch blocks advice_allowed",
    "R03": "missing FX on foreign cash flow fails closed",
    "R04": "NAV rise equal to contribution => TWR 0",
    "R05": "unmatched transfer blocks combined returns",
    "R06": "BUY updates cash/NAV/qty/cost_basis exactly",
    "R07": "unknown margin buying_power blocks borrow",
    "R08": "oversell rejected by quantity",
    "R09": "qty without price not executable",
    "R10": "same symbol two conids without conid => ambiguous",
    "R11": "amount-only new STK position rejected",
    "R12": "FX*qty*price applied to CAD cash",
    "R13": "snapshot_id changes with cost_basis/underlying_conid",
    "R14": "option delta weight = qty*delta*mult*spot*fx/nav = 0.7",
    "R15": "partial/replay cannot enrich",
    "R16": "2h-old live capture cannot enrich under 300s max age",
    "R17": "USD BuyingPower converted via fx_rate_to_base or left unavailable",
    "R18": "ancient broker capture cannot reconcile as current",
    "R19": "PIT as-of excludes future publication",
    "R20": "non-contiguous quarters cannot be TTM",
    "R21": "missing fcf_growth/dilution excluded, not zeroed",
    "R22": "actuals cannot backdate pre-earnings forecast",
    "R23": "beat without guidance raise != beat_and_raise",
    "R24": "bear op margin can be negative",
    "R25": "Gordon: 100/(0.10-0.02)=1250",
    "R26": "BSM ATM 1y closed form",
    "R27": "missing open stays missing for backtester",
    "R28": "Brier from actual outcomes",
    "R29": "public mode denies private sqlite",
    "R30": "backup/restore preserves rows",
    "R31": "exactly one human approval journal",
    "R32": "offline replay never live_verified / production_accepted",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--acceptance-junit", default="validation/evidence/acceptance.xml")
    ap.add_argument("--scenario-junit", default="validation/evidence/independent/scenarios-full.xml")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    manifest = json.loads((root / "validation" / "acceptance_manifest.json").read_text(encoding="utf-8"))
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    rows = []

    def consume(path: Path, kind: str):
        if not path.is_file():
            return
        for node in ET.parse(path).getroot().iter("testcase"):
            name = node.get("name", "")
            failed = node.find("failure") is not None or node.find("error") is not None
            skipped = node.find("skipped") is not None
            case_id = ""
            if name.startswith("test_") and len(name) >= 8 and name[5].isalpha() and name[6:8].isdigit():
                case_id = name[5:8]
            rows.append({
                "kind": kind,
                "id": case_id,
                "test": name,
                "classname": node.get("classname", ""),
                "status": "FAIL" if failed else ("SKIP" if skipped else "PASS"),
                "seconds": node.get("time", ""),
                "independent_oracle": R_ORACLES.get(case_id, manifest_oracle(manifest, case_id)),
                "source": manifest_source(manifest, case_id),
            })

    def manifest_oracle(m, case_id):
        for c in m["cases"]:
            if c["id"] == case_id:
                return c.get("assertion", "")[:240]
        return ""

    def manifest_source(m, case_id):
        for c in m["cases"]:
            if c["id"] == case_id:
                return c.get("source", "")
        return ""

    scenario_path = root / args.scenario_junit
    if not scenario_path.is_file():
        scenario_path = root / "validation/evidence/independent/scenarios.xml"
    consume(root / args.acceptance_junit, "acceptance")
    consume(scenario_path, "independent")
    mandatory = [c["id"] for c in manifest["cases"] if c.get("mandatory") and c.get("gate") == "G1"]
    parsed_ids = {row["id"] for row in rows if row["id"]}
    missing = [i for i in mandatory if i not in parsed_ids]
    r_missing = [f"R{i:02d}" for i in range(1, 33) if f"R{i:02d}" not in parsed_ids]
    report = {
        "schema": "evidence_matrix.v1",
        "git_sha": head,
        "mandatory_acceptance_ids": len(mandatory),
        "missing_mandatory_ids": missing,
        "missing_scenario_ids": r_missing,
        "rows": rows,
        "external_blockers": [
            {"id": "G2", "status": "BLOCKED_EXTERNAL", "reason": "Real IBKR read-only TWS connectivity was not run in this offline session."},
            {"id": "G3", "status": "BLOCKED_EXTERNAL", "reason": "20 trading-session Shadow Mode has no operator session record."},
        ],
        "live_verified": False,
        "production_accepted": False,
    }
    out = root / "validation" / "evidence" / "independent" / "evidence_matrix.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"matrix rows={len(rows)} missing_mandatory={missing} missing_R={r_missing} path={out}")
    return 0 if not missing and not r_missing and all(r["status"] == "PASS" for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
