"""Run reproducible checks and save portable UTF-8 evidence."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import unittest

root = Path(__file__).parent
evidence = root / 'evidence'
evidence.mkdir(exist_ok=True)
tests = subprocess.run([sys.executable, '-m', 'unittest', '-v'], cwd=root, capture_output=True, text=True)
(evidence / 'test-results.txt').write_text(tests.stdout + tests.stderr, encoding='utf-8')
demo = subprocess.run([sys.executable, 'demos.py', 'all'], cwd=root, capture_output=True, text=True)
(evidence / 'demo-output.json').write_text(demo.stdout, encoding='utf-8')
summary = {
    'executed_at_utc': datetime.now(timezone.utc).isoformat(),
    'python': platform.python_version(),
    'test_exit_code': tests.returncode,
    'demo_exit_code': demo.returncode,
    'test_count': unittest.defaultTestLoader.discover(str(root)).countTestCases(),
    'test_command': 'python -m unittest -v',
    'demo_command': 'python demos.py all',
    'network_used_by_demos': False,
    'source_sha256': {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in ('demos.py', 'test_demos.py', 'fixtures.json', 'README.md', 'verify.py')},
}
(evidence / 'verification.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
print(json.dumps(summary, indent=2))
sys.exit(tests.returncode or demo.returncode)
