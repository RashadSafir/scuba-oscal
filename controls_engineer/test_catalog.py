"""Tests for the SCuBA MS.AAD catalog. Run with:  python -m pytest -v"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent   # controls_engineer/
ROOT = HERE.parent                        # repo root
CATALOG = ROOT / "oscal" / "catalog.json"

# The contract the rest of the team depends on
EXPECTED = {
    "ms.aad.1.1v1": "SHALL",
    "ms.aad.2.1v1": "SHALL",
    "ms.aad.2.3v1": "SHALL",
    "ms.aad.3.8v1": "SHOULD",
    "ms.aad.5.2v1": "SHALL",
    "ms.aad.7.1v1": "SHALL",
    "ms.aad.7.4v1": "SHALL NOT",
    "ms.aad.8.1v1": "SHOULD",
}


def build():
    CATALOG.unlink(missing_ok=True)   # so an empty or broken script can't pass on an old file
    subprocess.run([sys.executable, str(HERE / "make_catalog.py")], cwd=ROOT, check=True,
                   capture_output=True)


def controls():
    cat = json.loads(CATALOG.read_text(encoding="utf-8"))["catalog"]
    return {c["id"]: c for g in cat["groups"] for c in g["controls"]}


def test_catalog_is_valid_oscal(tmp_path):
    # trestle only validates files inside its own workspace layout, so copy the
    # catalog into a throwaway workspace and validate it there
    build()
    subprocess.run([sys.executable, "-m", "trestle", "init"], cwd=tmp_path,
                   check=True, capture_output=True)
    target = tmp_path / "catalogs" / "scuba-aad" / "catalog.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(CATALOG, target)
    result = subprocess.run([sys.executable, "-m", "trestle", "validate", "-f", str(target)],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0 and "VALID" in result.stdout, result.stdout + result.stderr


def test_has_exactly_the_scoped_controls():
    assert set(controls()) == set(EXPECTED)


def test_every_control_has_statement_and_guidance_ids():
    for cid, c in controls().items():
        part_ids = {p["id"] for p in c["parts"]}
        assert f"{cid}_smt" in part_ids, f"{cid} is missing its statement part"
        assert f"{cid}_gdn" in part_ids, f"{cid} is missing its guidance part"


def test_obligations_are_correct():
    for cid, c in controls().items():
        obligation = next(p["value"] for p in c["props"] if p["name"] == "obligation")
        assert obligation == EXPECTED[cid], f"{cid}: got {obligation}, expected {EXPECTED[cid]}"


def test_every_control_links_to_nist():
    for cid, c in controls().items():
        assert c.get("links"), f"{cid} has no NIST 800-53 links"


def test_output_is_identical_between_runs():
    build()
    first = CATALOG.read_bytes()
    build()
    assert CATALOG.read_bytes() == first, "catalog changed between runs - ids may not be stable"