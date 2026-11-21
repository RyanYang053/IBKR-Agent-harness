# Reproduction evidence — Independent Real Scenario QA

- Candidate code SHA: `c72caab1bb469e7bc05c8f077ca154832bfdb61b`
- Evidence commit: `9e0a7975388352a890788a973344bf3fbbf0cdba`
- Gate result: `OFFLINE_VERIFIED` (52/52 mandatory IDs)
- Independent result: `SCENARIO_VERIFIED` (32/32 scenarios; 9/9 workflows)
- live_verified: false
- production_accepted: false

## Commands

```powershell
python -m pip install -e ".[dev]"
python -m pip check
python scripts/run_independent_qa.py --root .
python -m pytest tests/independent -q --junitxml=validation/evidence/independent/scenarios-full.xml
python validation/release_gate.py --root .
python validation/release_gate.py --root . --verify-report
```

## Independently checked numerical oracles

| ID | Expectation | Method |
|---|---|---|
| R04 | TWR 0 | contribution equals NAV rise; external flow excluded |
| R14 | 0.7 | 1*0.5*100*100*1.4/10000 |
| R25 | 1250 | Gordon 100/(0.10-0.02) |
| WMT FY2026 Revenue | 713163000000 | accession 0000104169-26-000055 end 2026-01-31 |
| WMT Q2 OCF standalone | 12941000000 | H1 YTD minus Q1 |
| MSFT FY2026 Revenue | 331839000000 | end 2026-06-30 |

## Data provenance

- MSFT CompanyFacts SHA-256 `8c1903c041f5c27e86ed15f37debb1f41882b7904bba129ef9edc76478b1e8ea`
- WMT CompanyFacts SHA-256 `49330f98609f70fcef4054edc90bc811398a58c4bf26dc0b218d607967a8dbe4`
- Broker transport: fake ibapi EClient callbacks only

## Unresolved blockers

- `G2_LIVE_IBKR` (BLOCKED_EXTERNAL): Real IBKR Gateway/TWS read-only connectivity must be run on the operator machine. See `runbooks/Operator Readonly Live QA.md`.
- `G3_SHADOW_20` (BLOCKED_EXTERNAL): 20 trading-session Shadow Mode has no operator session evidence.

## Workflow coverage

- `test_flex_import_snapshot_risk_trade_and_policy`: PASS
- `test_recorded_sec_filings_pit_ttm_valuation_and_discovery`: PASS
- `test_research_committee_approval_journal_and_restart`: PASS
- `test_concurrent_approval_commits_one_journal`: PASS
- `test_broker_callbacks_freshness_and_risk_fingerprint`: PASS
- `test_scheduled_monitor_failure_retry_report_and_restore`: PASS
- `test_shadow_and_live_gates_stay_external`: PASS
- `test_broker_disconnect_retry_entitlement_and_stale_last_good`: PASS
- `test_sec_filings_to_committee_decision_chain`: PASS
