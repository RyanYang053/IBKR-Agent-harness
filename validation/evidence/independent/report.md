# Independent scenario QA — SCENARIO_VERIFIED

Git SHA: `740186956880ac74f03ccdcd912f0997cc586d99`
Independent scenarios: 32/32 passed; 0 failed; 0 errors; 0 skipped.
Offline workflows: 9/9 passed; 0 failed.

| ID | Status | Summary |
|---|---|---|
| test_R01_full_120_position_snapshot_not_ui_truncated | PASS |  |
| test_R02_account_statement_date_mismatch_blocks_advice | PASS |  |
| test_R03_unknown_foreign_cash_flow_requires_explicit_fx | PASS |  |
| test_R04_cash_contribution_is_not_investment_profit | PASS |  |
| test_R05_unmatched_transfer_must_block_combined_returns | PASS |  |
| test_R06_buy_preserves_account_cash_nav_and_lot_identity | PASS |  |
| test_R07_unknown_margin_buying_power_blocks_new_borrowing | PASS |  |
| test_R08_oversell_is_rejected_by_quantity_not_just_market_value | PASS |  |
| test_R09_quantity_amount_without_price_is_not_executable_trade | PASS |  |
| test_R10_same_symbol_different_contract_without_conid_must_not_guess | PASS |  |
| test_R11_value_with_zero_quantity_is_not_a_valid_new_stock_position | PASS |  |
| test_R12_fx_and_contract_multiplier_are_applied_to_actual_trade | PASS |  |
| test_R13_snapshot_id_changes_when_risk_relevant_lot_metadata_changes | PASS |  |
| test_R14_option_delta_exposure_matches_signed_notional_oracle | PASS |  |
| test_R15_partial_or_replay_capture_cannot_enrich_investment_state | PASS |  |
| test_R16_stale_live_broker_snapshot_must_not_enrich | PASS |  |
| test_R17_broker_buying_power_usd_must_be_converted_or_marked_unavailable | PASS |  |
| test_R18_old_live_capture_must_not_reconcile_as_current_state | PASS |  |
| test_R19_as_of_restatement_never_uses_future_publication | PASS |  |
| test_R20_incomplete_quarters_cannot_be_called_ttm | PASS |  |
| test_R21_missing_fcf_growth_and_dilution_are_not_zero_return_forecasts | PASS |  |
| test_R22_earnings_actuals_must_not_be_backdated_as_pre_earnings_forecast | PASS |  |
| test_R23_beat_without_guidance_raise_must_not_claim_beat_and_raise | PASS |  |
| test_R24_unprofitable_bear_case_must_not_be_forced_positive_margin | PASS |  |
| test_R25_dcf_oracle_perpetuity_100_fcf_10pct_wacc_2pct_g | PASS |  |
| test_R26_bsm_closed_form_atm_one_year_european_call | PASS |  |
| test_R27_missing_open_price_remains_missing_to_backtester | PASS |  |
| test_R28_prediction_brier_is_computed_from_actual_outcomes | PASS |  |
| test_R29_public_mode_cannot_open_local_private_sqlite | PASS |  |
| test_R30_real_sqlite_backup_restore_preserves_rows | PASS |  |
| test_R31_real_approval_journal_has_exactly_one_human_commit | PASS |  |
| test_R32_offline_replay_cannot_certify_live_shadow_mode | PASS |  |

| Workflow | Status |
|---|---|
| test_flex_import_snapshot_risk_trade_and_policy | PASS |
| test_recorded_sec_filings_pit_ttm_valuation_and_discovery | PASS |
| test_research_committee_approval_journal_and_restart | PASS |
| test_concurrent_approval_commits_one_journal | PASS |
| test_broker_callbacks_freshness_and_risk_fingerprint | PASS |
| test_scheduled_monitor_failure_retry_report_and_restore | PASS |
| test_shadow_and_live_gates_stay_external | PASS |
| test_broker_disconnect_retry_entitlement_and_stale_last_good | PASS |
| test_sec_filings_to_committee_decision_chain | PASS |

## Unresolved blockers

- `G2_LIVE_IBKR` (BLOCKED_EXTERNAL): Real IBKR Gateway/TWS read-only connectivity must be run on the operator machine.
- `G3_SHADOW_20` (BLOCKED_EXTERNAL): 20 trading-session Shadow Mode has no operator session evidence.

## Commands

- `baseline`: PASS, log `validation\evidence\independent\baseline.log`
- `original_contract`: PASS, log `validation\evidence\independent\original_contract.log`
- `independent_scenarios`: PASS, log `validation\evidence\independent\independent_scenarios.log`
- `offline_workflows`: PASS, log `validation\evidence\independent\offline_workflows.log`

No live or production acceptance is claimed.
