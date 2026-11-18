# Personal Investment OS — Independent release audit and executable acceptance contract

**Baseline:** `fad9786bb7eda3a1061d44ce7ea547f394ad2c89` (`main`)  
**Repository:** https://github.com/RyanYang053/IBKR-Agent-harness  
**Current observed CI:** https://github.com/RyanYang053/IBKR-Agent-harness/actions/runs/44375631863 — success.  
**Release decision:** NOT ACCEPTED as production. G1 independent offline qualification remains to be demonstrated.

## 1. Scope and evidence

This audit uses the pinned Git tree, actual Python implementation, runtime configuration, PowerShell entry points and verification source, rather than commit descriptions or feature claims. Retrieved all 13 nonempty core Python files (broker, finance, decision, quant, market, ops, knowledge, server, core, report, CLI, dashboard, verify) and matched their Git blob IDs to the tree. Reviewed high-impact dependency paths and selected implementation ranges; this is a risk-based source audit, not a proof of absence of all defects. The existing Ubuntu workflow installs the package and runs `scripts/verify.py`; its success is real but does not establish comprehensive functional, data-source or host acceptance. No remote repository changes were made.

Direct Git access in the auditing container failed DNS resolution, so the full repo was not independently executed. Arithmetic and SQLite-query counterexamples were independently reproduced; they are not executions of the full production functions. The included gate runner was syntax checked and its missing/skip/fail, parameterized-failure, source drift and report-exclusion behavior tested locally on synthetic inputs. Actual acceptance tests must be implemented and run by Cursor in your local repository. No production broker accounts, credentials or market subscriptions were accessed in this audit.

## 2. Decision and target architecture

Keep the existing single-user, local-first Python + SQLite + existing DeepSeek Harness/MCP/Streamlit + Windows scheduler. The system shall be a **read-only investment decision-support OS**. It may gather public evidence, read authorized brokerage/account state, forecast, compare plans, journal explicitly approved decisions and monitor risks. It must never submit, modify, cancel or exercise broker orders. Do not add microservices, a new frontend, a new agent framework, or paid subscriptions without explicit approval. Make real positive end-to-end workflows work; blanket disabling is not an acceptable way to pass. Unsupported data or models must explicitly fail closed, with honest feature scope.

The fundamental architecture is one common data contract: immutable full PortfolioSnapshot plus time-stamped market/broker/evidence snapshots → deterministic accounting/risk/reducer → financial research/valuation → actual decision alternatives → policy gate → explicit human approval → idempotent decision journal → monitoring/learning. Flex remains the historical ledger, live broker state remains a separate source. No private information can be sent to public models or outbound web tools. All internal financial calculations use complete data, not presentation pagination. All numerical outputs must identify base currency, unit, date and coverage. Unknown is not zero. Use unit-specific tolerances; no silent default for mandatory fields.

## 3. Release gates and what Cursor must deliver

**G0 — Defects reproduced/traceable.** For each mandatory ID below, create a real production-function or entry-point test. Demonstrate failure on pinned baseline or show it already passes with specific code/output; capture sanitized evidence. If an ID comprises several subconditions, test each. Use external-boundary mocks, not mocks of the behavior under test. Keep expected values independent of production calculation. Include positive and negative cases. No manual insertion of missing intermediary fields in a claimed data-pipeline E2E case. Never fabricate source content.

**G1 — OFFLINE_VERIFIED (the pre-submit target).** All 52 mandatory acceptance IDs pass, all subcases pass, existing verification/Golden Evals pass, dependency check and syntax compilation exit 0. Run on a clean supported Python environment; use Windows locally and Linux CI. Test with synthetic data or recorded public source fixtures. Every mandatory ID must have executable tests in `tests/acceptance/` named `test_F01_...`, etc. Parameterization allowed. No skipped, xfailed, empty or placeholder tests. Retain baseline tests and do not weaken assertions or thresholds, except explicit, documented semantic correction backed by independent reference. Every claimed feature has at least one successful end-to-end positive control; invalid data must be blocked. Run `release_gate.py` against the actual working tree, not a different version. Mark G1 achieved only from actual report artifacts. Have another review or operator inspect test semantics; a JUnit status alone does not prove correctness. If tests cannot be satisfied, leave work unsubmitted and record the blocker. Do not push failing code or tag a production release.

**G2 — LIVE_VERIFIED (operator-dependent).** On authorized local host, validate TWS/Gateway is in Read-Only API mode; expected managed accounts and account currencies; complete account summary/positions, timestamps, quotes, actual option metadata/Greeks when entitled; compare both accounts with actual broker statement at aligned timestamps. CAD conversion documented, no unaccounted quantity differences, NAV/cash reconciled per account within CAD0.01 when same-time values available (otherwise disclose timing/mark differences and do not call exact reconciliation). Validate real SEC pipeline on at least three representative issuers against independently read filings, including non-calendar issuer and recent comparative/restatement facts. Verify Windows ACL, private model routing, egress canary test and actual scheduler/launcher. Entitlement/credential/dependency limitations must be explicit; no pressure to purchase data, no secret upload. Live data and model availability are never inferred from replay. For no permissions, report `BLOCKED_EXTERNAL` for the affected tests, and remain offline Beta. Real API tests must use read-only account data and safe rate limits. No actual broker trade.

**G3 — PRODUCTION_ACCEPTED.** Minimum 20 completed trading sessions of genuine operator-side shadow operation with timestamped release/snapshot IDs, job successes/failures and recovery evidence, no unresolved P0/critical financial, privacy, approval, or data integrity defects; compare each session against broker, preserve stale/partial flags; demonstrate restore of a real sanitized backup to separate sandbox; verify data coverage, model/data source versions, and no undisclosed hard IPS violations. A skipped day does not count; a material defect resets the affected qualification window. This confirms engineering reliability, NOT demonstrated investment alpha. Live entitlement gaps stay restricted/disabled and explicitly scoped. No G3 from one synthetic readiness call.

## 4. Exact Cursor workflow (submission control)

1. Create a local candidate branch from the actual current HEAD; do not push to main or make a release tag. Record `git rev-parse HEAD`. Inspect each implementation and manifest ID independent of past messages. Keep original contract and report deviations; no silently deleting/renumbering cases.
2. Copy this kit into the project `validation/` directory (three files). Add acceptance tests in `tests/acceptance/` and minimal external fixtures. Use temporary DB/config/home and deny real external calls via injected transport or offline sandbox. For SEC cases preserve raw public payloads and expected independent checks. For broker cases fake EClient transport, callbacks, events and errors, not the adapter itself. For concurrency use barriers and worker processes/fault injection. For secret canaries use synthetic markers. Do not use actual portfolio in offline CI.
3. Implement root-cause corrections and migrate all downstream consumers. Use true typed errors/statuses, exact contract/account identity, finite/validated values and time-aware data. Use Decimal for money; CAD cents for accounting checks, appropriate relative/absolute tolerance for Greek/valuation checks. Do not round intermediate calculations to force a test pass. Reproduce real paths through actual functions/CLI/MCP.
4. Install dependencies in a clean venv including pytest (declare test dependency, use tested ibapi installation for real adapter and fake transport for offline). Run:

   ```powershell
   python -m pip install -e .
   python -m pip install pytest
   python validation/release_gate.py --root .
   ```

   Gate executes compile, pip check, existing `scripts/verify.py` (including Golden Eval), and actual pytest acceptance suite, collecting JUnit and logs. It rejects missing/failed/skipped IDs, runtime failure, absent baseline success marker, and source drift during run. The runner itself does not construct tests, enforce an OS firewall, inspect test assertion quality or verify real IBKR data; those are required separate controls. Set network guard in test fixtures and keep real credentials out. Inspect `validation/report.md` and JSON. The runner only produces `OFFLINE_VERIFIED` or `BLOCKED`, always `production_accepted=false`.
5. Required review artifacts: `validation/report.json`, `validation/report.md`, `validation/evidence/acceptance.xml`, logs with SHA256, candidate source fingerprint, actual test source and sanitized fixtures; also `validation/reproduction.md` with baseline red/green result for each ID, exact command, observed/expected, code locations and any already-fixed proof. No real private inputs in tracked logs. Record exact supported capability matrix G1/G2/G3 and limitations.
6. **Only after local G1 PASS:** commit the tested source and test files (exclude secret/raw data and private logs), then run `python validation/release_gate.py --root . --verify-report`. This checks the current file-content fingerprint, report, manifest and raw JUnit/log digests. If any source/test/config change, rerun full gate. Candidate working tree must be clean except intentionally untracked validation evidence; confirm `git status --short` and submitted commit contents match the tested source. No `--no-verify`, no manually editing results, no replacing the manifest with an easier one. If you want a local pre-push hook, call the same verify-report command and abort on nonzero; remote protected checks still required.
7. Push the passing candidate branch, open PR; run the SAME gate in CI on Windows and Ubuntu, upload sanitized JUnit and reports, then merge only on both pass. Set branch protection if owner chooses; local hook is not a substitute for CI. Run gate again on merged commit if merge changes tree. No production label until G2/G3 remain demonstrably satisfied. If user authorizes only local work, do not push at all.

### Machine-readable contract

`acceptance_manifest.json` is the immutable scope of G1. `release_gate.py` is a small standard-library runner; it checks actual pytest results and reproducible source fingerprint. The complete case descriptions are reproduced below for human review. Do not treat installing this kit as implementing any of the 52 tests or closing findings.

## 5. Mandatory test matrix (all 52 cases)


### Financial correctness, accounting and risk

**F01 · P0** — Several analytics still consume the bounded portfolio/tax view, although full loader exists.

Source: `finance.py:152-202; decision.py:9-58,130-188,505-624`.

**Acceptance:** Import 55 positions on two accounts, then change max_tool_rows from 1 to 50. Exposure, shock, attribution, factor risk, pre-trade, optimizer, tax totals and approval fingerprints must be invariant. Only display pages differ; combined and per-account denominators must be explicit.

**F02 · P0** — Loader claims a consistent read transaction but no explicit BEGIN after schema; overlapping imports can revise historical NAV rows from older reports.

Source: `finance.py:83-202; core.py:28-40`.

**Acceptance:** Use separate SQLite connections, barrier after first SELECT, commit replacement snapshot before second SELECT. Reader returns all-old or all-new, never hybrid. Import newer statement then older overlapping report for another end date; newer per-account/per-day NAV and records are preserved; import unrelated older account/backfill succeeds. Duplicate import unchanged.

**F03 · P0** — Freshness and quality are incomplete: future statement can be treated as fresh; empty/incomplete positions or unknown cash can be accepted.

Source: `finance.py:55-69,83-202,445-522`.

**Acceptance:** Freeze Toronto time: future report rejected; weekend/holiday last valid close accepted under documented calendar policy; missing required position section, mismatched sources, missing cash, nonfinite values, duplicate account/contract or unrecognized currency cannot yield advice_allowed. Valid empty position set with cash-only NAV succeeds.

**F04 · P0** — Transfer pairing and return accounting can silently assume currency or omit noncash external flows.

Source: `finance.py:281-407`.

**Acceptance:** CAD 1000 opening, USD100 contribution at 1.4, CAD1140 closing: TWR 0. Same contribution between two NAV dates: 0. Missing FX or unknown currency blocks; internal +100.00/-100 across accounts yields 0 external; unmatched/ambiguous legs block; external in-kind contribution CAD200 with NAV rising 200 produces 0 return or explicit unsupported block. Old/unconfigured accounts excluded.

**F05 · P1** — XIRR exact-grid early return hides ambiguity; VaR/Sharpe treats irregular intervals as daily.

Source: `finance.py:343-407; decision.py:59-89`.

**Acceptance:** Equal +100/-100 same-day => undefined, not -99%; annual -100,+230,-132 => multiple roots including 10% and 20%, return ambiguous/None; -100,+110 one year => 10% within 1e-6. Missing daily bars must suppress or correctly resample daily risk. Document approximation of TWR, not exact.

**F06 · P0** — Reducer accepts unknown side as sell, silent invalid input, quantity inconsistent with amount, short cover crosses zero.

Source: `quant.py:544-636`.

**Acceptance:** BUY 10 shares at CAD100 with amount CAD500 => reject inconsistent values; invalid side, negative fee, NaN, nonpositive price, absent account/conid in executable plan => reject. Sell 11 of 10 and cover 2 of short 1 reject unless supported distinct opening trade. Same symbol two distinct contracts selected only by exact identity. Research-only notional estimates explicitly non-executable.

**F07 · P0** — Missing cash becomes zero in some paths; actual margin and registered-account permissions absent.

Source: `quant.py:544-636; finance.py:13-50`.

**Acceptance:** TFSA cash CAD100, buy CAD100 fee1 => block; cash unknown => block; margin account buying power CAD250 buy CAD300 => block; unavailable broker margin fields => no executable margin plan. A configured TFSA cannot be overridden into margin by IPS. Cash contributions explicitly account-bound; withdrawal cannot make registered cash negative.

**F08 · P0** — Option risk ignores positions near zero value and guessed contract multiplier/FX.

Source: `quant.py:126-260,544-636`.

**Acceptance:** Existing 1 USD call, premium 5, spot100, delta .5, mult100, FX1.4, NAV CAD10000 => economic weight .70; buy 1 with consistent USD premium => 1.40; even premium=0 with qty=1 => .70. Adjusted mult10 => .07; short put sign correct; close to qty0 => no exposure. Unknown or nonfinite delta/FX, sentinel or out-of-range delta, stale underlying => incomplete/blocked.

**F09 · P0** — A single authoritative reducer exists but constraints and coverage are not uniform across direct plan, optimizer, audit.

Source: `quant.py:126-260,544-636`.

**Acceptance:** Gross 160% at limit 135% => hard violation in all plan paths and diagnostic audit severity. +8000/-8000 at NAV10000 => gross160%, net0. Unknown asset class and empty positions on incomplete ledger cannot imply zero risk. Single-stock, tactical, cash, cluster and drawdown explicitly categorized HARD/SOFT/TARGET; configured hard constraints block, ordinary approvals cannot bypass.

**F10 · P0** — Optimizer uses fresh contribution to compare an existing-cash trade; incomplete option coverage and rounded proposal vs model divergence.

Source: `quant.py:637-820; decision.py:625-733; ops.py:431-476`.

**Acceptance:** Use NAV10000, cash1000 and BUY500 from existing cash: NAV unchanged except fees, cash500; external contribution500 then buy500 => NAV10500 cash1000. Full account-specific plan for both. Total allocated+retained=new funds exactly to cent; report risk of emitted rounded trades; post-plan hard violation => blocked. Infeasible plan returns typed status not uncaught exception. All existing options included or risk marked incomplete.

**F11 · P0** — ACB keyed by tax bucket and symbol, split potentially applied more than once across accounts, commission currency guessed.

Source: `decision.py:919-987`.

**Acceptance:** Two taxable accounts holding identical stock 10+10 at CAD10+20 => pooled ACB300, 2:1 split reported in both accounts => 40 units, ACB300, not 80. Sell 10 at CAD12 => proceeds120, relieved75, gain45 before fee. Third-currency commission requires own FX. Missing chronological identity/time or opening cost => partial/blocked. Separate option contracts/security identifiers and registered accounts; never call estimates tax-ready.

**F12 · P1** — Attribution residual identity not independent reconciliation; historical windows bounded and fees may use FX=1.

Source: `decision.py:804-918,1032-1070; finance.py:227-236`.

**Acceptance:** 1000 opening +100 external +30 independently marked market P&L +14 CAD dividend -7 CAD commissions =1137 close. USD fees/dividend converted at dated FX; absent conversion blocks. One option bought $5/share and sold $6/share x100 => USD100 not 59500. Missing opening date blocks, not first available fallback. Reconcile actual accounting components and separately disclose unexplained residual; no tautological pass.


### Broker and live data integrity

**B01 · P0** — Event completion alone does not prove complete payload; account queries share caches and do not validate expected account set.

Source: `broker.py:186-288,339-365`.

**Acceptance:** Drive real IBKRTWSAdapter with fake EClient/EWrapper callback transport: out-of-order, duplicate, delayed, missing required tags and unknown accounts. Only complete configured accounts + valid positions with completion events yield verified capture. Timeout/no accounts/disconnect => partial or unavailable; no success after old callbacks. Cancel and clean request state. No actual orders.

**B02 · P0** — Partial capture written with adapter name, later read as status ok/live_verified based only on adapter name.

Source: `broker.py:85-171,339-365`.

**Acceptance:** Fake timeout yields partial, persist and restart process: still partial, never enrich or pass readiness. Replayed matching NAV/positions never live. Successful capture followed by failure preserves last-good snapshot with correct stale/error status, not fresh success. Include capture status, request outcomes, entitlements, per-field timestamps/content hash.

**B03 · P0** — Broker reconciliation sums NetLiquidation across currencies and checks only combined NAV and quantity.

Source: `broker.py:133-171,261-275`.

**Acceptance:** CAD1000 + USD1000 @1.4 => CAD2400; missing FX blocks. Opposite account NAV errors +100/-100 with equal combined NAV => not reconciled. Skip absent configured account, unknown currencies or stale/mismatched timestamps; detect missing cash/margin/positions; distinguish intraday timing differences vs matched same-time snapshot.

**B04 · P0** — Quote requests lack complete identity, data type/timestamp validation; delayed/sentinel values treated as live; request IDs collide with conids.

Source: `broker.py:215-255,289-337`.

**Acceptance:** Inject request ID 9001 and contract conid 9001 for different contracts; never cross-associate. Missing/crossed/negative quotes, IB sentinel delta, delayed/frozen marketDataType, timeout, denied entitlement and no Greek callback => unavailable/partial, not live. Valid per-contract market data with timestamp and entitlement => live. Verify cancellation and cleanup.

**B05 · P0** — Only trade gate enriches risk; enrichment accepts replay/stale data and hash suffix ignores contents.

Source: `broker.py:366-391; quant.py:126-130,261-268,637-645; ops.py:221-222,778-816`.

**Acceptance:** Use same Flex position and two valid broker snapshots with delta .2/.8, changed underlying/margin. Every current risk surface and optimizer uses same validated as-of snapshot; approval fingerprint changes; stale/replay/partial data cannot enter executable plan. Flex historical TWR remains unchanged. Repeated identical inputs hash identically.

**B06 · P1** — Security definition option parameters are not executable option contracts/quotes.

Source: `broker.py:289-353; cli.py:28,81; quant.py:998-1042`.

**Acceptance:** Fake chain includes expiration/strike/trading class/multiplier, qualify supported contracts via contract details, obtain bid/ask/OI/Greeks where entitled, preserve timestamps. Missing OI or quote timestamp, stale > policy TTL, invalid no-arbitrage quote rejected or research-only. Return actual contract IDs, not unsupported cross-product pairs.

**B07 · P0** — Official ibapi optional dependency not provisioned; adapter readonly claim not independently enforced.

Source: `broker.py:186-252; pyproject.toml; config/harness-mcp.example.yml`.

**Acceptance:** Clean install documented broker extras/config; fake API imported successfully, test connection failure distinct from missing dependency. Allowlist official read-only requests; verify no order-capable tool/CLI path; real TWS read-only setting separately operator-tested. Pin/test supported API callback signature, close worker on timeout.


### Financial data, research and valuation

**C01 · P0** — SEC mapping missing equity; test manually inserts it; raw source provenance hashes only description.

Source: `market.py:371-490; scripts/verify.py:420-459`.

**Acceptance:** Replay real saved unmodified SEC CompanyFacts responses for non-calendar mature tech and second issuer; through sync_filings transport only, no direct DB inserts. Produce Revenue, EBIT, OCF, CapEx, equity, cash, current+noncurrent debt and dated shares with verified accession/units or explicit missing statuses. Archive source bytes/hash/parser version, prove values against independent filing extracts.

**C02 · P0** — SEC fy/fp describe report context, not every comparative fact; grouping can overwrite current and prior periods.

Source: `market.py:422-488`.

**Acceptance:** Same 2026-Q2 filing emits current quarter 120 and prior-year comparison 90 with same fy/fp but different start/end. Map to distinct fiscal periods using explicit fiscal calendar/accession; retain both. Include 52/53-week fiscal year and quarter ended previous calendar year. Reject ambiguous periods.

**C03 · P0** — MAX(revision_seq) selects insertion ordering not publication time; dedup discards legitimate equal-value filings.

Source: `market.py:379-388,905-921`.

**Acceptance:** Import Aug revision value120 first, May original100 second; as-of June=>100, September=>120. Import reverse order and repeat => same results and no duplicate exact source facts. Preserve accessions and legitimate revisions, no future timestamp leakage. Date-only publication conservatively eligible after release cutoff, timestamps normalized UTC.

**C04 · P0** — Derives only last cumulative vintages; fallback Q3/Q4 can use revised future data without propagating availability.

Source: `market.py:445-488`.

**Acceptance:** Q1=100 Feb; H1=210 May; Q1 restated110 Aug; 9M=330 Aug; FY=460 Nov. As-of June Q2=110, no Aug data; later recompute compatible history or mark conflict, preserve old vintage. Q3 standalone120 and Q4=130 with compatible inputs; each derived fact availability=max(all component availability). OCF cumulative 100/200/300/400 => quarters 100 each; never sum YTD.

**C05 · P0** — EPS and weighted average shares are nonadditive; blindly differencing FY/9M gives nonsense.

Source: `market.py:390-488,923-953`.

**Acceptance:** FY diluted weighted shares100, 9M diluted weighted shares100 => Q4 weighted shares100 if legitimate day-weighted derivation, not0. EPS FY 4, nine-month3 not automatically valid Q4 EPS unless earnings/shares and method support derivation. Explicit point-in-time shares, BS end balances and flow facts distinct; never sum balance-sheet figures.

**C06 · P0** — TTM selects last four labels regardless of continuity/units; missing metrics become zero; annual BS may be mismatched.

Source: `market.py:923-986`.

**Acceptance:** Quarters 2025Q4,2026Q1,Q2,Q4 without Q3 => TTM blocked. Mixed currencies or units => convert with evidence or block. Missing equity/net debt/growth/dilution => insufficient, never silently zero. Verify four contiguous OCF/capex revenues and latest allowable matched balance-sheet date, stale fields disclosed.

**C07 · P0** — Evidence record hashes generic CIK text; financial facts not anchored to accession or audit trail.

Source: `market.py:863-953,955-986; decision.py:989-1031`.

**Acceptance:** Two different source documents for same ticker produce distinct content SHA; facts link to exact document/field and period; historical as-of does not reference later-retrieved unsupported evidence. No fabricated values, source IDs or consensus. Conflicting evidence withhold affected recommendation; untrusted filing text cannot trigger writes.

**C08 · P0** — DCF defaults and SBC/dilution need consistent economic treatment; finite/range validation incomplete.

Source: `quant.py:339-418; decision.py:91-114,418-504`.

**Acceptance:** Independent golden model FCFF100 perpetual g2%, WACC10% => EV1250 when 100 is next-period FCFF; subtract debt200, shares10 =>105/share. Driver model yearly rows reproduce revenue/NOPAT/reinvestment/PV and probability sum=1. Verify monotonic WACC/g on positive flows; reject NaN/inf, invalid rates, g>=WACC, negative shares. Negative bear margins allowed; explicitly reconcile SBC and dilution without double count; terminal reinvestment matches ROIC.

**C09 · P1** — Selection of valuation method does not implement full sector forecast, automatic DCF uses generic defaults.

Source: `quant.py:339-418,1043-1086; decision.py:989-1031`.

**Acceptance:** For bank, profitable software, semi-cycle, pre-profit early stage and diversified issuer, output suitable method + supported model or explicit unsupported (no forced generic FCFF; no confident plan). WACC debt/equity weights and after-tax debt where relevant, source inputs and per-share units. RIM negative residual income retained, SOTP net debt once; verify simple independent reference cases.

**C10 · P0** — Discovery still admits guessed values, stale prices and mixed missing coverage; no real source-backed automated valuation test.

Source: `decision.py:989-1031; market.py:955-986`.

**Acceptance:** Use C01 pipeline fixtures only and frozen valuation/price dates: qualified cheap company yields candidate; expensive yields no compelling; missing half the universe reports coverage and partial/insufficient; stale price or missing share count => no executable recommendation. NaN/inf feature rejected. Candidate valuation output cites inputs/as-of and clearly preliminary. No manual feature injection for this E2E case.

**C11 · P0** — PIT expectations may be first recorded together with actuals; forecast timestamp checks and outcome horizon incomplete.

Source: `quant.py:483-543; knowledge.py:237-270`.

**Acceptance:** Freeze time. Expectation created after exact release time or same-call first expectation+actuals => rejected or retrospective/non-scored. Partial actuals merge preserving prior ones; missing guidance cannot label beat-and-raise. Forecast cannot resolve before creation or before defined outcome window unless rule permits early outcome; racing resolution uses CAS. Brier 0.8 success + 0.2 failure => .04, unresolved excluded.

**C12 · P1** — Research facts and filing diffs may be incomplete; no independent evidence/research acceptance.

Source: `decision.py:218-258,733-803; market.py:521-639`.

**Acceptance:** Run source-backed memo for one real cached issuer and a missing-data issuer. Every material claim/source input traceable; distinguish fact/inference/assumption; real contrary evidence and null case, event dates, risk and quantifiable thesis breaker. Absent evidence = research incomplete, cannot mark investment-ready. Untrusted prompt injection ignored.


### Workflow, approvals and autonomous operations

**W01 · P0** — Run params saved but resume version/data binding and concurrent execution insufficient.

Source: `ops.py:193-260,477-495`.

**Acceptance:** Crash at every workflow checkpoint, restart same inputs and workflow version; exactly one logical run, preserved immutable params and input hash. Modified input/version or unapproved source changes => restart required. Two resume requests cannot interleave state. Completed/rejected states non-resumable. Legacy migration tested.

**W02 · P0** — Initial approver does not own lease; recovery can steal active claim; no fencing, mutable note changes payload.

Source: `ops.py:511-629; knowledge.py:116-137`.

**Acceptance:** Use barriers and 10 concurrent approve requests plus reject. Initial live owner lease prevents recovery; expired owner cannot finalize. Exactly one journal row per run, same immutable payload/note, one terminal state. Force original owner to resume after new owner; stale fencing token rejected. Never delete another owner intent on exception.

**W03 · P0** — Approval crash recovery must cover every persistence boundary and invalid TTL.

Source: `ops.py:511-647`.

**Acceptance:** Kill a worker after intent claim, after journal commit, after decision mark, before workflow completion; recover with no duplicates, journal and workflow state converge. Invalid/missing timestamp blocks. TTL uses original staging time, not latest retry. An expired or rejected plan cannot write journal; existing committed journal recovered accurately. Reject crash path reconcilable.

**W04 · P0** — Investment Committee approval acceptance still mainly trade gate; research may be None and allocator may fail.

Source: `ops.py:431-476,571-590; decision.py:625-733`.

**Acceptance:** Inject missing financials/DCF, failed allocator, blocked risk, empty thesis breaker, contradictory evidence separately: no accepted trade plan. Complete evidence + validated valuation + explicit funding source/account + policy-pass + alternatives => approval pending only; human approval writes journal, never order. Hash binds exact evidence/model/snapshot/quote/IPS/action and invalidates on material change.

**W05 · P1** — Monitoring is a simple screen; alerts dedup suppresses changed risk; scheduler status ignores failures.

Source: `ops.py:751-827; scripts/daily-sync.ps1; cli.py:81-114`.

**Acceptance:** Replay earnings/news material event, changed thesis KPI and duplicate event; one dedup alert, updated severity/details and re-open on material change. Trigger required research/review, not automatic trade. Re-run same schedule window idempotently. Insufficient data alerted distinctly from no opportunity. Enforce bounded research budget/timeouts and surfaced job failure.

**W06 · P1** — New functions not consistently exposed to existing user surfaces, e.g. risk data freshness.

Source: `server.py; dashboard.py; cli.py; ops.py`.

**Acceptance:** Real MCP stdio and CLI, Streamlit headless tests: portfolio, risk, ACB, discovery, evidence/valuation, alerts, approvals/resume, health, broker sync accessible through appropriate authorized surface; consistent results/status. CLI invalid or blocked operational sync exits nonzero. Dashboard visibly marks stale/missing vs genuine 0; never expose private actions in public mode.


### Security and privacy

**S01 · P0** — Public-only tool registration does not isolate filesystem/DB; knowledge --public-only ignored.

Source: `server.py:6-45,218-246; core.py:9-40; config/harness-mcp.example.yml`.

**Acceptance:** Create private canaries in finance/knowledge/ops, private watchlist, logs, raw Flex and credentials. Run public research process with distinct home/config and access guard: every tool, default quote universe, malicious path/URL and public knowledge mode refuse private reads, canaries absent from payload/logs. Deny or remove knowledge public mode. Private/public stores separated; test real denial rather than just tool name list.

**S02 · P0** — Local model default is not enforced privacy routing; outbound web tools available in private presets.

Source: `config/harness-mcp.example.yml:1-120; market.py:748-833`.

**Acceptance:** Use fake local/cloud/web endpoints; private conversation containing canary or tool result attempting remote model/web exfiltration never reaches external endpoint. Route public research separately with sanitized explicitly allowed input. Local-only mode enforced beyond persona text; no secret-bearing inherited environment for public process.

**S03 · P0** — SSRF DNS check then re-resolve creates TOCTOU; redirects and proxies must be checked.

Source: `market.py:734-833`.

**Acceptance:** Use fake resolver/transport only: public DNS first then loopback on connect, redirect to link-local/private/IPv6, localhost variants, credentials, non-http, proxy bypass and oversized response. Deny before private connection. Resolve/pin validated destination or enforce restricted egress policy; bound response size and timeout. No external attack traffic.

**S04 · P0** — Private data paths, Windows ACLs, symlink traversal and backups not validated end-to-end.

Source: `core.py:24-40,51-89; knowledge.py:30-62; .gitignore`.

**Acceptance:** Test allowed root and symlink/reparse escape, secrets by content and filename, restricted directory/file access, scrubbed logs, no raw real account files or keys in staged/git artifacts. Local file indexing allowlist and user-approved memory only. Operator Windows ACL and firewall validation separate live gate.


### Quantitative models and learning

**Q01 · P0** — Missing open silently replaced by close; provider mixing, incomplete backfills and non-atomic replacement.

Source: `market.py:211-274,680-695`.

**Acceptance:** Bars missing open => no claimed next-open fill; use explicit alternative or block. Fake provider returns empty/truncated/duplicate/out-of-order rows; verify quality status and safe fallback; atomic staged replacement preserves last-good on fault. Currency, splits/dividend adjustment, provenance and version recorded, no mixed basis in return series.

**Q02 · P1** — Backtest strategies may have unfair timing and fixed contribution cadence; hash must bind actual data and assumptions.

Source: `quant.py:893-973,1087-1131`.

**Acceptance:** Freeze 60-day hand-computable path, same initial/cash contributions at explicit dates, fees and idle yield. Signal closing day t executes next valid open t+1. Flat underlying no fees yields exact contributions + documented cash yield. 3x two days +10%, -9.0909% => underlying flat, 3x loss about 5.4545% before drag. Higher financing cost lowers leveraged sleeve; no leverage double count. Change a price/FX/basis/parameter => hash changes; no future prices influence past trades.

**Q03 · P1** — Risk model uses defaults for missing samples and simple equity covariance; correlated options and FX missing.

Source: `quant.py:265-315,637-820; decision.py:505-624`.

**Acceptance:** Use known correlated return series; PSD covariance and correct sample alignment, CAD FX converted (USD +10%, FX -10% => CAD -1%). Constant/short data => no precise beta/Sharpe; clearly mark priors. Marginal contributions sum under model; options scenario nonlinear or marked incomplete. Stress at -20% and FX move against reference; no more precision than coverage supports.

**Q04 · P1** — ETF composition manually supplied, unvalidated weights, leverage and unknown ETFs disguised as direct exposures.

Source: `quant.py:974-997`.

**Acceptance:** QQQ weight50%, constituent A20%; direct A10% => A total20%. Unknown ETF or residual reported unallocated; sums above 100% reject unless documented leveraged net/gross methodology; snapshot date/source and stale cutoff. TQQQ economic lookthrough handled via explicit multiplier, no double counting ETF + constituents.

**Q05 · P1** — Option estimates European approximation and quote validity incomplete; chain timestamp missing can pass.

Source: `quant.py:824-892,998-1042`.

**Acceptance:** Independent BSM call/put reference, parity for European no-dividend, N(d2) distinct from delta, IV invert within tolerance. Invalid premium > spot and NaN => no payoff recommendation. For 1 covered call strike110 premium2 cost100, expiry spot120 profit1200 USD; spot80 loss1800. Roll close ask2/open bid2.8 => credit80 for 100 multiplier; disclose fees, assignment, dividends, early exercise, Greeks and exposure. Missing/stale quote rejects executable plan.

**Q06 · P1** — No robust strategy comparison/learning evaluation beyond basic metrics.

Source: `quant.py:893-973,1087-1131; knowledge.py:237-270`.

**Acceptance:** Cash-flow-matched QQQ/passive vs active, common dates/costs, separate train and test with frozen parameters, report sample size/drawdown and representative crisis windows; no claims of alpha from synthetic. Forecast outcome resolution must have evidence and fixed horizon. Learning recommendations never automatically alter policy.


### Operational reliability and release gate

**O01 · P0** — Per-db WAL backup valid but cross-db consistency and complete manifest missing.

Source: `core.py:28-40,51-89`.

**Acceptance:** Backup during approval commit and import using quiescence or consistent generation; restore all expected databases to separate temp root, verify SQLite integrity and logical invariants (journal/run links, row counts, hashes) and restore config/policy/raw authoritative inputs or explicit prerequisite. Missing/corrupt files/partial restore => fail; no overwrite current production state.

**O02 · P0** — Readiness verifies adapter label and latest eval percent, not true freshness, complete risk or hard violations.

Source: `ops.py:778-816; broker.py:114-171`.

**Acceptance:** Table-driven: stale ledger, stale/partial/replay broker, wrong account, invalid mandatory risk, hard breach (AUDIT mode), stale eval from old source, stuck intent, backup failure each blocks readiness. Fully valid synthetic readiness can only produce offline_ready, production_accepted false. Actual shadow session history required separately.

**O03 · P1** — Market/monitor exit codes ignored; CLI returns 0 for blocked status; stream duration not strictly bounded.

Source: `scripts/daily-sync.ps1; cli.py:96-114; market.py:655-695`.

**Acceptance:** Inject failure at every stage and connection never ending; scheduler reports specific stage/exit status, preserves previous valid report and logs redacted error; retry with backoff/idempotence. No credentials => explicit blocked, not success. Use bounded timeout on healthy stream. Windows PowerShell script test with fake dependencies.

**O04 · P0** — CI only runs existing smoke script, may not cover real callback/PIT/migration/failure paths.

Source: `scripts/verify.py:11-14,420-464,467-504; .github/workflows/ci.yml`.

**Acceptance:** Keep baseline verify and Golden Evals; add mandatory tests named by this manifest and run through gate runner. No skip/xfail/only mocks of production function. Test previous DB schema migration twice, rollback on migration error, Windows and Linux clean install. Validate report matches exact candidate tree. Independent oracle and red-before/green-after evidence or prior-fix proof.

**O05 · P1** — Dependency/environment/host integration and operational usability not proved by Ubuntu CI.

Source: `pyproject.toml; dashboard.py; scripts/open-agent.ps1; config/personal.example.json`.

**Acceptance:** Reproducible install from clean environment; pin supported Python and dependency ranges/lock, pip check, no secret config required for offline mode. CLI help/config checks, headless UI, long explicit local workflow with timeout, disconnect/restart, report generation; no silently swallowed exceptions. Actual Windows launcher/Task Scheduler validation on operator host.


## 6. Final release declaration template

- Candidate commit / tested source fingerprint:
- G1: PASS / FAIL, mandatory cases passed / 52, exact CI run IDs on both systems:
- G2: PASS / BLOCKED_EXTERNAL / NOT_RUN, actual permission/source evidence (redacted):
- G3: PASS / NOT_RUN, actual trading-session history:
- Open issues / restricted capabilities:
- Production accepted: false until G3 confirmed by operator.

A test/report that asserts a capability exists, a module imports, an adapter name is live, or a synthetic fixture was accepted is insufficient as end-to-end evidence. Do not submit until G1 meets the explicit standard; use G2/G3 as separately tracked real-world conditions.
