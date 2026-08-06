from __future__ import annotations

import json
import sqlite3


def test_feature_readiness_distinguishes_evidence_volume_from_coverage(tmp_path):
    from kreports.quality.feature_readiness import evaluate_feature_readiness

    database = tmp_path / "runtime.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE audit_procedure_items (id INTEGER)")
        connection.execute("CREATE TABLE report_sections (id INTEGER)")
        connection.execute("INSERT INTO audit_procedure_items VALUES (1)")
        connection.execute("INSERT INTO report_sections VALUES (1)")
    manifest = database.with_suffix(".db.release.json")
    manifest.write_text(
        json.dumps(
            {
                "dataset": {"version": "test-v1"},
                "release_gate": {
                    "blockers": ["audit_procedure_coverage"],
                    "feature_coverage": {
                        "audit_procedure": {
                            "coverage_pct": 0.0,
                            "threshold_pct": 95.0,
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_feature_readiness(database)

    procedure = report["features"]["kam_and_audit_procedure"]
    assert procedure["status"] == "limited"
    assert procedure["evidence_counts"]["audit_procedure_items"] == 1
    assert procedure["coverage"]["coverage_pct"] == 0.0
