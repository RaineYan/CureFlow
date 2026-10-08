"""One-shot script to update fault_injection.py to also mark the
baseline (v_n) version INVALIDATED, by writing the updated
artifact's JSON directly to its on-disk path.

This is needed because `update_status` in ArtifactStore only
operates on the latest content version.  When
`inject_field_value_tamper` creates v_n+1, the baseline v_n
must also be marked INVALIDATED so the propagation BFS can
find downstream nodes that reference v_n in their
`input_artifact_ids`.
"""
import re
from pathlib import Path


PATH = Path("experiments/fault_injection.py")
text = PATH.read_text(encoding="utf-8")

# The previous (broken) version we want to replace.
old = (
    "    # Day 13.6 FINAL follow-up: also mark the baseline\n"
    "    # (original) version INVALIDATED so propagation BFS can\n"
    "    # walk downstream nodes whose `input_artifact_ids`\n"
    "    # reference the older version.  Without this, BFS from\n"
    "    # the new INVALIDATED v_n+1 finds no downstream (the\n"
    "    # baseline metric/claim/section still cite v_n), so\n"
    "    # contamination never reaches them and the recovery\n"
    "    # plan is empty.  `update_status` does NOT increment\n"
    "    # version (Day 4 invariant).\n"
    "    from artifacts import ArtifactStatus\n"
    "    store.update_status(\n"
    "        run_id, artifact_id, ArtifactStatus.INVALIDATED,\n"
    "    )\n"
)

new = (
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
)

if old not in text:
    raise SystemExit(
        "old block not found in fault_injection.py; "
        "file may have been edited already."
    )
text = text.replace(old, new)
PATH.write_text(text, encoding="utf-8")
print("OK")
