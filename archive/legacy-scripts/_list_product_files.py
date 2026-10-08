from pathlib import Path
root = Path(r"C:\Users\Lenovo\.minimax-agent-cn\projects\poc")
files = []
for p in sorted(root.rglob("*.py")):
    rel = p.relative_to(root)
    parts = rel.parts
    if "tests" in parts: continue
    if "experiments" in parts: continue
    if "__pycache__" in parts: continue
    if "poc_trash" in parts: continue
    if "assets" in parts: continue
    if "artifact_data" in parts: continue
    if "data" in parts: continue
    if rel.name.startswith("_"): continue
    n = sum(1 for _ in open(p, encoding="utf-8", errors="ignore"))
    files.append((str(rel).replace("\\","/"), n))
total = 0
for f, n in files:
    print(f"{f:<55} {n:>5}")
    total += n
print("-" * 65)
print(f"TOTAL: {total} lines, {len(files)} files")
