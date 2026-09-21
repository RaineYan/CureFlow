"""Day 13.6 FINAL-FIX: dedent run_test_c body after removing the
tempfile.TemporaryDirectory block.  The with block is gone; every
line that was inside it (8-space indent) must become 4-space
(function-body) indent.
"""
from pathlib import Path

p = Path("live_gate_run.py")
text = p.read_text(encoding="utf-8")
lines = text.splitlines(keepends=True)

# Find the with line.
start_idx = None
for k, ln in enumerate(lines):
    if ln == "    with tempfile.TemporaryDirectory() as td:\n":
        start_idx = k
        break

if start_idx is None:
    raise SystemExit("WARN: tempdir line not found")

# End of the with block: next non-empty, non-4-space-indented line.
end_idx = None
for k in range(start_idx + 1, len(lines)):
    ln = lines[k]
    if ln.strip() == "":
        continue
    if ln.startswith("    ") or ln.startswith("\t"):
        continue
    end_idx = k
    break

if end_idx is None:
    end_idx = len(lines)

print(f"Found with block at line {start_idx+1}; end at {end_idx+1}")

# Remove the with line; dedent the body (8 -> 4 spaces).
new_lines = lines[:start_idx]
for ln in lines[start_idx + 1:end_idx]:
    if ln.startswith("        "):
        new_lines.append(ln[4:])
    else:
        new_lines.append(ln)
new_lines.extend(lines[end_idx:])
p.write_text("".join(new_lines), encoding="utf-8")
print("Re-indented.")
