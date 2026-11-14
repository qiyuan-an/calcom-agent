"""Opt-in loopback server check; never opens a session or loads private .env.

Run from repository root: .venv/bin/python -m scripts.check_ui_server
Checks the actual HTTP server only, not browser behavior or SSH forwarding.
"""

import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen


def main():
    process = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app.py", "--server.address=127.0.0.1",
         "--server.port=8501"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        healthy = False
        for _ in range(50):
            if process.poll() is not None:
                raise RuntimeError("UI server exited before becoming ready (port 8501 must be free)")
            try:
                with urlopen("http://127.0.0.1:8501/_stcore/health", timeout=1) as response:
                    healthy = response.status == 200 and response.read() == b"ok"
                if healthy:
                    break
            except (URLError, TimeoutError):
                time.sleep(0.1)
        if not healthy:
            raise RuntimeError("UI health check timed out")
        with urlopen("http://127.0.0.1:8501/", timeout=2) as response:
            if response.status != 200 or b"<html" not in response.read().lower():
                raise RuntimeError("UI root page check failed")
        print("PASS: loopback Streamlit health and root HTTP page on port 8501")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


if __name__ == "__main__":
    main()
