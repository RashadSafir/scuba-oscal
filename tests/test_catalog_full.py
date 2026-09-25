"""Tests for the per-product catalogs in oscal/Controls (built by pipeline/make_catalog_full.py).
Run with:  python -m pytest -v tests/test_catalog_full.py"""
import json                                    # reads the catalog files
import re                                      # finds policy headings in the rulebooks
import shutil                                  # copies files
import subprocess                              # runs our scripts and trestle as separate programs
import sys                                     # gives us the path to the Python being used
from pathlib import Path                       # works with file and folder paths

ROOT = Path(__file__).resolve().parent.parent                      # the project folder
CONTROLS = ROOT / "oscal" / "Controls"                             # where the app and AI read catalogs
ENTRA = CONTROLS / "EntraID-catalog-full.json"                     # the Entra ID catalog
SCOPED = ROOT / "oscal" / "catalog.json"                           # the team's 8-control catalog
BASELINES = ROOT / "vendor" / "ScubaGear" / "PowerShell" / "ScubaGear" / "baselines"


def run(script):
    """Run one of our pipeline scripts, and fail the test if it errors."""
    subprocess.run([sys.executable, str(ROOT / "pipeline" / script)], cwd=ROOT, check=True,
                   capture_output=True)


def catalog_files():
    """Every per-product catalog in oscal/Controls."""
    return sorted(CONTROLS.glob("*-catalog-full.json"))


def build():
    for p in catalog_files():
        p.unlink()                             # delete the old files first, so a broken script can't pass
    run("make_catalog_full.py")                # rebuild them


def controls(path):
    """Return {control id: control} for one catalog file."""
    cat = json.loads(path.read_text(encoding="utf-8"))["catalog"]
    return {c["id"]: c for g in cat["groups"] for c in g["controls"]}


def policies_in(md_file):
    """The policy ids written in one rulebook, lowercased, e.g. {'ms.aad.1.1v1', ...}."""
    text = md_file.read_text(encoding="utf-8")
    return {m.lower() for m in re.findall(r"^####\s+(MS\.[A-Z]+\.\d+\.\d+v\d+)\s*$", text, re.M)}


def test_every_catalog_is_valid_oscal(tmp_path):
    # trestle only validates files inside its own folder layout, so copy each
    # catalog into a throwaway trestle workspace and validate it there
    build()
    subprocess.run([sys.executable, "-m", "trestle", "init"], cwd=tmp_path,
                   check=True, capture_output=True)
    assert catalog_files(), "no catalogs were written"
    for path in catalog_files():
        target = tmp_path / "catalogs" / path.stem / "catalog.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, target)
        result = subprocess.run([sys.executable, "-m", "trestle", "validate", "-f", str(target)],
                                cwd=tmp_path, capture_output=True, text=True)
        assert result.returncode == 0 and "VALID" in result.stdout, f"{path.name}: {result.stdout}{result.stderr}"


def test_entra_file_has_every_entra_policy():
    build()
    assert set(controls(ENTRA)) == policies_in(BASELINES / "aad.md")


def test_every_policy_appears_exactly_once_across_the_folder():
    # the app merges every file in oscal/Controls, so no policy may be missing or duplicated
    build()
    expected = set()
    for md in BASELINES.glob("*.md"):
        if md.name != "removedpolicies.md":    # retired policies are deliberately left out
            expected |= policies_in(md)
    seen = [cid for path in catalog_files() for cid in controls(path)]   # every control id in every file
    duplicates = {cid for cid in seen if seen.count(cid) > 1}
    assert not duplicates, f"controls appear in more than one file: {sorted(duplicates)}"
    assert set(seen) == expected


def test_every_file_has_the_same_layout():
    # catalog -> groups -> controls, with no groups nested inside groups, so the app reads them all the same way
    for path in catalog_files():
        cat = json.loads(path.read_text(encoding="utf-8"))["catalog"]
        assert set(cat) == {"uuid", "metadata", "groups"}, f"{path.name}: unexpected top-level keys"
        for g in cat["groups"]:
            assert set(g) == {"id", "title", "controls"}, f"{path.name}: group {g.get('id')} has a different shape"


def test_every_control_has_all_parts_text_and_links():
    for path in catalog_files():
        for cid, c in controls(path).items():
            parts = {p["id"]: p.get("prose", "") for p in c["parts"]}
            for suffix in ("_smt", "_gdn", "_rem"):              # statement, guidance, remediation
                assert parts.get(cid + suffix), f"{cid} is missing or has empty {suffix}"
            assert c.get("links"), f"{cid} has no NIST 800-53 links"


def test_obligation_matches_the_rule_wording():
    # the obligation prop must agree with the words in the rule sentence
    for path in catalog_files():
        for cid, c in controls(path).items():
            obligation = next(p["value"] for p in c["props"] if p["name"] == "obligation")
            statement = next(p["prose"] for p in c["parts"] if p["id"] == f"{cid}_smt")
            assert obligation in statement, f"{cid}: obligation {obligation} not found in '{statement}'"


def test_scoped_controls_are_identical_in_both_catalogs():
    # the team's 8 controls must look exactly the same in catalog.json and EntraID-catalog-full.json
    run("make_catalog.py")
    build()
    entra = controls(ENTRA)
    for cid, c in controls(SCOPED).items():
        assert entra[cid] == c, f"{cid} differs between catalog.json and EntraID-catalog-full.json"


def test_output_is_identical_between_runs():
    build()
    first = {p.name: p.read_bytes() for p in catalog_files()}   # every file after one run
    build()
    second = {p.name: p.read_bytes() for p in catalog_files()}  # every file after a second run
    assert first == second, "catalogs changed between runs - ids may not be stable"