#!/usr/bin/env python3
"""Attempt G2 read-only IBKR live QA and write local evidence.

Never uploads private balances. If TWS/Gateway is unreachable, records BLOCKED_EXTERNAL.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "local" / "g2_live_evidence"
PORTS = (7496, 7497, 4001, 4002)


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            return True
    except OSError:
        return False


def cli(*args: str) -> dict:
    p = subprocess.run(
        [sys.executable, "-m", "personal_agent.cli", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    body = {}
    try:
        body = json.loads(p.stdout) if p.stdout.strip() else {}
    except json.JSONDecodeError:
        body = {"raw_stdout": (p.stdout or "")[:2000], "raw_stderr": (p.stderr or "")[:2000]}
    body["_exit_code"] = p.returncode
    return body


def redact(obj):
    """Drop likely private numeric account fields from shared evidence copies."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(x in lk for x in ("nav", "cash", "buying", "balance", "liquidation", "accountid", "account_id")):
                out[k] = "<redacted>"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    open_ports = [p for p in PORTS if port_open(p)]
    report = {
        "schema": "g2_live_qa.v1",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "open_api_ports": open_ports,
        "live_verified": False,
        "production_accepted": False,
        "steps": {},
    }
    if not open_ports:
        report["result"] = "BLOCKED_EXTERNAL"
        report["reason"] = "No local TWS/IB Gateway API port is accepting connections on 7496/7497/4001/4002."
        report["next_action"] = "Start TWS or IB Gateway with read-only API enabled, then re-run this script."
        path = OUT / f"g2_{stamp}_blocked.json"
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        public = ROOT / "validation" / "evidence" / "g2_status.json"
        public.parent.mkdir(parents=True, exist_ok=True)
        public.write_text(json.dumps({
            "schema": "g2_status.v1",
            "result": "BLOCKED_EXTERNAL",
            "live_verified": False,
            "production_accepted": False,
            "reason": report["reason"],
            "recorded_at": report["recorded_at"],
            "local_evidence": str(path.relative_to(ROOT)),
        }, indent=2) + "\n", encoding="utf-8")
        print(f"BLOCKED_EXTERNAL: {report['reason']}")
        print(f"local_evidence={path}")
        return 2

    for name, args in (
        ("check_config", ("check-config",)),
        ("broker_live_sync", ("broker-live-sync",)),
        ("broker_status", ("broker-status",)),
        ("broker_reconcile", ("broker-reconcile",)),
        ("portfolio", ("portfolio",)),
        ("policy", ("policy",)),
        ("shadow_check", ("shadow-check",)),
        ("report", ("report",)),
    ):
        report["steps"][name] = redact(cli(*args))

    sync = report["steps"].get("broker_live_sync") or {}
    status = report["steps"].get("broker_status") or {}
    live_ok = bool(sync.get("live_verified")) and bool(status.get("live_verified"))
    report["live_verified"] = live_ok
    report["result"] = "LIVE_VERIFIED" if live_ok else "BLOCKED_EXTERNAL"
    if not live_ok:
        report["reason"] = "TWS port open but broker sync did not set live_verified=true (partial/unavailable/entitlement)."
    path = OUT / f"g2_{stamp}_{report['result'].lower()}.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    public = ROOT / "validation" / "evidence" / "g2_status.json"
    public.write_text(json.dumps({
        "schema": "g2_status.v1",
        "result": report["result"],
        "live_verified": report["live_verified"],
        "production_accepted": False,
        "reason": report.get("reason", "operator completed read-only sync"),
        "recorded_at": report["recorded_at"],
        "local_evidence": str(path.relative_to(ROOT)),
        "open_api_ports": open_ports,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"{report['result']}: local_evidence={path}")
    return 0 if live_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
