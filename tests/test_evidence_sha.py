import hashlib
import json

from scripts.verify_evidence_sha import verify


def test_sha_verifier_detects_a_changed_file(tmp_path):
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("original", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"sample": {"file_count": 1, "shas": {"evidence.txt": hashlib.sha256(evidence.read_bytes()).hexdigest()}}}), encoding="utf-8")
    assert verify(manifest, tmp_path) == (1, [])
    evidence.write_text("changed", encoding="utf-8")
    count, errors = verify(manifest, tmp_path)
    assert count == 1 and "SHA mismatch" in errors[0]
