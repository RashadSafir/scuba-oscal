"""Tests for the assessment-results transformer. Run with:  python -m pytest -v

The expected verdicts are worked out here straight from the raw scan file, not by
calling the script's own logic, so the tests can catch the script being wrong.
"""
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import make_assessment_results as mar  # noqa: E402  (only used for the default file locations)

RESULTS = mar.default_results()
CATALOG = mar.default_catalog()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


FIXED_TIME = "2026-01-15T10:30:00+00:00"


def build(out, scan_time=FIXED_TIME, results=None, extra=()):
    """Writes assessment-results (at `out`) and assessment-plan.json (next to it)."""
    cmd = [sys.executable, str(HERE / "make_assessment_results.py"),
           "--results", str(results or RESULTS), "--catalog", str(CATALOG), "--out", str(out), *extra]
    if scan_time:
        cmd += ["--scan-time", scan_time]
    subprocess.run(cmd, check=True, capture_output=True, cwd=HERE)


def plan_of(out):
    return read(Path(out).parent / "assessment-plan.json")["assessment-plan"]


def trestle_validate(tmp_path, source, folder, filename):
    # trestle only validates files inside its own workspace layout, so copy the
    # file into a throwaway workspace and validate it there
    subprocess.run([sys.executable, "-m", "trestle", "init"], cwd=tmp_path, check=True, capture_output=True)
    target = tmp_path / folder / "scuba-aad" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(source, target)
    return subprocess.run([sys.executable, "-m", "trestle", "validate", "-f", str(target)],
                          cwd=tmp_path, capture_output=True, text=True)


def all_uuids(path):
    doc = read(path)["assessment-results"]
    r = doc["results"][0]
    return [doc["uuid"], r["uuid"]] + [x["uuid"] for k in ("observations", "findings", "risks")
                                     for x in r.get(k, [])]


@pytest.fixture(scope="module")
def out_file(tmp_path_factory):
    out = tmp_path_factory.mktemp("ar") / "assessment-results.json"
    build(out)
    return out


@pytest.fixture(scope="module")
def result(out_file):
    return read(out_file)["assessment-results"]["results"][0]


def catalog_controls():
    cat = read(CATALOG)["catalog"]
    return {c["id"]: c for g in cat["groups"] for c in g["controls"]}


def scan_records(path=RESULTS):
    """control id lowercased -> ScubaGear control record, straight from the results report."""
    doc = read(path)
    return {c["Control ID"].lower(): c for groups in doc["Results"].values()
            for g in groups for c in g["Controls"]}


def expected_verdicts():
    """control id -> True (met) / False, from the raw report, for evaluated catalog controls only.
    Pass is met; Fail and Warning (ScubaGear's failed SHOULD) are not; N/A, Error, Omitted are skipped."""
    scan = scan_records()
    out = {}
    for cid in catalog_controls():
        result = scan[cid]["Result"]
        if result in ("Pass", "Fail", "Warning"):
            out[cid] = result == "Pass"
    return out


def test_output_is_valid_oscal(out_file, tmp_path):
    r = trestle_validate(tmp_path, out_file, "assessment-results", "assessment-results.json")
    assert r.returncode == 0 and "VALID" in r.stdout, r.stdout + r.stderr


def test_plan_is_valid_oscal(out_file, tmp_path):
    r = trestle_validate(tmp_path, out_file.parent / "assessment-plan.json", "assessment-plans",
                         "assessment-plan.json")
    assert r.returncode == 0 and "VALID" in r.stdout, r.stdout + r.stderr


def test_plan_scope_matches_the_catalog(out_file):
    plan = plan_of(out_file)
    planned = {c["control-id"]: c["statement-ids"] for sel in plan["reviewed-controls"]["control-selections"]
               for c in sel["include-controls"]}
    assert planned == {cid: [f"{cid}_smt"] for cid in catalog_controls()}


def test_results_import_ap_points_at_the_generated_plan(out_file):
    href = read(out_file)["assessment-results"]["import-ap"]["href"]
    target = out_file.parent / href                       # the link is relative to the results file
    assert target.exists(), f"import-ap points at a file that does not exist: {href}"
    assert "assessment-plan" in read(target)


def test_plan_ssp_link_defaults_to_the_declared_placeholder_and_can_be_overridden(tmp_path):
    a, b = tmp_path / "a" / "assessment-results.json", tmp_path / "b" / "assessment-results.json"
    build(a)
    build(b, extra=["--ssp-href", "https://example.org/real-ssp.json"])
    assert plan_of(a)["import-ssp"]["href"] == "./system-security-plan.json"
    assert "placeholder" in plan_of(a)["import-ssp"]["remarks"].lower()
    assert plan_of(b)["import-ssp"]["href"] == "https://example.org/real-ssp.json"


def test_plan_ids_follow_the_scope_not_the_scan(tmp_path):
    a, b = tmp_path / "a" / "assessment-results.json", tmp_path / "b" / "assessment-results.json"
    build(a)
    build(b, scan_time="2026-02-01T00:00:00+00:00")       # a different scan of the same scope
    assert plan_of(a)["uuid"] == plan_of(b)["uuid"], "the plan changed just because a new scan was run"


def test_one_finding_per_catalog_control_with_the_right_verdict(result):
    by_target = {f["target"]["target-id"]: f["target"]["status"]["state"] for f in result["findings"]}
    expected = {f"{cid}_smt": ("satisfied" if met else "not-satisfied")
                for cid, met in expected_verdicts().items()}
    assert by_target == expected
    assert len(result["findings"]) == len(expected_verdicts())


def test_every_finding_points_at_a_real_catalog_statement_part(result):
    # the most important test: no finding may point at nothing
    statement_ids = {p["id"] for c in catalog_controls().values() for p in c["parts"] if p["name"] == "statement"}
    for f in result["findings"]:
        assert f["target"]["type"] == "statement-id"
        assert f["target"]["target-id"] in statement_ids, f"dangling target-id: {f['target']['target-id']}"


def test_reviewed_controls_match_the_catalog(result):
    included = {c["control-id"] for sel in result["reviewed-controls"]["control-selections"]
                for c in sel["include-controls"]}
    assert included == set(expected_verdicts())      # the evaluated catalog controls


def test_every_uuid_link_resolves(result):
    obs = {o["uuid"] for o in result["observations"]}
    risks = {r["uuid"] for r in result.get("risks", [])}
    for f in result["findings"]:
        assert f["related-observations"], f"{f['title']} has no observation"
        for ro in f["related-observations"]:
            assert ro["observation-uuid"] in obs
        for rr in f.get("related-risks", []):
            assert rr["risk-uuid"] in risks
    for r in result.get("risks", []):
        for ro in r["related-observations"]:
            assert ro["observation-uuid"] in obs


def test_only_failures_get_a_risk(result):
    risk_ids = {rr["risk-uuid"] for f in result["findings"] for rr in f.get("related-risks", [])}
    failed = [f for f in result["findings"] if f["target"]["status"]["state"] == "not-satisfied"]
    passed = [f for f in result["findings"] if f["target"]["status"]["state"] == "satisfied"]
    assert all(f.get("related-risks") for f in failed)
    assert not any(f.get("related-risks") for f in passed)
    assert len(result.get("risks", [])) == len(failed) == len(risk_ids)


def test_uuids_are_unique(out_file):
    ids = all_uuids(out_file)
    assert len(ids) == len(set(ids))


def test_html_is_stripped_from_descriptions(result):
    for o in result["observations"]:
        assert "<" not in o["description"] and ">" not in o["description"]


def test_scan_time_is_used_and_no_end_time_is_invented(result):
    assert result["start"] == FIXED_TIME
    assert all(o["collected"] == FIXED_TIME for o in result["observations"])
    assert "end" not in result


def test_default_scan_time_comes_from_the_report(tmp_path):
    out = tmp_path / "default.json"
    build(out, scan_time=None)
    stamp = read(RESULTS)["MetaData"]["TimestampZulu"]
    expected = datetime.fromisoformat(stamp.replace("Z", "+00:00")).replace(microsecond=0)
    r = read(out)["assessment-results"]["results"][0]
    assert datetime.fromisoformat(r["start"]) == expected
    assert all(datetime.fromisoformat(o["collected"]) == expected for o in r["observations"])


def test_tenant_and_report_are_recorded(result):
    meta = read(RESULTS)["MetaData"]
    props = {p["name"]: p["value"] for p in result["props"]}
    assert props["tenant-id"] == meta["TenantId"]
    assert props["tenant-domain"] == meta["DomainName"]
    assert props["scuba-tool-version"] == meta["ToolVersion"]
    assert props["scuba-report-uuid"] == meta["ReportUUID"]


def with_results(tmp_path, changes):
    """A copy of the report with {control id: new Result} applied."""
    doc = read(RESULTS)
    for groups in doc["Results"].values():
        for g in groups:
            for c in g["Controls"]:
                if c["Control ID"] in changes:
                    c["Result"] = changes[c["Control ID"]]
    path = tmp_path / "scuba_results_edited.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_warning_is_a_failure_and_unevaluated_results_are_skipped(tmp_path):
    scan = with_results(tmp_path, {"MS.AAD.1.1v1": "Warning", "MS.AAD.2.1v1": "N/A",
                                   "MS.AAD.2.3v1": "Error", "MS.AAD.7.1v1": "Omitted"})
    out = tmp_path / "ar.json"
    build(out, results=scan)
    r = read(out)["assessment-results"]["results"][0]
    states = {f["target"]["target-id"]: f["target"]["status"]["state"] for f in r["findings"]}
    assert states["ms.aad.1.1v1_smt"] == "not-satisfied"
    for skipped in ("ms.aad.2.1v1_smt", "ms.aad.2.3v1_smt", "ms.aad.7.1v1_smt"):
        assert skipped not in states, f"{skipped} was not evaluated but got a verdict"
    reviewed = {c["control-id"] for s in r["reviewed-controls"]["control-selections"] for c in s["include-controls"]}
    assert "ms.aad.2.1v1" not in reviewed


def test_an_unknown_result_value_is_refused(tmp_path):
    scan = with_results(tmp_path, {"MS.AAD.1.1v1": "Maybe"})
    out = tmp_path / "ar.json"
    r = subprocess.run([sys.executable, str(HERE / "make_assessment_results.py"), "--results", str(scan),
                        "--catalog", str(CATALOG), "--out", str(out)], capture_output=True, text=True, cwd=HERE)
    assert r.returncode != 0 and "unknown ScubaGear Result" in r.stderr
    assert not out.exists()


def test_a_file_in_the_wrong_format_is_refused(tmp_path):
    wrong = tmp_path / "TestResults.json"
    wrong.write_text(json.dumps([{"PolicyId": "MS.AAD.1.1v1", "RequirementMet": True}]), encoding="utf-8")
    r = subprocess.run([sys.executable, str(HERE / "make_assessment_results.py"), "--results", str(wrong),
                        "--catalog", str(CATALOG), "--out", str(tmp_path / "ar.json")],
                       capture_output=True, text=True, cwd=HERE)
    assert r.returncode != 0 and "not a ScubaGear results report" in r.stderr


def test_same_scan_and_time_reproduce_the_same_file(tmp_path):
    # only last-modified (the moment of generation) is allowed to differ, in both files
    docs = []
    for name in ("a", "b"):
        out = tmp_path / name / "assessment-results.json"
        build(out)
        results, plan = read(out), read(out.parent / "assessment-plan.json")
        results["assessment-results"]["metadata"].pop("last-modified")
        plan["assessment-plan"]["metadata"].pop("last-modified")
        docs.append((results, plan))
    assert docs[0] == docs[1], "same scan + same time gave different output - ids are not stable"


def test_a_different_scan_never_shares_ids(tmp_path):
    a, b, c = tmp_path / "a.json", tmp_path / "b.json", tmp_path / "c.json"
    build(a)
    build(b, scan_time="2026-02-01T00:00:00+00:00")           # same file, different scan time
    other = tmp_path / "TestResults.json"                      # different file content -> different scan
    other.write_bytes(RESULTS.read_bytes() + b"\n")
    build(c, results=other)
    ids = [set(all_uuids(p)) for p in (a, b, c)]
    assert not (ids[0] & ids[1]) and not (ids[0] & ids[2]), "different scans reused uuids"
