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
    if os.environ.get('INVESTMENT_OS_PUBLIC_ONLY')=='1' and domain in {'finance','knowledge','ops'}:
        raise PermissionError(f'public-only process is forbidden from opening {domain}.sqlite3')
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

def backup_databases(dest_dir=None):
    """Online WAL-safe SQLite snapshot backup via sqlite3.Connection.backup() with PRAGMA integrity_check verification."""
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    target_root=private_dir(Path(dest_dir) if dest_dir else (private_dir(HOME)/'backups'/stamp))
    backed_up=[]
    for domain in ('finance','market','knowledge','ops'):
        src_path=HOME/f'{domain}.sqlite3'
        if not src_path.exists(): continue
        dst_path=target_root/f'{domain}.sqlite3'
        src=sqlite3.connect(src_path, timeout=15)
        dst=sqlite3.connect(dst_path, timeout=15)
        try:
            src.backup(dst)
            integrity=dst.execute('PRAGMA integrity_check').fetchone()[0]
            if integrity!='ok': raise RuntimeError(f'backup integrity check failed for {domain}: {integrity}')
            backed_up.append({'domain':domain,'bytes':dst_path.stat().st_size,'integrity':integrity})
        finally:
            dst.close(); src.close()
    return {'status':'ok','backup_dir':str(target_root),'databases':backed_up,'verified_at':now()}

def restore_databases(backup_dir, dest_dir):
    """Restore SQLite backup snapshot into dest_dir with PRAGMA integrity_check verification."""
    b_root=Path(backup_dir); d_root=private_dir(Path(dest_dir))
    restored=[]
    for domain in ('finance','market','knowledge','ops'):
        src_path=b_root/f'{domain}.sqlite3'
        if not src_path.exists(): continue
        dst_path=d_root/f'{domain}.sqlite3'
        src=sqlite3.connect(src_path, timeout=15)
        dst=sqlite3.connect(dst_path, timeout=15)
        try:
            src.backup(dst)
            integrity=dst.execute('PRAGMA integrity_check').fetchone()[0]
            if integrity!='ok': raise RuntimeError(f'restore integrity check failed for {domain}: {integrity}')
            restored.append({'domain':domain,'bytes':dst_path.stat().st_size,'integrity':integrity})
        finally:
            dst.close(); src.close()
    if not restored: raise ValueError(f'no SQLite backups found in {b_root}')
    return {'status':'ok','restored_dir':str(d_root),'databases':restored,'verified_at':now()}
