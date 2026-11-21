# Operator-only real IBKR read-only QA

This section must run **on the user's own Windows computer**, not on a hosted coding agent. No private IBKR source data, credentials, full account identifiers, balances, or unredacted logs should be uploaded to cloud tools.

## 0. Preflight

- Complete the 32 independent offline scenarios and existing release gate first.
- TWS / IB Gateway must be running locally, authenticated, API access explicitly configured for **read-only** requests. Ensure trade/order methods remain unavailable in the client and MCP/CLI.
- Choose the correct local paper/live API port from your TWS configuration; never assume the default port.
- Ensure `config/personal.json` maps **each configured IBKR account** to its unique local alias and confirms reporting base currency.
- Use actual market-data entitlement status; delayed/frozen data must be labeled and never represented as current executable prices.
- Take a privacy-safe local backup before any import or schema migration. Do not copy full `local/` directory into git.

## 1. Operator commands (PowerShell, within project repo)

```powershell
python -m personal_agent.cli check-config
python -m personal_agent.cli portfolio
python -m personal_agent.cli broker-live-sync
python -m personal_agent.cli broker-status
python -m personal_agent.cli broker-reconcile
python -m personal_agent.cli policy
python -m personal_agent.cli shadow-check
python -m personal_agent.cli report
```

These tools should not place orders; verify by source inspection and TWS API read-only settings. `broker-live-sync` stores private read-only snapshots locally. Stop if it reports errors or if permissions/entitlements are unclear.

## 2. Required independent observations

| Check | Actual external truth | Pass criterion |
|---|---|---|
| Accounts | TWS account selector and account summary | Exactly expected accounts; no unknown alias or duplicated account |
| Net liquidation | TWS account-level NAV | Same source timestamp and currency; documented reconciliation tolerance |
| Buying power | TWS AvailableFunds/BuyingPower/ExcessLiquidity | All values have explicit currency, conversion and applicable snapshot age |
| Holdings | TWS Positions by account+conId | Exact quantity, asset category, multiplier and currency match |
| Greeks | TWS option quote market-data subscriptions | Verify conId, quote time, underlying spot, delta range and entitlement; missing is unavailable |
| FX | TWS currency field and date-matched FX feed | No CAD/USD implicit 1.0 for foreign values |
| Historical ledger | Last completed daily Flex report | Historical close unchanged by live capture; expected intraday differences disclosed |
| Safety | TWS connection logs | No placeOrder/modifyOrder/cancelOrder/exercise options request |
| Failure | Terminate TWS / deny entitlement | Status changes to unavailable/partial; stale snapshot cannot pass decision readiness |

For provider issues, output `BLOCKED_EXTERNAL` with precise reason and time. Never report `LIVE_VERIFIED` unless actual TWS responses were observed. Collect local redacted evidence only, including session timestamp, expected/observed numeric deltas and contract matching counts.

## 3. Real public filings, no private secrets

Use SEC EDGAR public CompanyFacts JSON from two issuers with different fiscal year ends. Archive SHA256 and SEC filing accessions. Inspect `Revenue`, `OperatingCashFlow`, `CapEx`, diluted shares, current+noncurrent debt, equity and cash against the original 10-K/10-Q. Validate as-of snapshots before and after amendments, and include one issuer with cumulative YTD cash flow. A coding agent may perform this step using public data.

## 4. Shadow sessions

Measure at least 20 real trading sessions after offline and live validation. Each session: last successful Flex date, broker capture age, entitlement status, per-account NAV deltas, risk snapshot hash, stale and unknown fields, automated alerts, approval-lease state, backup success, and scheduled job exit codes. Keep `production_accepted=false` until this record is complete and manually approved.
