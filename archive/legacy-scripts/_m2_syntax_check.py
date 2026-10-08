"""M2 syntax check (read-only) for the 3 modified source files."""
import ast
from pathlib import Path

ROOT = Path(".")
for rel in [
    "llm/parse.py",
    "workflow/claim_tasks.py",
    "_exp02_stage15_real_runner.py",
]:
    p = ROOT / rel
    try:
        ast.parse(p.read_text(encoding="utf-8"))
        print(f"{rel}: SYNTAX OK")
    except SyntaxError as e:
        print(f"{rel}: SYNTAX ERROR -> {e}")
