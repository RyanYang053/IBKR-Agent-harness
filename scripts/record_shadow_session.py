#!/usr/bin/env python3
"""Record one operator Shadow Mode session into local/ (gitignored).

G3 requires 20 distinct trading-session records. This script never sets
production_accepted=true. Private NAV values stay only under local/.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SESSIONS = ROOT / "local" / "shadow_sessions"
LEDGER = SESSIONS / "ledger.json"


def cli(*args: str):
    p = subprocess.run(
        [sys.executable, "-m", "personal_agent.cli", *args],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
    )
    try:
        body = json.loads(p.stdout) if p.stdout.strip() else {}
    except json.JSONDecodeError:
        body = {"parse_error": True, "stdout": (p.stdout or "")[:1000], "stderr": (p.stderr or "")[:1000]}
    if isinstance(body, dict):
        body["_exit_code"] = p.returncode
        return body
    return {"_exit_code": p.returncode, "payload": body}


def redact_public(summary: dict) -> dict:
    return {
        "session_date": summary.get("session_date"),
        "recorded_at": summary.get("recorded_at"),
        "flex_as_of": summary.get("flex_as_of"),
        "broker_capture_status": summary.get("broker_capture_status"),
        "broker_live_verified": summary.get("broker_live_verified"),
        "broker_age_seconds": summary.get("broker_age_seconds"),
        "shadow_status": summary.get("shadow_status"),
        "readiness_blockers": summary.get("readiness_blockers"),
        "backup_status": summary.get("backup_status"),
        "monitoring_status": summary.get("monitoring_status"),
        "alerts_count": summary.get("alerts_count"),
        "production_accepted": False,
    }


def main() -> int:
    SESSIONS.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    stamp = datetime.now(timezone.utc).isoformat()
    portfolio = cli("portfolio")
    broker = cli("broker-status")
    reconcile = cli("broker-reconcile")
    shadow = cli("shadow-check")
    policy = cli("policy")
    alerts = cli("alerts")
    monitoring = cli("run-monitoring")
    backup = cli("backup")

    session = {
        "schema": "shadow_session.v1",
        "session_date": today,
        "recorded_at": stamp,
        "flex_as_of": portfolio.get("as_of"),
        "flex_status": portfolio.get("status"),
        "broker_capture_status": broker.get("capture_status") or broker.get("status"),
        "broker_live_verified": bool(broker.get("live_verified")),
        "broker_as_of": broker.get("as_of"),
        "broker_age_seconds": broker.get("age_seconds"),
        "reconcile_status": reconcile.get("status"),
        "shadow_status": shadow.get("status"),
        "readiness_blockers": shadow.get("readiness_blockers") or [],
        "policy_status": policy.get("status") if isinstance(policy, dict) else None,
        "alerts_count": (
            len(alerts.get("alerts") or alerts.get("rows") or alerts.get("payload") or [])
            if isinstance(alerts, dict) else None
        ),
        "monitoring_status": monitoring.get("status"),
        "backup_status": backup.get("status"),
        "backup_dir": backup.get("backup_dir"),
        "snapshot_id": portfolio.get("snapshot_id") or shadow.get("flex_snapshot_id"),
        "production_accepted": False,
        "notes": "Operator shadow session; not investment performance certification.",
    }
    path = SESSIONS / f"session_{today}.json"
    path.write_text(json.dumps(session, indent=2) + "\n", encoding="utf-8")

    ledger = {"schema": "shadow_ledger.v1", "sessions": [], "required_sessions": 20, "production_accepted": False}
    if LEDGER.exists():
        ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    # One record per calendar session date; replace same-day reruns.
    ledger["sessions"] = [s for s in ledger.get("sessions", []) if s.get("session_date") != today]
    ledger["sessions"].append(redact_public(session))
    ledger["sessions"].sort(key=lambda s: s.get("session_date") or "")
    ledger["completed_sessions"] = len(ledger["sessions"])
    ledger["remaining_sessions"] = max(0, 20 - ledger["completed_sessions"])
    ledger["updated_at"] = stamp
    ledger["production_accepted"] = False
    LEDGER.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")

    public = ROOT / "validation" / "evidence" / "g3_shadow_status.json"
    public.parent.mkdir(parents=True, exist_ok=True)
    public.write_text(json.dumps({
        "schema": "g3_shadow_status.v1",
        "result": "IN_PROGRESS" if ledger["completed_sessions"] < 20 else "READY_FOR_OPERATOR_SIGN_OFF",
        "completed_sessions": ledger["completed_sessions"],
        "required_sessions": 20,
        "remaining_sessions": ledger["remaining_sessions"],
        "production_accepted": False,
        "live_verified": False,
        "updated_at": stamp,
        "local_ledger": str(LEDGER.relative_to(ROOT)),
        "note": "G3 cannot be completed in one coding session; operator must record 20 trading days.",
    }, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "recorded": str(path.relative_to(ROOT)),
        "completed_sessions": ledger["completed_sessions"],
        "remaining_sessions": ledger["remaining_sessions"],
        "production_accepted": False,
        "shadow_status": session["shadow_status"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
