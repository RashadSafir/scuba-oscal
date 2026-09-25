"""Tests for the Fix first scoring (app/fix_first.py) and the scan comparison (app/compare_scans.py).
Run with:  python -m pytest -v tests/test_fix_first_and_changes.py
"""
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "app"), str(ROOT / "pipeline")]
import compare_scans  # noqa: E402
import fix_first as ff  # noqa: E402
from ai import findings as fm  # noqa: E402

CFG = ff.load_config()


def finding(cid="MS.AAD.9.9v1", title="Something", group="Area", obligation="SHALL", status="FAIL", nist=(),
            scuba_result="Fail"):
    return fm.Finding(control_id=cid, title=title, group=group, obligation=obligation, status=status,
                      priority=fm.PRIORITY.get(obligation) if status == "FAIL" else None, requirement="r",
                      rationale="", finding="", evidence="", scuba_result=scuba_result, remediation="",
                      nist=list(nist), oscal_control_id=cid.lower())


def test_required_always_outranks_recommended():
    required = ff.score(finding(obligation="SHALL"), CFG)
    recommended = ff.score(finding(obligation="SHOULD", title="Privileged admin role MFA",
                                   nist=["NIST SP 800-53 Rev 5 AC-6"]), CFG)
    assert required.points > recommended.points


def test_the_reason_names_every_factor_that_scored():
    s = ff.score(finding(title="Phishing-resistant MFA for privileged roles", nist=["NIST SP 800-53 Rev 5 IA-2(1)"]), CFG)
    assert s.reason == "Required (SHALL), privileged access, 800-53 IA"
    assert s.points == CFG["obligation"]["SHALL"] + 30 + CFG["family"]["IA"]


def test_related_controls_become_one_item_ranked_by_the_best():
    a = finding("MS.AAD.3.1v1", "Phishing-resistant MFA for all users", nist=["NIST SP 800-53 Rev 5 IA-2(1)"])
    b = finding("MS.AAD.3.6v1", "Phishing-resistant MFA for privileged roles", nist=["NIST SP 800-53 Rev 5 IA-2(1)"])
    c = finding("MS.EXO.13.1v1", "Mailbox auditing", nist=["NIST SP 800-53 Rev 5 AU-12c"])
    items, _ = ff.fix_first([a, b, c], CFG)
    assert items[0].name == "Phishing-resistant MFA"
    assert [f.control_id for f in items[0].findings] == ["MS.AAD.3.6v1", "MS.AAD.3.1v1"]
    assert items[1].name is None and items[1].findings == [c]


def test_a_related_group_with_one_failure_is_just_that_control():
    items, _ = ff.fix_first([finding("MS.AAD.3.6v1", "Phishing-resistant MFA for privileged roles")], CFG)
    assert items[0].name is None


def previous_doc(results, tenant_id="t1", start="2026-09-15T14:30:12+00:00", ar_uuid=None):
    """A minimal valid assessment-results document with one finding per (control id, state)."""
    obs, finds = [], []
    for i, (cid, state) in enumerate(results.items()):
        o = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"o{i}"))
        obs.append({"uuid": o, "description": "d", "methods": ["TEST"], "collected": start})
        finds.append({"uuid": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"f{i}")), "title": cid, "description": "d",
                      "target": {"type": "statement-id", "target-id": f"{cid}_smt",
                                 "status": {"state": state}},
                      "related-observations": [{"observation-uuid": o}]})
    return {"assessment-results": {
        "uuid": ar_uuid or str(uuid.uuid5(uuid.NAMESPACE_DNS, "ar")),
        "metadata": {"title": "t", "last-modified": "2026-09-15T15:00:00+00:00", "version": "1", "oscal-version": "1.1.2"},
        "import-ap": {"href": "assessment-plan.json"},
        "results": [{"uuid": str(uuid.uuid5(uuid.NAMESPACE_DNS, "r")), "title": "r", "description": "d", "start": start,
                     "props": [{"name": "tenant-id", "value": tenant_id}],
                     "reviewed-controls": {"control-selections": [{"include-all": {}}]},
                     "observations": obs, "findings": finds}]}}


def test_each_control_is_classified_by_its_result_then_and_now():
    prev = compare_scans.read_previous(previous_doc({
        "ms.aad.1.1v1": "not-satisfied", "ms.aad.2.1v1": "satisfied", "ms.aad.3.1v1": "not-satisfied",
        "ms.aad.4.1v1": "satisfied"}))
    now = [finding("MS.AAD.1.1v1", status="PASS"), finding("MS.AAD.2.1v1"), finding("MS.AAD.3.1v1"),
           finding("MS.AAD.4.1v1", status="PASS"), finding("MS.AAD.5.1v1")]
    changed = compare_scans.compare(prev, now)
    ids = {k: [f.control_id for f in v] for k, v in changed.items()}
    assert ids == {"resolved": ["MS.AAD.1.1v1"], "regressed": ["MS.AAD.2.1v1"], "new": ["MS.AAD.5.1v1"],
                   "still failing": ["MS.AAD.3.1v1"], "unchanged": ["MS.AAD.4.1v1"]}


def test_the_proposed_closure_names_the_item_make_poam_created():
    doc = previous_doc({"ms.aad.1.1v1": "not-satisfied"})
    prev = compare_scans.read_previous(doc)
    ar_id = doc["assessment-results"]["uuid"]
    expected = str(uuid.uuid5(uuid.UUID("6f1c2d3e-0000-4000-8000-5c0ba0000002"), f"{ar_id}:poam-item:ms.aad.1.1v1"))
    assert compare_scans.previous_poam_item(prev, finding("MS.AAD.1.1v1", status="PASS")) == expected


def test_pairing_problems_are_reported():
    prev = compare_scans.read_previous(previous_doc({"ms.aad.1.1v1": "satisfied"}, tenant_id="other",
                                                    start="2026-09-30T00:00:00+00:00"))
    w = compare_scans.warnings(prev, "t1", "2026-09-24T15:02:41+00:00")
    assert len(w) == 2 and "different tenant" in w[0] and "not from an earlier scan" in w[1]


def test_a_file_that_is_not_assessment_results_is_refused():
    catalog = json.loads((ROOT / "oscal" / "profile.json").read_text(encoding="utf-8"))
    try:
        compare_scans.read_previous(catalog)
    except compare_scans.ComparisonError as e:
        assert "not an OSCAL assessment results file" in str(e)
    else:
        raise AssertionError("a profile was accepted as assessment results")


def test_the_rescan_sample_resolves_six_and_regresses_one():
    prev = compare_scans.read_previous(json.loads(
        (ROOT / "data" / "sample" / "scuba_results_sample_2-assessment-results.json").read_text(encoding="utf-8")))
    rescan = json.loads((ROOT / "data" / "sample" / "scuba_results_sample_2_rescan.json").read_text(encoding="utf-8-sig"))
    now = {c["Control ID"].lower(): c["Result"] for gs in rescan["Results"].values() for g in gs for c in g["Controls"]}
    resolved = [c for c, r in prev["results"].items() if r == "FAIL" and now.get(c) == "Pass"]
    regressed = [c for c, r in prev["results"].items() if r == "PASS" and now.get(c) in ("Fail", "Warning")]
    assert len(resolved) == 6 and regressed == ["ms.aad.7.4v1"]


def test_target_dates_go_into_the_poam_and_it_stays_valid(tmp_path):
    import subprocess
    from datetime import date
    import poam_dates
    from validate_oscal import validate
    out = tmp_path / "ar.json"
    subprocess.run([sys.executable, str(ROOT / "pipeline" / "make_assessment_results.py"), "--out", str(out)],
                   check=True, capture_output=True, cwd=ROOT / "pipeline")
    subprocess.run([sys.executable, str(ROOT / "pipeline" / "make_poam.py"), "--assessment-results", str(out),
                    "--out", str(tmp_path / "poam.json")], check=True, capture_output=True, cwd=ROOT / "pipeline")
    doc = json.loads((tmp_path / "poam.json").read_text(encoding="utf-8"))
    plan = poam_dates.poam_plan(doc)
    cid = next(iter(plan))
    assert plan[cid]["milestones"] == ["Plan the change", "Apply the change", "Verify with a ScubaGear rescan"]
    dated = poam_dates.poam_with_dates(doc, {cid: date(2026, 10, 31)})
    assert validate(dated)["valid"], validate(dated)["errors"]
    risk = next(r for r in dated["plan-of-action-and-milestones"]["risks"] if r["uuid"] in plan[cid]["risk_uuids"])
    assert risk["deadline"] == "2026-10-31T23:59:59+00:00"
    assert risk["remediations"][0]["tasks"][-1]["timing"] == {"on-date": {"date": "2026-10-31T23:59:59+00:00"}}
    assert doc != dated and "deadline" not in json.dumps(doc)   # the original is not changed
