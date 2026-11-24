"""Exercise the public fixture demo with the repository's actual engines."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def test_public_fixture_demo(tmp_path):
    output = tmp_path / "harness-demo.html"
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "demo.py"), "--output", str(output)], cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    page = output.read_text(encoding="utf-8")
    assert "SYNTHETIC RESEARCH DEMO" in page
    assert "Content-Security-Policy" in page
    assert '6 / 6 passed.' in page
    assert 'Reverse DCF round trip' in page
    assert 'Synthetic two-account NAV' in page
