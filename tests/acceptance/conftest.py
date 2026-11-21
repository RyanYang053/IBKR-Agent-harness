import json, os, sys
from importlib import reload
from pathlib import Path
from unittest.mock import patch
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    cfg = tmp_path / 'config.json'
    cfg.write_text(json.dumps({
        'knowledge_roots': [],
        'finance': {'accounts': {'U0000001': 'margin', 'U0000002': 'tfsa'}, 'base_currency': 'CAD', 'flex_queries': [], 'max_snapshot_age_days': 4000},
        'market': {'watchlist': ['QQQ'], 'alpaca_key_env': 'ALPACA_API_KEY', 'alpaca_secret_env': 'ALPACA_API_SECRET', 'finnhub_key_env': 'FINNHUB_API_KEY', 'fred_key_env': 'FRED_API_KEY', 'edgar_contact': 'test@example.com'},
        'policy': {'max_file_bytes': 10000000, 'max_tool_rows': 50}
    }), encoding='utf-8')
    monkeypatch.setenv('PERSONAL_AGENT_HOME', str(tmp_path))
    monkeypatch.setenv('PERSONAL_AGENT_CONFIG', str(cfg))
    monkeypatch.delenv('INVESTMENT_OS_PUBLIC_ONLY', raising=False)
    for k in ('IBKR_FLEX_TOKEN', 'IBKR_FLEX_QUERY_ID', 'ALPACA_API_KEY', 'ALPACA_API_SECRET', 'FINNHUB_API_KEY', 'FRED_API_KEY'):
        monkeypatch.delenv(k, raising=False)
    from personal_agent import core, finance, market, knowledge, decision, quant, ops, broker, report
    for mod in (core, finance, market, knowledge, decision, quant, ops, broker, report):
        reload(mod)
    with patch('urllib.request.urlopen', side_effect=AssertionError('Outbound network forbidden in offline acceptance suite')):
        yield tmp_path
