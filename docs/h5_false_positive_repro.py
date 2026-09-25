"""Offline characterization of the current H5 false positive; not a product test.

Run from the repository root: python -m docs.h5_false_positive_repro
This asserts the *observed* bug so a future fix should replace this script with
a desired-behavior regression test in tests/test_claim_grounding.py.
"""

import os
from tempfile import TemporaryDirectory

from artifacts import Artifact, ArtifactRef, ArtifactStore, ArtifactType
from validation.claim_grounding import RiskCode, audit_claim_grounding


def main() -> None:
    with TemporaryDirectory(dir=os.environ.get("CUREFLOW_REPRO_TMP")) as root:
        store = ArtifactStore(root)
        metrics = [
            store.save(Artifact(
                artifact_id=name,
                artifact_type=ArtifactType.METRIC,
                producer_task="h5_repro",
                run_id="h5_repro",
                value=value,
            ))
            for name, value in (
                ("metric_revenue_growth", 0.6078),
                ("metric_profit_margin", 0.4756),
            )
        ]
        refs = [
            ArtifactRef(run_id=row.run_id, artifact_id=row.artifact_id, version=row.version)
            for row in metrics
        ]
        findings = {}
        for label, content in (
            ("baseline", "营收同比增长60.78%，利润率达到47.56%。"),
            ("neutral", "本分析记录了 recommendation system 的使用情况；营收同比增长60.78%，利润率达到47.56%。"),
        ):
            claim = store.save(Artifact(
                artifact_id=f"claim_{label}",
                artifact_type=ArtifactType.CLAIM,
                producer_task="h5_repro",
                run_id="h5_repro",
                content=content,
                evidence_ids=refs,
            ))
            audit = audit_claim_grounding(claim=claim, domain="financial", store=store)
            findings[label] = audit
            print(label, audit.verdict.value, [code.value for code in audit.risk_codes], audit.issues)

        assert RiskCode.H5_SCOPE_EXPANSION not in findings["baseline"].risk_codes
        assert RiskCode.H5_SCOPE_EXPANSION in findings["neutral"].risk_codes
        assert findings["neutral"].issues == ["recommendation token found: 'recommend'"]
        print("REPRODUCED: neutral mention of recommendation system is marked H5")


if __name__ == "__main__":
    main()
