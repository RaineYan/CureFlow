from experiments.day14 import verify_frozen_evidence_unchanged
r = verify_frozen_evidence_unchanged()
print("Day 13 frozen evidence:")
print("  passed:", r["passed"])
print("  checked:", len(r["checked_files"]), "files")
if not r["passed"]:
    print("  mismatches:", r["mismatches"])
    print("  missing:", r["missing"])
