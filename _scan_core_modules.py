"""Scan core modules: list files, lines, public classes/functions."""
import ast
from pathlib import Path

ROOT = Path(r"C:\Users\Lenovo\.minimax-agent-cn\projects\poc")
MODULES = ["workflow", "contamination", "recovery", "provenance", "llm", "validation", "artifacts"]

for mod in MODULES:
    mod_dir = ROOT / mod
    if not mod_dir.exists():
        print(f"=== {mod}/ NOT FOUND ===")
        continue
    print(f"\n=== {mod}/ ===")
    for p in sorted(mod_dir.glob("*.py")):
        if p.name.startswith("_") or p.name in ("__init__.py",):
            continue
        try:
            src = p.read_text(encoding="utf-8")
        except Exception as e:
            print(f"  {p.name}: ERR {e}")
            continue
        lines = len(src.splitlines())
        try:
            tree = ast.parse(src, filename=str(p))
        except SyntaxError as e:
            print(f"  {p.name}: SYNTAX {e}")
            continue
        classes = []
        funcs = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                methods = [m.name for m in node.body if isinstance(m, ast.FunctionDef) and not m.name.startswith("_")]
                classes.append((node.name, methods))
            elif isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                funcs.append(node.name)
        print(f"  {p.name}  ({lines} lines)")
        for cn, ms in classes:
            print(f"    class {cn}: {ms}")
        for fn in funcs:
            print(f"    def {fn}()")
