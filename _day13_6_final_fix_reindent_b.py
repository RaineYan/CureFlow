"""Day 13.6 FINAL-FIX: re-indent run_test_b body inside the
tempfile.TemporaryDirectory block.  The with line is at line 595;
the body (lines 596..759) is currently at 4-space indent; it
should be at 8-space indent to live inside the with block.
"""
from pathlib import Path

p = Path("live_gate_run.py")
text = p.read_text(encoding="utf-8")
lines = text.splitlines(keepends=True)

# The with line is at 595 (1-indexed) = lines[594] (0-indexed).
# Body starts at 596 = lines[595].
# End: line 759 (last line of run_test_b, right before "# =====" section).
# We can detect end by the next line that is "# ==" or def run_test_c.
start = 595
end = None
for k in range(start, len(lines)):
    if lines[k].startswith("# ====") or lines[k].startswith("def run_test_c"):
        end = k
        break
if end is None:
    raise SystemExit("End of run_test_b not found")

print(f"Re-indenting lines {start+1}..{end} (1-indexed)")
new_lines = lines[:start]
for ln in lines[start:end]:
    if ln.startswith("    "):
        new_lines.append("    " + ln)
    elif ln.strip() == "":
        new_lines.append(ln)
    else:
        new_lines.append(ln)
new_lines.extend(lines[end:])
p.write_text("".join(new_lines), encoding="utf-8")
print("Re-indented run_test_b.")
