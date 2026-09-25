"""Tests for the POA&M builder. Run with:  python -m pytest -v

Expected values are worked out here from the raw scan file and the catalog, not by calling the
script's own logic, so the tests can catch the script being wrong.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
# the scripts live in ../pipeline in the repo; fall back to this folder when everything sits together
PIPELINE = HERE.parent / "pipeline" if (HERE.parent / "pipeline" / "make_assessment_results.py").exists() else HERE
sys.path.insert(0, str(PIPELINE))
import make_assessment_results as mar  # noqa: E402  (only used for the default file locations)

RESULTS = mar.default_results()
CATALOG = mar.default_catalog()
ROOT_KEY = "plan-of-action-and-milestones"
FIXED_TIME = "2026-01-15T10:30:00+00:00"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def run(script, *args):
    return subprocess.run([sys.executable, str(PIPELINE / script), *map(str, args)],
                          capture_output=True, text=True, cwd=PIPELINE)


def make_results(folder, scan=None):
    """Run the results builder into `folder`; returns the assessment-results.json path."""
    out = Path(folder) / "assessment-results.json"
    r = run("make_assessment_results.py", "--results", scan or RESULTS, "--catalog", CATALOG,
            "--out", out, "--scan-time", FIXED_TIME)
    assert r.returncode == 0, r.stderr
    return out


def make_poam(results, out=None):
    out = out or Path(results).parent / "poam.json"
    return run("make_poam.py", "--assessment-results", results, "--catalog", CATALOG, "--out", out), out


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    folder = tmp_path_factory.mktemp("poam")
    results = make_results(folder)
    proc, out = make_poam(results)
    assert proc.returncode == 0, proc.stderr
    return dict(folder=folder, results=results, out=out, poam=read(out)[ROOT_KEY],
                ar=read(results)["assessment-results"]["results"][0])


def catalog_controls():
    cat = read(CATALOG)["catalog"]
    return {c["id"]: c for g in cat["groups"] for c in g["controls"]}


def part(control, name):
    return next(p for p in control["parts"] if p["name"] == name)


def report_controls(doc):
    return [c for groups in doc["Results"].values() for g in groups for c in g["Controls"]]


def failing_policy_ids():
    """Policy ids (catalog scope only) that the raw report says failed: Fail, or Warning (a failed SHOULD)."""
    scope = set(catalog_controls())
    return {c["Control ID"] for c in report_controls(read(RESULTS))
            if c["Control ID"].lower() in scope and c["Result"] in ("Fail", "Warning")}


def test_poam_is_valid_oscal(built, tmp_path):
    # trestle only validates files inside its own workspace layout, and it works out the document
    # type from the file name, so the scratch copy has to be called plan-of-action-and-milestones.json
    subprocess.run([sys.executable, "-m", "trestle", "init"], cwd=tmp_path, check=True, capture_output=True)
    target = tmp_path / ROOT_KEY / "scuba-aad" / f"{ROOT_KEY}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(built["out"], target)
    r = subprocess.run([sys.executable, "-m", "trestle", "validate", "-f", str(target)],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0 and "VALID" in r.stdout, r.stdout + r.stderr


def test_one_item_per_failed_check_and_none_for_passes(built):
    titled = {i["title"].split(":")[0] for i in built["poam"]["poam-items"]}
    assert titled == failing_policy_ids()
    assert len(built["poam"]["poam-items"]) == len(failing_policy_ids())


def test_priority_follows_the_catalog_obligation(built):
    controls = catalog_controls()
    for item in built["poam"]["poam-items"]:
        cid = item["title"].split(":")[0].lower()
        obligation = next(p["value"] for p in controls[cid]["props"] if p["name"] == "obligation")
        expected = "moderate" if obligation == "SHOULD" else "high"      # SHALL and SHALL NOT are both hard rules
        props = {p["name"]: p["value"] for p in item["props"]}
        assert props["priority"] == expected, f"{cid}: {obligation} should be {expected}"
        assert props["obligation"] == obligation


def test_every_link_resolves_inside_the_poam(built):
    p = built["poam"]
    finding_ids = {f["uuid"] for f in p["findings"]}
    obs_ids = {o["uuid"] for o in p["observations"]}
    risk_ids = {r["uuid"] for r in p["risks"]}
    for item in p["poam-items"]:
        assert item["related-findings"] and item["related-observations"] and item["related-risks"]
        assert {r["finding-uuid"] for r in item["related-findings"]} <= finding_ids
        assert {r["observation-uuid"] for r in item["related-observations"]} <= obs_ids
        assert {r["risk-uuid"] for r in item["related-risks"]} <= risk_ids
    for r in p["risks"]:
        assert {o["observation-uuid"] for o in r["related-observations"]} <= obs_ids


def test_records_are_the_same_ones_as_in_the_assessment_results(built):
    for key in ("observations", "risks", "findings"):
        assert {x["uuid"] for x in built["poam"][key]} <= {x["uuid"] for x in built["ar"][key]}, key


def test_findings_in_the_poam_are_failures_pointing_at_real_catalog_parts(built):
    statement_ids = {part(c, "statement")["id"] for c in catalog_controls().values()}
    assert built["poam"]["findings"]
    for f in built["poam"]["findings"]:
        assert f["target"]["status"]["state"] == "not-satisfied"
        assert f["target"]["target-id"] in statement_ids


def test_every_risk_carries_the_catalogs_remediation_text(built):
    controls = catalog_controls()
    by_risk = {rr["risk-uuid"]: f for f in built["poam"]["findings"] for rr in f["related-risks"]}
    for r in built["poam"]["risks"]:
        cid = by_risk[r["uuid"]]["target"]["target-id"].removesuffix("_smt")
        (rem,) = r["remediations"]
        assert rem["lifecycle"] == "recommendation"
        assert rem["description"] == part(controls[cid], "remediation")["prose"]


def test_nothing_is_invented(built):
    # no SSP link (optional in a POA&M), and no deadlines or milestones - only the fixers can set those
    assert "import-ssp" not in built["poam"]
    assert all("deadline" not in r for r in built["poam"]["risks"])
    assert all("tasks" not in r for r in built["poam"]["risks"])


def test_uuids_are_unique_per_kind(built):
    p = built["poam"]
    for key in ("poam-items", "observations", "risks", "findings"):
        ids = [x["uuid"] for x in p[key]]
        assert len(ids) == len(set(ids)), key
    assert p["uuid"] not in {x["uuid"] for k in ("poam-items", "observations", "risks", "findings") for x in p[k]}


def test_same_results_reproduce_the_same_poam(built, tmp_path):
    proc, out = make_poam(built["results"], built["out"].parent / "again.json")   # same folder, so the relative links match
    assert proc.returncode == 0, proc.stderr
    docs = []
    for path in (built["out"], out):
        doc = read(path)
        doc[ROOT_KEY]["metadata"].pop("last-modified")     # only the generation time may differ
        docs.append(doc)
    assert docs[0] == docs[1]


def test_a_new_scan_gives_a_poam_with_new_ids(built, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    results = other / "assessment-results.json"
    r = run("make_assessment_results.py", "--results", RESULTS, "--catalog", CATALOG, "--out", results,
            "--scan-time", "2026-02-01T00:00:00+00:00")
    assert r.returncode == 0, r.stderr
    proc, out = make_poam(results)
    assert proc.returncode == 0, proc.stderr
    old = {x["uuid"] for x in built["poam"]["poam-items"]} | {built["poam"]["uuid"]}
    new = {x["uuid"] for x in read(out)[ROOT_KEY]["poam-items"]} | {read(out)[ROOT_KEY]["uuid"]}
    assert not old & new


def test_an_all_pass_scan_writes_no_poam(tmp_path):
    scan = read(RESULTS)
    for c in report_controls(scan):
        if c["Control ID"].lower() in catalog_controls():
            c["Result"] = "Pass"
    scan_file = tmp_path / "AllPass.json"
    scan_file.write_text(json.dumps(scan), encoding="utf-8")
    results = make_results(tmp_path, scan=scan_file)
    proc, out = make_poam(results)
    assert proc.returncode == 0, proc.stderr
    assert not out.exists(), "a POA&M was written even though nothing failed"
    assert "nothing to plan" in proc.stderr


def test_a_finding_pointing_at_nothing_is_refused(built, tmp_path):
    doc = read(built["results"])
    for f in doc["assessment-results"]["results"][0]["findings"]:
        if f["target"]["status"]["state"] == "not-satisfied":
            f["target"]["target-id"] = "ms.aad.9.9v9_smt"       # not in the catalog
            break
    bad = tmp_path / "assessment-results.json"
    bad.write_text(json.dumps(doc), encoding="utf-8")
    proc, out = make_poam(bad)
    assert proc.returncode != 0 and "not in the catalog" in proc.stderr
    assert not out.exists()


def test_poam_file_links_resolve(built):
    for link in built["poam"]["metadata"]["links"]:
        assert (built["out"].parent / link["href"]).exists(), f"dangling link: {link['href']}"


def test_evidence_links_resolve_to_the_scan_file_in_back_matter(built):
    doc = read(built["out"])["plan-of-action-and-milestones"]
    resources = {r["uuid"] for r in doc["back-matter"]["resources"]}
    links = [l["href"] for o in doc["observations"] for l in o.get("links", [])]
    assert links and all(h.startswith("#") and h[1:] in resources for h in links)


def test_every_remediation_has_the_standard_undated_milestones(built):
    for risk in built["poam"]["risks"]:
        (rem,) = risk["remediations"]
        tasks = rem["tasks"]
        assert [t["title"] for t in tasks] == ["Plan the change", "Apply the change", "Verify with a ScubaGear rescan"]
        assert all(t["type"] == "milestone" and "timing" not in t for t in tasks)   # no invented dates
        assert "deadline" not in risk
