# IBKR Agent Harness — Personal Investment Operating System

**Local-first quantitative portfolio intelligence, CIO Investment Policy engine, stateful workflow harness, and privacy-segregated MCP control plane for Interactive Brokers.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![MCP Compatible](https://img.shields.io/badge/MCP-stdio-orange.svg)](https://modelcontextprotocol.io/)
[![Golden Evals](https://img.shields.io/badge/Golden_Evals-10%2F10_(100%25)-brightgreen.svg)](#2-run-the-46-assertion-verification--10-case-golden-evaluation-suites-no-api-keys-needed)
[![Zero Auto-Trading](https://img.shields.io/badge/Execution-Read--Only%20%2F%20HITL-critical.svg)](#safety--privacy-guarantees)

`ibkr-agent-harness` transforms Interactive Brokers Activity Flex XML statements and zero-cost public financial feeds into a complete **Personal Investment Operating System (Personal Investment OS)**. It unites seven institutional functions—**CIO Policy Governance, Fundamental & SEC Forensics, Expectations & Multi-Model Valuation, Covariance & Tail Risk, Incremental Capital Allocation, Analytical Options & Strategy Labs, and Behavioral Attribution**—over a stateful Agent Harness engineered for **constrained local hardware (32K-context local LLMs on consumer GPUs)** as well as **128K-200K external Cloud LLM APIs**.

---

## Why This System Exists

Most open-source "AI finance agents" fail in real portfolio management for five structural reasons:

1. **Probabilistic Arithmetic & Hallucinated Consensus:** Letting an LLM sum multi-currency tax lots, invent analyst consensus estimates, or mistake raw NAV changes (which include deposits) for investment return.
2. **Nominal vs Economic Leverage Blindness:** Treating `20%` in `TQQQ` as a `20%` weight instead of `60%` daily-reset equity exposure with $\frac{1}{2}L(L-1)\sigma^2$ volatility variance drain, or claiming "+5% Alpha" without adjusting for `1.4x` market Beta.
3. **Silent Stale-Price Substitution:** Quietly substituting 15-minute delayed or prior-close scraped quotes for live quotes when computing valuation upside.
4. **Context Window Collapse on Local Models:** Dumping `60+ KB` of raw brokerage XML rows and generic coding-agent tool schemas into a `32K`-context local model, triggering GPU-to-RAM KV paging.
5. **Cloud Privacy Leakage:** Sending raw account IDs, dollar balances, and execution history to third-party cloud LLM APIs during equity research.

`ibkr-agent-harness` resolves all five at the architecture layer:

- **93.7% First-Turn Context Compression:** Coarse-to-fine pre-computed executive digests compress multi-account state, risk, tax alerts, and behavioral guardrails from `~60.5 KB` (`~15,100` tokens) down to `~3.8 KB` (`~950` tokens).
- **100% Deterministic Quant, Tax & Options Engines:** Every accounting reconciliation, bisection solver, covariance decomposition, Black-Scholes Greek, walk-forward backtest, and SEC `10-K` diff runs in pure Python (`Decimal`, `math.erf`, SQLite WAL).
- **Hardware-Enforced Dual-Plane DLP:** Private ledger tools bind to your local LLM (`127.0.0.1`), while `--public-only` physically strips all brokerage ledger and personal memory tools from the MCP process when routing public research to Cloud LLM APIs (DeepSeek, OpenAI, Anthropic, OpenRouter).
- **7 Stateful Workflows + Programmatic Human-in-the-Loop:** Multi-stage workflows checkpoint state in SQLite and halt at `awaiting_approval`; the LLM has no approval tool—only the human operator (via CLI or loopback Control Plane UI) can approve and commit decisions.

---

## System Architecture

```text
+-----------------------------------------------------------------------------------+
|                           APPLICATION & OPERATOR SURFACES                         |
|  DeepSeek Harness / MCP Clients (:3080)  |  9-Tab Investment OS UI (:8501)  | CLI |
+--------------------+---------------------+-----------------+----------------+-----+
                     |                                       |
                     v                                       v
+----------------------------------------+   +--------------------------------------+
|     DUAL-PLANE MODEL & MCP GATEWAY     |   |   OPERATOR-ONLY INGEST & HITL GATE   |
|                                        |   |                                      |
|  [Preset 1: Local Private Agent]       |   |  - IBKR Activity Flex XML Sync (v3)  |
|   -> Local LLM (Strata / vLLM / Ollama)|   |  - Alpaca IEX WS Stream + Batch REST |
|   -> 37 Read-Only Investment MCP Tools |   |  - SEC EDGAR / Finnhub / FRED / BoC  |
|                                        |   |  - Approve / Reject Staged Workflows |
|  [Preset 2: Public Research Agent]     |   +------------------+-------------------+
|   -> Cloud APIs (DeepSeek/OpenAI/Claude|                      |
|   -> `--public-only` Zero-PII MCP      |                      |
+--------------------+-------------------+                      |
                     |                                          |
                     +--------------------+---------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
|            STATEFUL WORKFLOW, OBSERVABILITY & EVAL PLANE (`ops.py`)               |
|  - 7 Declarative Workflows (incl. 8-Stage `investment_committee_memo` & HITL)     |
|  - Living Theses (`living_theses`) & Point-in-Time Earnings Expectations          |
|  - DLP-Safe Tool Tracing (`latency_ms`, `approx_tokens`, zero payload logging)    |
|  - 10-Invariant Golden Evaluation Harness (`eval_runs` 100% benchmark suite)      |
+-----------------------------------------+-----------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
|          DETERMINISTIC QUANT, POLICY, LEDGER & MEMORY ENGINES (SQLite WAL)        |
|                                                                                   |
|  `finance.py`     Multi-account Flex reconciliation, transfer pairing, TWR / XIRR |
|  `quant.py`       IPS Policy Engine, Economic Leverage, CVaR 95%, Driver 3-Scen   |
|                   DCF, Capital Allocator (Quarter-Kelly), BSM Options & Backtests |
|  `decision.py`    Multi-Stage & Reverse DCF, 90d Covariance/MCTR, Factor Stress,  |
|                   ITA s.54 Tax Engine, 6-Point Pre-Trade Gate, Counterfactuals    |
|  `market.py`      Freshness-gated IEX/EDGAR/Finnhub/FRED/BoC + SSRF Web Search    |
|  `knowledge.py`   SQLite FTS5 Trigram + CJK Substring RAG, Mistake/Thesis/Brier   |
+-----------------------------------------------------------------------------------+
```

---

## Core Investment OS Capabilities

### 1. CIO Investment Policy Engine & Capital Allocation (`quant.py`)
- **Formal Investment Policy Statement (`config/investment_policy.example.json`):** Governs target return, core vs tactical bounds, minimum cash floor, drawdown budget, single-stock concentration limits, and daily economic leverage caps.
- **Incremental Capital Allocator (`allocate_capital`):** Directly answers *"How should I deploy `$5,000` of new cash today across candidates vs `QQQ` vs holding cash?"* under IPS constraints, empirical volatility, conservative **Quarter-Kelly (`0.25x`)** sizing caps, and **optimal Canadian account location (`TFSA` vs `Taxable`)**.

### 2. Multi-Method Valuation, Living Theses & Earnings Expectations (`quant.py`, `decision.py`, `market.py`)
- **Driver-Based Bear/Base/Bull DCF (`value_driver_scenarios`):** Explicit operating model (`Revenue -> Operating Margin -> NOPAT -> CapEx & NWC Reinvestment -> FCFF -> Probability-Weighted Fair Value Range`) alongside **Multi-Stage Linear-Fade DCF** (`3x3` WACC x Terminal sensitivity) and 70-step bisection **Reverse DCF** (`solve_reverse_dcf` market-implied growth hurdle).
- **Point-in-Time Earnings Expectation Gap (`record_earnings_expectation`):** Locks pre-earnings management guidance or internal model snapshots prior to release and audits post-earnings revenue/EPS surprises without fabricating consensus.
- **Versioned Living Theses & `L0-L4` Event Triage (`upsert_living_thesis`, `triage_catalyst`):** Tracks thesis versions, KPIs, catalysts, and invalidation breakers, filtering daily headlines into `L0` (ignore noise) through `L4` (Thesis Breaker -> trigger Committee Memo).
- **SEC EDGAR `10-K/20-F` Forensic Audit (`audit_sec_filing_delta`):** Multi-year XBRL accrual conversion (`OCF / Net Income < 75%`) & share dilution checks + `difflib` `Item 1A` paragraph delta **stripping `~90-95%` unchanged boilerplate** and flagging `may/could` -> `has/have experienced` risk escalations.

### 3. Advanced Tail Risk, Economic Exposure & Covariance MCTR (`quant.py`, `decision.py`)
- **Leverage-Adjusted Economic Exposure & Decay Simulator:** Separates nominal weights from daily-reset economic exposure (`TQQQ/UPRO/SOXL = 3x`, `QLD/SSO = 2x`, `SQQQ = -3x`) and simulates continuous variance drain $\frac{1}{2}L(L-1)\sigma^2$ plus empirical daily-reset drawdowns.
- **Expected Shortfall (`CVaR 95%`), Covariance MCTR & Stress Testing:** Computes tail `CVaR 95%`, underwater recovery days, per-stock **Marginal Contribution to Risk (MCTR)**, variance share, **HHI Effective Number of Bets**, high-correlation clusters ($\rho \ge 0.65$), and joint **Empirical Beta + `USD/CAD` FX stress scenarios**.

### 4. Analytical Options Lab & Walk-Forward Strategy Lab (`quant.py`)
- **Options Lab (`analyze_options`):** Pure-Python Black-Scholes-Merton pricing, analytical `Delta / Gamma / Vega / Theta`, bisection Implied Volatility solver from market premium, **Covered Call** annualized static/called yield & breakeven analyzer, and **LEAPS** delta-leverage & capital-saved comparator.
- **Strategy Lab (`run_strategy_backtest`):** Zero-look-ahead historical replay comparing `(A) 100% Buy & Hold`, `(B) 70/30 Drawdown Dip-Buying`, `(C) Periodic DCA`, and `(D) 80% Core + 20% 3x Daily-Reset Sleeve` across `CAGR`, `Max Drawdown`, `Calmar`, `Sharpe`, and `Max Underwater Days`.

### 5. Counterfactual Attribution, Beta-Adjusted Alpha & 7 Stateful Workflows (`decision.py`, `ops.py`)
- **Beta-Adjusted Return & Counterfactual Replay:** Separates passive market Beta contribution from selection/FX residual, and replays every executed equity trade (excluding `CASH` FX spot conversions) against a zero-trade Buy-and-Hold baseline to quantify buy-timing alpha, premature winner exits, protective exits, and commission drag.
- **7 Declarative Stateful Workflows (with Programmatic HITL Approval Gates):**
  `morning_portfolio_review` · `investment_committee_memo` (8-stage *Should I Invest?* synthesis) · `equity_valuation_and_expectations` · `tax_aware_pre_trade_approval` · `sec_filing_delta_red_flag` · `counterfactual_decision_attribution` · `recursive_behavioral_post_mortem`.

---

## Quick Start

### 1. Install (`Python 3.11+`)

```bash
git clone https://github.com/RyanYang053/IBKR-Agent-harness.git
cd IBKR-Agent-harness

python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# macOS / Linux:
# source .venv/bin/activate

python -m pip install -e .
```

### 2. Run the 46-Assertion Verification & 10-Case Golden Evaluation Suites (No API Keys Needed)

```bash
python scripts/verify.py
ibkr-agent eval
```

Both suites run offline in an isolated temporary directory—testing multi-account Flex reconciliation, transfer neutrality, Multi-Stage/Driver/Reverse DCF inversion, Black-Scholes IV inversion, leveraged-ETF variance drain, ITA s.54 superficial-loss detection, SSRF firewalling, `--public-only` Cloud DLP isolation, workflow `awaiting_approval` -> `Resume After Approval`, and headless 9-tab Streamlit UI rendering.

### 3. Configure Local Credentials Privately

```bash
cp config/personal.example.json config/personal.json
cp config/investment_policy.example.json config/investment_policy.json
```

1. Map your IBKR account IDs to aliases (`taxable`, `tfsa`, `rrsp`) in `config/personal.json` (gitignored).
2. Export API keys in your OS user environment (never write secret values into JSON/YAML files):
   - **IBKR Activity Flex v3:** `IBKR_FLEX_TOKEN`, `IBKR_FLEX_QUERY_ID`
   - **Market Feeds (optional, free tiers):** `ALPACA_API_KEY`, `ALPACA_API_SECRET`, `FINNHUB_API_KEY`, `FRED_API_KEY`
   - **SEC EDGAR User-Agent:** set `"edgar_contact": "Your Name your@email.com"` in `config/personal.json`

### 4. Sync Data & Launch the 9-Tab Personal Investment Intelligence UI

```bash
ibkr-agent import-flex /path/to/activity_flex.xml
# or: ibkr-agent sync-flex
ibkr-agent market-sync

streamlit run dashboard.py --server.address 127.0.0.1 --server.port 8501
```

---

## Connecting to DeepSeek Harness, Claude Desktop, or Cursor (MCP)

### Option A: DeepSeek Harness (Hybrid Local LLM + Cloud-Safe DLP Overlay)
1. Copy `config/harness-mcp.example.yml` to `config/harness-mcp.local.yml` (gitignored) and replace `/ABSOLUTE/PATH` with your repository path.
2. Boot DeepSeek Harness Web:
   ```bash
   dsh web --patch config/harness-mcp.local.yml
   ```
3. Three isolated presets mount automatically:
   - **`Investment Agent (Local Private)`** — Full 37-tool private OS surface (pair with your local model on `127.0.0.1:8080`).
   - **`Public Equity Research (Cloud-API Safe)`** — Runs `personal_agent.server investment --public-only` (physically excludes private ledger & memory tools; safe for DeepSeek / OpenAI / Claude / OpenRouter APIs).
   - **`Personal Knowledge`** — Local SQLite FTS5 trigram RAG & durable memory.

### Option B: Standard MCP Clients (Claude Desktop / Cursor / Windsurf)
Use `--public-only` whenever your MCP client sends context to a cloud model:

```json
{
  "mcpServers": {
    "ibkr-public-research": {
      "command": "/ABSOLUTE/PATH/.venv/Scripts/python.exe",
      "args": ["-m", "personal_agent.server", "investment", "--public-only"]
    }
  }
}
```

---

## CLI Reference

```bash
# System readiness, DLP-safe observability spans, and 10-case Golden Benchmark
ibkr-agent health
ibkr-agent telemetry
ibkr-agent eval

# CIO policy audit, capital allocation, options lab, strategy backtest & attribution
ibkr-agent brief
ibkr-agent policy
ibkr-agent allocate 5000 --candidates MSFT,QQQ
ibkr-agent options covered_call 100 110 45 --vol 0.32 --cost-basis 94
ibkr-agent backtest --symbol QQQ --capital 10000
ibkr-agent attribution --days 90
ibkr-agent filing-delta NVDA --sync

# Living thesis, earnings expectations, stateful workflows & HITL approvals
ibkr-agent upsert-thesis MSFT --pillars "Enterprise cloud;AI copilot" --breakers "Azure growth <20%"
ibkr-agent expectation MSFT 2026Q4 --basis management_guidance --exp-rev 68000 --act-rev 69500
ibkr-agent workflows
ibkr-agent run-workflow investment_committee_memo --params '{"symbol":"MSFT","amount_base":"3000","revenue_t0":"245000","shares":"7430"}'
ibkr-agent approvals
ibkr-agent approve <run_id> --note "Approved IC Memo"
```

---

## Repository Structure

```text
.
├── config/
│   ├── harness-mcp.example.yml        # Hybrid Local + Cloud-Safe DeepSeek Harness overlay
│   ├── investment_policy.example.json # CIO Investment Policy Statement (IPS) template
│   └── personal.example.json          # Sanitized ledger & market provider template
├── personal_agent/
│   ├── core.py                        # Private SQLite WAL connections, bounds & file guards
│   ├── finance.py                     # IBKR Activity Flex v3 parser, reconciliation, TWR/XIRR
│   ├── quant.py                       # IPS Policy, Economic Exposure, CVaR, Driver DCF,
│   │                                  # Capital Allocator, BSM Options & Strategy Backtester
│   ├── decision.py                    # Multi-Stage/Reverse DCF, Covariance/MCTR, ITA s.54 Tax,
│   │                                  # 6-Point Pre-Trade Gate, SEC Forensics, Counterfactuals
│   ├── market.py                      # Alpaca WS/REST, EDGAR XBRL & Item 1A Diff, Finnhub,
│   │                                  # FRED, Bank of Canada FX & SSRF-guarded Web Search
│   ├── knowledge.py                   # SQLite FTS5 Trigram + CJK RAG & Brier memory store
│   ├── ops.py                         # 7 Stateful Workflows, HITL Queue, Tracing & Eval Suite
│   ├── report.py                      # 93.7%-compressed Daily Executive Brief & JSON archives
│   ├── server.py                      # Traced stdio MCP (`investment`, `--public-only`, `knowledge`)
│   └── cli.py                         # Operator CLI entry point (`ibkr-agent`)
├── scripts/
│   ├── verify.py                      # 46-assertion integration, security & headless UI suite
│   ├── daily-sync.ps1                 # Idempotent scheduled Flex + market + brief sync
│   ├── backup.ps1                     # Timestamped local SQLite WAL snapshot backup
│   └── open-agent.ps1                 # Local launcher for Control Plane (:8501) & Harness (:3080)
├── skills/
│   ├── investment/SKILL.md            # Coarse-to-fine routing & quantitative grounding SOP
│   └── knowledge/SKILL.md             # Source-cited local RAG & memory SOP
├── tests/fixtures/                    # 100% synthetic Flex XML test fixtures
├── dashboard.py                       # 9-tab Personal Investment Intelligence Streamlit UI
├── IMPLEMENTATION.md                  # Architectural invariants & accounting specification
└── pyproject.toml                     # Package manifest (`ibkr-agent-harness` v0.3.0)
```

---

## Safety & Privacy Guarantees

- **No Brokerage Execution:** Zero order-placement code paths exist anywhere in this codebase; it never connects to IBKR TWS / Gateway order-routing endpoints.
- **No Financial or Tax Advice:** All valuation, risk, option, tax, and backtest outputs are deterministic mathematical transformations of user-supplied statements and public filings for research and educational use only. Verify tax calculations (including Canadian ACB and ITA s.54 superficial-loss rules) with a qualified tax professional.
