"""Day 13.6 FINAL-FIX: dedent run_test_c body.  The tempdir block
was removed; the body (lines 843..1329) is still at 8-space indent
and must be at 4-space indent (function body level).
"""
from pathlib import Path

p = Path("live_gate_run.py")
text = p.read_text(encoding="utf-8")
lines = text.splitlines(keepends=True)

# Find the start: the line "# 5) execute recovery.  The wrapper..."
start = None
for k, ln in enumerate(lines):
    if "# 5) execute recovery" in ln:
        start = k
        break
if start is None:
    raise SystemExit("WARN: # 5) execute recovery marker not found")

# End: line 1329 (right before "# =====" at 1330).
end = None
for k in range(start, len(lines)):
    if lines[k].startswith("# ====") or lines[k].startswith("def main"):
        end = k
        break
if end is None:
    raise SystemExit("End of run_test_c not found")

print(f"Dedenting lines {start+1}..{end} (1-indexed)")
new_lines = lines[:start]
for ln in lines[start:end]:
    if ln.startswith("        "):
        new_lines.append(ln[4:])
    elif ln.strip() == "":
        new_lines.append(ln)
    else:
        new_lines.append(ln)
new_lines.extend(lines[end:])
p.write_text("".join(new_lines), encoding="utf-8")
print("Dedented run_test_c.")
