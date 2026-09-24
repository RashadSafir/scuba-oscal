"""Tests for the all-policies catalog. Run with:  python -m pytest -v tests/test_catalog_full.py"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FULL = ROOT / "oscal" / "catalog-full.json"
SCOPED = ROOT / "oscal" / "catalog.json"


def run(script):
    subprocess.run([sys.executable, str(ROOT / "pipeline" / script)], cwd=ROOT, check=True,
                   capture_output=True)


def build():
    FULL.unlink(missing_ok=True)   # so a broken script can't pass on an old file
    run("make_catalog_full.py")


def controls(path):
    cat = json.loads(path.read_text(encoding="utf-8"))["catalog"]
    return {c["id"]: c for g in cat["groups"] for c in g["controls"]}


def test_full_catalog_is_valid_oscal(tmp_path):
    build()
    subprocess.run([sys.executable, "-m", "trestle", "init"], cwd=tmp_path,
                   check=True, capture_output=True)
    target = tmp_path / "catalogs" / "scuba-aad-full" / "catalog.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FULL, target)
    result = subprocess.run([sys.executable, "-m", "trestle", "validate", "-f", str(target)],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0 and "VALID" in result.stdout, result.stdout + result.stderr


def test_has_every_policy_in_the_baseline():
    build()
    aad = (ROOT / "vendor" / "ScubaGear" / "PowerShell" / "ScubaGear" / "baselines" / "aad.md")
    import re
    in_baseline = {m.lower() for m in re.findall(r"^####\s+(MS\.AAD\.\d+\.\d+v\d+)\s*$",
                                                   aad.read_text(encoding="utf-8"), re.M)}
    assert set(controls(FULL)) == in_baseline


def test_every_control_has_all_parts_and_text():
    for cid, c in controls(FULL).items():
        parts = {p["id"]: p.get("prose", "") for p in c["parts"]}
        for suffix in ("_smt", "_gdn", "_rem"):
            assert parts.get(cid + suffix), f"{cid} is missing or has empty {suffix}"
        assert c.get("links"), f"{cid} has no NIST 800-53 links"


def test_scoped_controls_are_identical_in_both_catalogs():
    # the 8 team controls must look exactly the same in the full catalog
    run("make_catalog.py")
    build()
    full = controls(FULL)
    for cid, c in controls(SCOPED).items():
        assert full[cid] == c, f"{cid} differs between catalog.json and catalog-full.json"


def test_output_is_identical_between_runs():
    build()
    first = FULL.read_bytes()
    build()
    assert FULL.read_bytes() == first, "full catalog changed between runs - ids may not be stable"
    