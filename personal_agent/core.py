"""Private local store; no model or network dependency."""
import os, json, sqlite3, hashlib
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, timezone
ROOT = Path(__file__).resolve().parents[1]
HOME = Path(os.environ.get('PERSONAL_AGENT_HOME', ROOT/'local')).expanduser().resolve()
CONFIG = Path(os.environ.get('PERSONAL_AGENT_CONFIG', ROOT/'config/personal.json')).expanduser().resolve()
def config():
    return json.loads(CONFIG.read_text(encoding='utf-8'))
def operator_env(name):
    if not name: return ''
    val=os.environ.get(name,'').strip()
    if val or os.name!='nt' or 'PERSONAL_AGENT_CONFIG' in os.environ: return val
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as rk:
            raw,_=winreg.QueryValueEx(rk, name)
            return str(raw).strip()
    except OSError:
        return ''
def now(): return datetime.now(timezone.utc).isoformat()
def digest(data): return hashlib.sha256(data).hexdigest()
def private_dir(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path
@contextmanager
def db(domain):
    if domain not in {'knowledge','finance','market','ops'}: raise ValueError('invalid domain')
    p=private_dir(HOME)/f'{domain}.sqlite3'
    c=sqlite3.connect(p, timeout=15)
    c.row_factory=sqlite3.Row
    try:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('PRAGMA foreign_keys=ON')
        yield c
        c.commit()
    except Exception:
        c.rollback(); raise
    finally: c.close()
def bounded(n): return max(1,min(int(n),int(config().get('policy',{}).get('max_tool_rows',50)),100))
def read_text(path):
    p=Path(path)
    if p.stat().st_size > config().get('policy',{}).get('max_file_bytes',10000000): raise ValueError('file too large')
    if p.suffix.lower()=='.pdf':
        from pypdf import PdfReader
        return '\n'.join(f'[page {i+1}]\n'+(page.extract_text() or '') for i,page in enumerate(PdfReader(p).pages))
    if p.suffix.lower() not in {'.txt','.md','.json','.csv'}: raise ValueError('unsupported text type')
    return p.read_text(encoding='utf-8-sig')
