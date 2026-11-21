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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--acceptance-junit", default="validation/evidence/acceptance.xml")
    ap.add_argument("--scenario-junit", default="validation/evidence/independent/scenarios.xml")
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
            rows.append({
                "kind": kind,
                "id": name.split("_", 1)[0].replace("test", "") if name.startswith("test_") else name,
                "test": name,
                "classname": node.get("classname", ""),
                "status": "FAIL" if failed else ("SKIP" if skipped else "PASS"),
                "seconds": node.get("time", ""),
            })

    consume(root / args.acceptance_junit, "acceptance")
    consume(root / args.scenario_junit, "independent")
    mandatory = [c["id"] for c in manifest["cases"] if c.get("mandatory") and c.get("gate") == "G1"]
    seen = {row["id"] for row in rows}
    missing = [i for i in mandatory if i not in seen and not any(i in row["test"] for row in rows)]
    # Acceptance test names are test_F01_... so the id parsed above is F01 when split on first underscore after test.
    parsed_ids = set()
    for row in rows:
        test = row["test"]
        if test.startswith("test_") and len(test) >= 8 and test[5:8].isalnum():
            parsed_ids.add(test[5:8])
    missing = [i for i in mandatory if i not in parsed_ids]
    report = {
        "schema": "evidence_matrix.v1",
        "git_sha": head,
        "mandatory_acceptance_ids": len(mandatory),
        "missing_mandatory_ids": missing,
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
    print(f"matrix rows={len(rows)} missing_mandatory={missing} path={out}")
    return 0 if not missing and all(r["status"] == "PASS" for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
