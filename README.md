# IBKR Agent Harness

**Local-first quantitative portfolio intelligence, stateful workflow harness, and privacy-segregated MCP control plane for Interactive Brokers.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![MCP Compatible](https://img.shields.io/badge/MCP-stdio-orange.svg)](https://modelcontextprotocol.io/)
[![Zero Auto-Trading](https://img.shields.io/badge/Execution-Read--Only%20%2F%20HITL-critical.svg)](#safety--privacy-guarantees)

`ibkr-agent-harness` turns Interactive Brokers Activity Flex XML statements and free public market feeds into an institutional-grade quantitative decision system designed to run reliably on **constrained local hardware (32K-context local LLMs on consumer GPUs)** as well as **128K-200K external Cloud LLM APIs**—without ever leaking private brokerage balances to external model providers or letting an LLM hallucinate financial math.

---

## Why This Project Exists

Most open-source "AI finance agents" fail in real portfolio management for four structural reasons:

1. **Probabilistic Math & Fabricated Metrics:** Letting an LLM sum multi-currency tax lots, guess DCF discount math, or treat raw NAV changes (which include deposits/withdrawals) as investment return.
2. **Silent Stale-Price Substitution:** Quietly substituting 15-minute delayed or prior-close scraped quotes for live prices when computing valuation upside.
3. **Context Window Collapse on Local Models:** Dumping `60+ KB` of raw brokerage positions, transactions, and coding-agent tool schemas into a `32K`-context local model, triggering GPU-to-RAM KV paging and severe latency degradation.
4. **Cloud Privacy Leakage:** Sending raw account IDs, dollar balances, and execution history to third-party LLM APIs during equity research.

`ibkr-agent-harness` solves all four at the systems architecture layer:

- **93.7% First-Turn Context Compression:** Coarse-to-fine pre-computed executive digests compress multi-account state, risk, tax alerts, and behavioral guardrails from `~60.5 KB` (`~15,100` tokens) down to `~3.8 KB` (`~950` tokens).
- **100% Deterministic Financial & Matrix Engine:** Every accounting reconciliation, bisection solver, covariance decomposition, and SEC filing diff runs in pure Python (`Decimal` + SQLite WAL).
- **Hardware-Enforced Dual-Plane DLP:** Private ledger tools bind to your local LLM (`127.0.0.1`), while `--public-only` physically strips all brokerage ledger and personal memory tools from the MCP process when using Cloud LLM APIs (DeepSeek, OpenAI, Anthropic, OpenRouter).
- **Stateful Workflows + Programmatic Human-in-the-Loop:** Multi-stage workflows checkpoint state in SQLite and halt at `awaiting_approval`; the LLM has no approval tool—only the human operator (via CLI or loopback Control Plane UI) can approve and commit decisions.

---

## System Architecture

```text
+-----------------------------------------------------------------------------------+
|                           APPLICATION & OPERATOR SURFACES                         |
|  DeepSeek Harness / MCP Clients (:3080)  |  Streamlit Control Plane (:8501) | CLI |
+--------------------+---------------------+-----------------+----------------+-----+
                     |                                       |
                     v                                       v
+----------------------------------------+   +--------------------------------------+
|     DUAL-PLANE MODEL & MCP GATEWAY     |   |   OPERATOR-ONLY INGEST & HITL GATE   |
|                                        |   |                                      |
|  [Preset 1: Local Private Agent]       |   |  - IBKR Activity Flex XML Sync (v3)  |
|   -> Local LLM (Strata / vLLM / Ollama)|   |  - Alpaca IEX WS Stream + Batch REST |
|   -> Full Read-Only Investment MCP     |   |  - SEC EDGAR / Finnhub / FRED / BoC  |
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
|  - 6 Declarative Workflows (`workflow_runs` checkpoints & `awaiting_approval`)    |
|  - DLP-Safe Tool Tracing (`latency_ms`, `approx_tokens`, zero payload logging)    |
|  - 7-Invariant Golden Evaluation Harness (`eval_runs` regression benchmark)       |
+-----------------------------------------+-----------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
|               DETERMINISTIC QUANT, LEDGER & MEMORY ENGINES (SQLite WAL)           |
|                                                                                   |
|  `finance.py`     Multi-account Flex reconciliation, transfer pairing, TWR / XIRR |
|  `decision.py`    Multi-Stage & Reverse DCF, 90d Covariance/MCTR, Factor Stress,  |
|                   ITA s.54 Tax Engine, 6-Point Pre-Trade Gate, Counterfactuals    |
|  `market.py`      Freshness-gated IEX/EDGAR/Finnhub/FRED/BoC + SSRF Web Search    |
|  `knowledge.py`   SQLite FTS5 Trigram + CJK Substring RAG, Mistake/Thesis/Brier   |
+-----------------------------------------------------------------------------------+
```

---

## Core Quantitative & Agentic Capabilities

### 1. Multi-Account IBKR Flex Ledger & Jurisdiction-Aware Tax Engine (`finance.py`, `decision.py`)
- **Fail-Closed Multi-Account Aggregation:** Verifies matching base currency (default `CAD`) and identical statement end dates across accounts (`taxable`, `tfsa`, `rrsp`, `fhsa`) before reporting combined NAV.
- **Internal Transfer Neutrality:** Pairs opposite cross-account cash transfers by `(date, currency, abs(amount))` so moving cash between accounts never distorts external contributions, daily-approximation TWR, or bisection `XIRR`.
- **Cross-Account Tax & Superficial-Loss Radar:** Tracks `conid`-level weights across taxable and registered shelters, flags **30-day superficial-loss collisions (Canadian ITA s.54)** when a loss candidate in `taxable` is simultaneously held in `tfsa`/`rrsp` or bought within 30 days, and tracks unrecoverable 15% IRS dividend withholding inside TFSA accounts.

### 2. Institutional Valuation & SEC EDGAR Forensic Pipeline (`decision.py`, `market.py`)
- **Multi-Stage Linear-Fade DCF (`value_multi_stage_dcf`):** Explicit `1-20` year cash-flow runway with Damodaran-style linear growth fade toward perpetuity, automatic warning when terminal value exceeds `80%` of Enterprise Value, and an embedded `3x3` (`WACC` x `Terminal Growth`) sensitivity matrix.
- **Reverse DCF Expectations Solver (`solve_reverse_dcf`):** Uses 70-step bisection to solve for the exact market-implied Stage-1 FCFF CAGR embedded in the current stock price—converting vague LLM price targets into falsifiable growth hurdles.
- **SEC EDGAR `10-K/20-F` Forensic Accrual & Paragraph Delta (`audit_sec_filing_delta`):**
  - Extracts multi-year US-GAAP `OperatingCashFlow`, `CapEx`, `DerivedFreeCashFlow`, `NetDebt`, and `DilutedShares`, flagging accrual quality divergence (`OCF / Net Income < 0.75` or rising Net Income with falling OCF) and YoY share dilution (`> 3%`).
  - Runs deterministic `difflib` paragraph matching across consecutive `Item 1A (Risk Factors)` sections, **stripping `~90-95%` unchanged legal boilerplate** and surfacing newly added risk disclosures plus **hypothetical-to-realized modal escalations** (`may/could result` shifting to `has/have experienced`).

### 3. Cross-Asset Covariance, MCTR & Counterfactual Attribution (`decision.py`)
- **90-Day Sample Covariance Decomposition (`get_factor_risk`):** Computes annualized portfolio volatility, per-position **Marginal Contribution to Risk (MCTR)**, **Percentage Contribution to Variance**, empirical Beta against `QQQ`/`SPY` price proxies, Diversification Ratio, **Effective Number of Independent Bets (HHI $1 / \sum w_i^2$)**, and high-correlation clusters ($\rho \ge 0.65$).
- **Empirical Beta + FX Joint Stress Testing (`get_factor_stress`):** Simulates joint equity benchmark shocks and non-base currency (`USD/CAD`) FX moves simultaneously.
- **What-If Buy-and-Hold Counterfactual Replay (`get_counterfactual_attribution`):** Automatically excludes `CASH` spot FX conversions and replays every executed equity trade over a `30-365` day lookback against a zero-trade static baseline—decomposing **buy entry timing alpha**, **premature winner exits (foregone upside)**, **protective exits (drawdown saved)**, and **commission drag**.

### 4. Behavioral Habit Telemetry, Brier Calibration & 6 Stateful Workflows (`ops.py`, `knowledge.py`)
- **Closed-Loop Behavioral Memory:** Mines execution logs for `<=7d / <=30d` round-trip churn, `-20%` drawdown cost-anchoring lots, and untracked theses, linking them to FTS5-indexed `mistake`, `lesson`, `goal`, and `decision` records with **Brier Score** ($\frac{1}{N}\sum(p_i - y_i)^2$) probability calibration.
- **6 Declarative Stateful Workflows with Human-in-the-Loop Checkpoints:**
  1. `morning_portfolio_review` — End-to-end reconciliation, covariance scan, tax radar, and habit brief.
  2. `equity_valuation_and_expectations` — Multi-stage/Reverse DCF + peer correlation + optional thesis staging.
  3. `tax_aware_pre_trade_approval` — 6-point institutional Red-Team gate halting at `awaiting_approval`.
  4. `recursive_behavioral_post_mortem` — Execution habit audit + Brier calibration + guardrail rule staging.
  5. `sec_filing_delta_red_flag` — Forensic `10-K/20-F` accrual & `Item 1A` diff halting on held-stock red flags.
  6. `counterfactual_decision_attribution` — Active trading vs static Buy-and-Hold attribution replay.

### 5. Zero-Cost Multi-Provider Market Data & SSRF-Guarded Web Search (`market.py`)
- **Providers ($0/mo free tiers):** Alpaca Basic IEX (reconnecting WebSocket trade stream + batch REST quotes), Finnhub (company news & earnings calendar), SEC EDGAR (`companyfacts` + `Archives`), FRED (`DGS3MO` risk-free rate), Bank of Canada Valet (`FXUSDCAD`), `yfinance` historical/international bar fallback, and keyless Bing/Google News RSS + DuckDuckGo outbound web search with DNS/IP private-network **SSRF blocking**.

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

### 2. Run the Synthetic Verification & Golden Evaluation Suites (No API Keys Needed)

```bash
python scripts/verify.py
python -m personal_agent.cli eval
```

Both suites execute in an isolated temporary directory using sanitized synthetic Flex fixtures (`tests/fixtures/*.xml`) and verify all 42 accounting, valuation, freshness, SSRF, DLP isolation, and workflow state-machine invariants.

### 3. Configure Local Credentials Privately

Copy the example configuration (all `config/personal.json` and `local/` files are gitignored):

```bash
cp config/personal.example.json config/personal.json
```

1. Map your IBKR account IDs to clean aliases in `config/personal.json` (so raw account IDs never enter LLM prompts):
   ```json
   "finance": {
     "accounts": {
       "U0000000": "taxable",
       "U0000001": "tfsa"
     },
     "base_currency": "CAD",
     "flex_queries": [
       {"token_env": "IBKR_FLEX_TOKEN", "query_env": "IBKR_FLEX_QUERY_ID"}
     ],
     "max_snapshot_age_days": 4
   }
   ```
2. Export API keys in your OS user environment (never write secret values into JSON/YAML files):
   - **IBKR Activity Flex v3:** `IBKR_FLEX_TOKEN`, `IBKR_FLEX_QUERY_ID`
   - **Market Feeds (optional, free tiers):** `ALPACA_API_KEY`, `ALPACA_API_SECRET`, `FINNHUB_API_KEY`, `FRED_API_KEY`
   - **SEC EDGAR User-Agent:** set `"edgar_contact": "Your Name your@email.com"` in `config/personal.json`

### 4. Sync Data & Launch the Control Plane Dashboard

```bash
# Import a local Activity Flex XML export or sync directly from IBKR Flex Web Service v3
python -m personal_agent.cli import-flex /path/to/activity_flex.xml
# or: python -m personal_agent.cli sync-flex

# Sync market quotes, historical bars, FX, macro, news, earnings, and SEC filings
python -m personal_agent.cli market-sync

# Launch the 9-tab Streamlit Control Plane (bound strictly to loopback 127.0.0.1)
streamlit run dashboard.py --server.address 127.0.0.1 --server.port 8501
```

---

## Connecting to DeepSeek Harness, Claude Desktop, or Cursor (MCP)

### Option A: DeepSeek Harness (Hybrid Local LLM + Cloud-Safe DLP Overlay)
1. Copy `config/harness-mcp.example.yml` to `config/harness-mcp.local.yml` (gitignored) and replace `/ABSOLUTE/PATH` with your repository path.
2. Boot DeepSeek Harness Web with the overlay:
   ```bash
   dsh web --patch config/harness-mcp.local.yml
   ```
3. Three isolated presets appear automatically:
   - **`Investment Agent (Local Private)`** — Full private ledger, tax, covariance, DCF, and workflows (pair with your local model on `127.0.0.1:8080`).
   - **`Public Equity Research (Cloud-API Safe)`** — Runs `personal_agent.server investment --public-only` (physically excludes private ledger & memory tools; safe for DeepSeek / OpenAI / Claude / OpenRouter APIs).
   - **`Personal Knowledge`** — Local SQLite FTS5 trigram RAG & durable memory.

### Option B: Any Standard MCP Client (Claude Desktop / Cursor / Windsurf)
Use `--public-only` if your MCP client routes prompts to a cloud LLM, or omit `--public-only` only when connected to a local model:

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

## CLI & Stateful Workflow Reference

```bash
# Readiness, observability traces, and regression evals
ibkr-agent health
ibkr-agent telemetry
ibkr-agent eval

# Token-compact daily executive brief, habit audit, covariance risk, and counterfactual replay
ibkr-agent brief
ibkr-agent habits
ibkr-agent factor-risk
ibkr-agent attribution --days 90
ibkr-agent filing-delta AAPL --sync

# Execute declarative stateful workflows & manage Human-in-the-Loop approvals
ibkr-agent workflows
ibkr-agent run-workflow morning_portfolio_review
ibkr-agent run-workflow tax_aware_pre_trade_approval --params '{"symbol":"AAPL","action":"BUY","amount_base":"2500","account":"taxable","thesis":"Services FCF compounding","falsifier":"FCF margin <22%"}'
ibkr-agent approvals
ibkr-agent approve <run_id> --note "Verified thesis and sizing"
```

---

## Repository Structure

```text
.
├── config/
│   ├── harness-mcp.example.yml   # Hybrid Local + Cloud-Safe DeepSeek Harness overlay template
│   └── personal.example.json     # Sanitized configuration template (env variable names only)
├── personal_agent/
│   ├── core.py                   # Private SQLite WAL connections, bounds, and file guards
│   ├── finance.py                # IBKR Activity Flex v3 parser, reconciliation, TWR & XIRR
│   ├── decision.py               # Multi-stage/Reverse DCF, Covariance/MCTR, Tax, Counterfactuals
│   ├── market.py                 # Alpaca WS/REST, EDGAR XBRL & Item 1A Diff, Finnhub, FRED, BoC, Web
│   ├── knowledge.py              # SQLite FTS5 Trigram + CJK RAG & episodic/Brier memory store
│   ├── ops.py                    # Stateful Workflow Engine, HITL Approvals, Telemetry & Eval Suite
│   ├── report.py                 # 93.7%-compressed Daily Executive Brief & archival JSON reports
│   ├── server.py                 # Traced stdio MCP servers (`investment`, `--public-only`, `knowledge`)
│   └── cli.py                    # Operator CLI entry point (`ibkr-agent`)
├── scripts/
│   ├── verify.py                 # 42-assertion synthetic integration & security test suite
│   ├── daily-sync.ps1            # Idempotent scheduled Flex + market + brief sync script
│   ├── backup.ps1                # Timestamped local SQLite WAL snapshot backup
│   └── open-agent.ps1            # Local launcher for Control Plane (:8501) & Harness (:3080)
├── skills/
│   ├── investment/SKILL.md       # Coarse-to-fine routing & quantitative grounding SOP
│   └── knowledge/SKILL.md        # Source-cited local RAG & memory SOP
├── tests/fixtures/               # 100% synthetic Flex XML test fixtures
├── dashboard.py                  # 9-tab loopback Streamlit Control Plane & HITL Approval UI
├── IMPLEMENTATION.md             # Architectural invariants & accounting rules specification
└── pyproject.toml                # Package manifest (`ibkr-agent-harness`)
```

---

## Safety & Privacy Guarantees

- **No Brokerage Execution:** This software contains zero order-placement code paths and never connects to IBKR TWS / Gateway trading endpoints.
- **No Financial Advice:** All valuation, risk, tax, and attribution outputs are deterministic mathematical transformations of user-supplied statements and public filings for educational and research use only. Always verify tax treatment (including Canadian ACB and ITA s.54 superficial-loss rules) with a qualified tax professional.
