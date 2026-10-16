"""Emit a success marker only after pytest exits successfully."""

import subprocess
import sys

if __name__ == "__main__":
    result = subprocess.run([sys.executable, "-m", "pytest", "-q"], check=False)
    if result.returncode:
        raise SystemExit(result.returncode)
    print("TEST SUITE PASSED")
