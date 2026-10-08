"""Day 13.6 FORENSIC: redirect all test calls of
`_save_test_a_state` and `_save_test_b_state` to use
`tmp_path` instead of the real `experiments/results/`
state files.  This prevents tests from polluting the
real A/B state files with mock fixture data.
"""
import re
from pathlib import Path

p = Path("tests/test_day13_6_staged_live_gate.py")
text = p.read_text(encoding="utf-8")

# Find all call sites and update them
# Pattern: _save_test_a_state(test_a, ledger, fresh_store)
#          _save_test_a_state(test_a, ledger, run_store)
#          _save_test_a_state(test_a, a_ledger, run_store)
# Replace with: _save_test_a_state(test_a, ledger, fresh_store, state_path=str(tmp_path / "test_a_state.json"))

n_a = 0

# Pattern 1: _save_test_a_state(test_a, ledger, fresh_store)
new_text, c1 = re.subn(
    r"_save_test_a_state\(test_a, ledger, fresh_store\)",
    "_save_test_a_state(test_a, ledger, fresh_store, state_path=str(tmp_path / \"test_a_state.json\"))",
    text,
)
n_a += c1
text = new_text

# Pattern 2: _save_test_a_state(test_a, ledger, run_store)
new_text, c2 = re.subn(
    r"_save_test_a_state\(test_a, ledger, run_store\)",
    "_save_test_a_state(test_a, ledger, run_store, state_path=str(tmp_path / \"test_a_state.json\"))",
    text,
)
n_a += c2
text = new_text

# Pattern 3: _save_test_a_state(test_a, a_ledger, run_store)
new_text, c3 = re.subn(
    r"_save_test_a_state\(test_a, a_ledger, run_store\)",
    "_save_test_a_state(test_a, a_ledger, run_store, state_path=str(tmp_path / \"test_a_state.json\"))",
    text,
)
n_a += c3
text = new_text

# Pattern 4: _save_test_b_state(test_b, b_ledger, test_a_state_path=TEST_A_STATE_PATH)
# (no change; the test_a_state_path arg is fine)
# But the b state writes to TEST_B_STATE_PATH by default.
# We need to also pass state_path to it.

n_b = 0
new_text, c4 = re.subn(
    r"_save_test_b_state\(test_b, b_ledger,\s+test_a_state_path=TEST_A_STATE_PATH\)",
    "_save_test_b_state(test_b, b_ledger, test_a_state_path=str(tmp_path / \"test_a_state.json\"), state_path=str(tmp_path / \"test_b_state.json\"))",
    text,
)
n_b += c4
text = new_text

new_text, c5 = re.subn(
    r"_save_test_b_state\(test_b, b_ledger,\s+test_a_state_path=fake_a_state\)",
    "_save_test_b_state(test_b, b_ledger, test_a_state_path=fake_a_state, state_path=str(tmp_path / \"test_b_state.json\"))",
    text,
)
n_b += c5
text = new_text

p.write_text(text, encoding="utf-8")
print(f"Replaced {n_a} _save_test_a_state calls")
print(f"Replaced {n_b} _save_test_b_state calls")
