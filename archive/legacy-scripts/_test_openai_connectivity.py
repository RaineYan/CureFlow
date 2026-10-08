"""Quick test: can we reach api.openai.com:443 from this machine?"""
import subprocess
import re
import sys

r = subprocess.run(
    [
        "powershell", "-NoProfile", "-Command",
        "Test-NetConnection -ComputerName 'api.openai.com' -Port 443",
    ],
    capture_output=True, text=True, timeout=90,
)
print("returncode:", r.returncode)
print("--- stdout ---")
print(r.stdout)
m = re.search(r"TcpTestSucceeded\s*:\s*(True|False)", r.stdout)
print("parsed TcpTestSucceeded:", m.group(1) if m else "UNKNOWN")
