"""Tests for the OSCAL comparison script. Run with:  python -m pytest -v tests/test_compare_oscal.py

Small hand-built catalogs and results cover the four cases in the instructions (PASS, FAIL,
missing result, unknown result) and the error cases. The last test runs the real repo files and
works out the expected counts from the raw results, not from the script.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "comparison"))
import compare_oscal  # noqa: E402


def label(cid):
    return cid.upper().replace("V", "v")


def control(cid, title, obligation="SHALL"):
    return {"id": cid, "title": title,
            "props": [{"name": "obligation", "value": obligation}, {"name": "label", "value": label(cid)}],
            "parts": [{"id": cid + "_smt", "name": "statement", "prose": f"{title} {obligation}."},
                      {"id": cid + "_rem", "name": "remediation", "prose": f"Fix {title}."}]}


def catalog(*controls):
    return {"catalog": {"uuid": "c", "metadata": {"title": "t"},
                        "groups": [{"id": "g1", "title": "Group One", "controls": list(controls)}]}}


def results(*entries, policy_ids=None):
    """entries: (control id, OSCAL state, ScubaGear word)."""
    findings, observations = [], []
    for i, (cid, state, word) in enumerate(entries):
        obs_uuid = f"obs-{i}"
        observations.append({"uuid": obs_uuid, "description": "d", "methods": ["TEST"],
                             "props": [{"name": "scuba-policy-id", "value": (policy_ids or {}).get(cid, label(cid))},
                                       {"name": "scuba-result", "value": word}],
                             "relevant-evidence": [{"description": f"evidence for {cid}"}],
                             "collected": "2026-05-04T17:15:48+00:00"})
        findings.append({"uuid": f"f-{i}", "title": cid, "description": f"details for {cid}",
                         "target": {"type": "statement-id", "target-id": cid + "_smt", "status": {"state": state}},
                         "related-observations": [{"observation-uuid": obs_uuid}]})
    return {"assessment-results": {"uuid": "a", "metadata": {"title": "t"},
                                   "results": [{"uuid": "r", "start": "2026-05-04T17:15:48+00:00",
                                                "props": [{"name": "tenant-name", "value": "contoso"}],
                                                "observations": observations, "findings": findings}]}}


def write(path, data):
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
    return path


def compare(tmp_path, cat, res):
    out = tmp_path / "findings.json"
    code = compare_oscal.main(["--scuba", str(write(tmp_path / "cat.json", cat)),
                               "--scubagear", str(write(tmp_path / "ar.json", res)), "--output", str(out)])
    return code, (json.loads(out.read_text(encoding="utf-8")) if out.exists() else None)


def test_pass_creates_no_finding(tmp_path):
    code, doc = compare(tmp_path, catalog(control("ms.aad.1.1v1", "Block legacy")),
                        results(("ms.aad.1.1v1", "satisfied", "Pass")))
    assert code == 0
    assert doc["findings"] == []
    assert doc["assessments"][0]["status"] == "PASS"
    assert doc["summary"] == {"total_controls": 1, "passed": 1, "failed": 0, "warnings": 0,
                              "not_assessed": 0, "findings": 0, "unmatched_results": 0}


def test_fail_creates_one_finding_with_requirement_and_evidence(tmp_path):
    code, doc = compare(tmp_path, catalog(control("ms.aad.7.4v1", "No permanent roles", "SHALL NOT")),
                        results(("ms.aad.7.4v1", "not-satisfied", "Fail")))
    assert code == 0
    [f] = doc["findings"]
    assert f["control_id"] == "MS.AAD.7.4v1"
    assert f["status"] == "FAIL"
    assert f["scubagear_result"] == "Fail"
    assert f["requirement"] == "No permanent roles SHALL NOT."
    assert f["finding"] == "details for ms.aad.7.4v1"
    assert f["evidence"] == ["evidence for ms.aad.7.4v1"]
    assert f["remediation_guidance"] == "Fix No permanent roles."
    assert f["source"] == {"scuba": "cat.json", "scubagear": "ar.json"}
    assert "priority" not in f and "risk" not in f  # no invented analysis


def test_warning_keeps_scubagear_word_and_is_a_finding(tmp_path):
    code, doc = compare(tmp_path, catalog(control("ms.aad.3.8v1", "Managed devices", "SHOULD")),
                        results(("ms.aad.3.8v1", "not-satisfied", "Warning")))
    assert code == 0
    [f] = doc["findings"]
    assert f["status"] == "WARNING"
    assert f["scubagear_result"] == "Warning"
    assert doc["summary"]["warnings"] == 1 and doc["summary"]["failed"] == 0


def test_missing_result_is_not_assessed(tmp_path):
    code, doc = compare(tmp_path, catalog(control("ms.aad.1.1v1", "A"), control("ms.aad.2.1v1", "B")),
                        results(("ms.aad.1.1v1", "satisfied", "Pass")))
    assert code == 0
    assert doc["assessments"][1]["status"] == "NOT_ASSESSED"
    assert doc["assessments"][1]["scubagear_result"] is None
    assert doc["summary"]["not_assessed"] == 1


def test_unknown_result_is_reported_not_dropped(tmp_path, capsys):
    code, doc = compare(tmp_path, catalog(control("ms.aad.1.1v1", "A")),
                        results(("ms.aad.1.1v1", "satisfied", "Pass"), ("ms.aad.9.9v1", "not-satisfied", "Fail")))
    assert code == 0
    assert [r["control_id"] for r in doc["unmatched_results"]] == ["ms.aad.9.9v1"]
    assert doc["summary"]["unmatched_results"] == 1
    assert "Unmatched ScubaGear controls: 1" in capsys.readouterr().out


def test_folder_of_catalogs(tmp_path):
    folder = tmp_path / "Controls"
    folder.mkdir()
    write(folder / "a.json", catalog(control("ms.aad.1.1v1", "A")))
    write(folder / "b.json", catalog(control("ms.aad.2.1v1", "B")))
    out = tmp_path / "findings.json"
    code = compare_oscal.main(["--scuba", str(folder), "--output", str(out), "--scubagear",
                               str(write(tmp_path / "ar.json", results(("ms.aad.2.1v1", "not-satisfied", "Fail"))))])
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0
    assert doc["metadata"]["scuba_sources"] == ["a.json", "b.json"]
    assert [f["source"]["scuba"] for f in doc["findings"]] == ["b.json"]


@pytest.mark.parametrize("cat, res, message", [
    ("{not json", results(), "not valid JSON"),
    ({"something": {}}, results(), "not an OSCAL catalog"),
    (catalog(control("ms.aad.1.1v1", "A")), {"catalog": {}}, "not OSCAL assessment results"),
    (catalog(control("ms.aad.1.1v1", "A"), control("ms.aad.1.1v1", "A again")), results(), "duplicate control id"),
    (catalog({"title": "no id"}), results(), "has no id"),
    (catalog(control("ms.aad.1.1v1", "A")),
     results(("ms.aad.1.1v1", "satisfied", "Pass"), ("ms.aad.1.1v1", "satisfied", "Pass")), "more than one result"),
    (catalog(control("ms.aad.1.1v1", "A")), results(("ms.aad.1.1v1", "other", "Pass")), "unexpected finding state"),
    (catalog(control("ms.aad.1.1v1", "A")),
     results(("ms.aad.1.1v1", "satisfied", "Pass"), policy_ids={"ms.aad.1.1v1": "MS.AAD.2.1v1"}), "observation is for"),
])
def test_bad_input_fails_clearly(tmp_path, capsys, cat, res, message):
    code, _ = compare(tmp_path, cat, res)
    assert code == 1
    assert message in capsys.readouterr().err


def test_result_without_target_id_fails(tmp_path, capsys):
    res = results(("ms.aad.1.1v1", "satisfied", "Pass"))
    res["assessment-results"]["results"][0]["findings"][0]["target"]["target-id"] = ""
    code, _ = compare(tmp_path, catalog(control("ms.aad.1.1v1", "A")), res)
    assert code == 1
    assert "has no target-id" in capsys.readouterr().err


def test_missing_file_fails(tmp_path, capsys):
    code = compare_oscal.main(["--scuba", str(tmp_path / "nope.json"), "--scubagear", str(tmp_path / "x.json"),
                               "--output", str(tmp_path / "out.json")])
    assert code == 1
    assert "file not found" in capsys.readouterr().err


def test_real_repo_files(tmp_path):
    ar_path = ROOT / "oscal" / "assessment-results.json"
    scan = json.loads(ar_path.read_text(encoding="utf-8-sig"))["assessment-results"]["results"][0]
    raw = scan["findings"]
    words = [p["value"] for o in scan["observations"] for p in o["props"] if p["name"] == "scuba-result"]

    out = tmp_path / "findings.json"
    assert compare_oscal.main(["--scuba", str(ROOT / "oscal" / "Controls"), "--scubagear", str(ar_path),
                               "--output", str(out)]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    s = doc["summary"]
    assert s["passed"] == words.count("Pass")
    assert s["failed"] == words.count("Fail")
    assert s["warnings"] == words.count("Warning")
    assert len(doc["findings"]) == s["failed"] + s["warnings"]
    assert s["not_assessed"] == s["total_controls"] - len(raw)
    assert s["unmatched_results"] == 0


def test_assessments_carry_the_oscal_uuids_they_came_from(tmp_path):
    code, doc = compare(tmp_path, catalog(control("ms.aad.1.1v1", "Block legacy"), control("ms.aad.2.1v1", "Risky users")),
                        results(("ms.aad.1.1v1", "not-satisfied", "Fail")))
    assert code == 0
    assessed, missing = doc["assessments"]
    assert assessed["oscal"] == {"finding_uuid": "f-0", "observation_uuids": ["obs-0"], "risk_uuids": []}
    assert missing["oscal"] == {"finding_uuid": None, "observation_uuids": [], "risk_uuids": []}
    assert doc["metadata"]["scan"]["assessment_results_uuid"] == "a"
