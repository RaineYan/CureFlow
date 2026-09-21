"""One-shot: revert the inline v1 INVALIDATED write in
inject_field_value_tamper.  We will instead patch
propagate_contamination to be cross-version-aware.
"""
from pathlib import Path

PATH = Path("experiments/fault_injection.py")
text = PATH.read_text(encoding="utf-8")

new = (
    "    # Day 13.6 FINAL follow-up is in propagate_contamination:\n"
    "    # when the seed is an INVALIDATED version v_n+1 of a\n"
    "    # tool artifact (e.g. field_revenue_2025@v2), also\n"
    "    # treat v_n (and any earlier content version) as a\n"
    "    # contamination source so the BFS can reach\n"
    "    # downstream nodes whose input_artifact_ids still\n"
    "    # reference the older version.  This keeps the Day 7\n"
    "    # invariant that the baseline v_n value is NOT\n"
    "    overwritten and Day 9/10 experiments keep their\n"
    "    `v1 remains VALID after baseline` assumption.\n"
    "    return saved, record\n"
)

old = (
    "    # Day 13.6 FINAL follow-up: also mark the baseline\n"
    "    # (original v_n) version INVALIDATED so propagation BFS\n"
    "    # can walk downstream nodes whose input_artifact_ids\n"
    "    # reference the older version.  Without this, BFS from\n"
    "    # the new INVALIDATED v_n+1 finds no downstream (the\n"
    "    # baseline metric/claim/section still cite v_n), so\n"
    "    # contamination never reaches them and the recovery\n"
    "    # plan is empty.  We must update the SPECIFIC v_n\n"
    "    # status, not update_status (which only targets the\n"
    "    # latest content version = v_n+1).\n"
    "    from artifacts import ArtifactStatus\n"
    "    v1_artifact = store.get_version(\n"
    "        run_id, artifact_id, original.version,\n"
    "    )\n"
    "    if v1_artifact is not None:\n"
    "        v1_updated = v1_artifact.model_copy(\n"
    "            update={\"validation_status\":\n"
    "                    ArtifactStatus.INVALIDATED.value},\n"
    "            deep=True,\n"
    "        )\n"
    "        v1_path = store._path_for(\n"
    "            run_id, artifact_id, original.version,\n"
    "        )\n"
    "        with open(v1_path, \"w\", encoding=\"utf-8\") as f:\n"
    "            import json as _json\n"
    "            _json.dump(\n"
    "                v1_updated.to_dict(), f,\n"
    "                ensure_ascii=False, indent=2,\n"
    "            )\n"
    "    return saved, record\n"
)

if old not in text:
    raise SystemExit("old block not found; already reverted?")
text = text.replace(old, new)
PATH.write_text(text, encoding="utf-8")
print("OK")
