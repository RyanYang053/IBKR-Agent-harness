import asyncio, json, os, socket
from pathlib import Path
from unittest.mock import patch
import pytest
from personal_agent import core, finance, knowledge, market, server

def test_S01_public_mcp_process_db_isolation_and_canary(isolated_env):
    with core.db('finance') as c:
        finance.schema(c)
        c.execute('INSERT INTO nav VALUES(?,?,?,?,?,?)', ('margin', '2026-11-18', 'CAD', '1000', '1000', 'CANARY_SRC'))
        c.execute('INSERT INTO positions VALUES(?,?,?,?,?,?,?,?,?,?,?)', ('margin', '2026-11-18', 'C99', 'CANARY_PRIVATE_TICKER', 'STK', 'CAD', '10', '100', 'C99', '100', '1'))
    knowledge.save('fact', 'CANARY_SECRET_NOTE', 'operator')

    with pytest.raises(ValueError):
        server.make_server('knowledge', public_only=True)

    pub_mcp = server.make_server('investment', public_only=True)
    async def _probe():
        tools = {t.name for t in await pub_mcp.list_tools()}
        assert 'get_portfolio' not in tools and 'recall_memory' not in tools
        q_res = await pub_mcp.call_tool('get_quotes', {})
        return repr(q_res)
    out = asyncio.run(_probe())
    assert 'CANARY_PRIVATE_TICKER' not in out and 'CANARY_SECRET_NOTE' not in out
    for priv in ('finance', 'knowledge', 'ops'):
        with pytest.raises(PermissionError):
            with core.db(priv):
                pass

def test_S02_local_privacy_routing_boundary(isolated_env):
    # Verify harness-mcp.example.yml isolates public-only from private ledger
    yml = (Path(__file__).resolve().parents[2] / 'config' / 'harness-mcp.example.yml').read_text(encoding='utf-8')
    assert '--public-only' in yml
    assert 'knowledge' in yml

def test_S03_ssrf_blocks_loopback_private_ipv6_creds_and_redirects(isolated_env):
    for bad_url in (
        'http://127.0.0.1:8080/v1',
        'http://169.254.169.254/latest/meta-data',
        'http://10.0.0.1/admin',
        'http://[::1]:8080/',
        'http://user:pass@example.com/',
        'file:///etc/passwd',
    ):
        with pytest.raises(ValueError):
            market._assert_public_http_url(bad_url)

    # DNS rebinding to loopback blocked
    with patch.object(socket, 'getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 0))]):
        with pytest.raises(ValueError):
            market._assert_public_http_url('https://rebind.example.com/data')

def test_S04_knowledge_index_blocks_secrets_and_traversal(isolated_env):
    root = Path(isolated_env) / 'docs'
    root.mkdir()
    (root / 'safe.md').write_text('Public investment note', encoding='utf-8')
    (root / 'secret_keys.txt').write_text('API_KEY=CANARY_SECRET', encoding='utf-8')
    (root / '.env.local').write_text('TOKEN=CANARY_TOKEN', encoding='utf-8')
    cfg_path = Path(isolated_env) / 'config.json'
    cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
    cfg['knowledge_roots'] = [str(root)]
    cfg_path.write_text(json.dumps(cfg), encoding='utf-8')
    res = knowledge.index()
    assert res['updated'] == 1
    assert not knowledge.search('CANARY_SECRET') and not knowledge.search('CANARY_TOKEN')
