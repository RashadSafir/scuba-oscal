"""Tests for the OSCAL profile builder and the OSCAL validator. Run with:
python -m pytest -v tests/test_profile_validate.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
import make_profile  # noqa: E402
import validate_oscal  # noqa: E402

CATALOG = make_profile.DEFAULT_CATALOG


def as_doc(model):
    """The model as the JSON document it would be written as (wrapped in its model name)."""
    return json.loads(model.oscal_serialize_json())


def catalog_ids():
    cat = json.loads(CATALOG.read_text(encoding="utf-8-sig"))["catalog"]
    return [c["id"] for g in cat["groups"] for c in g["controls"]], cat


def test_profile_selects_every_catalog_control_and_is_valid(tmp_path):
    ids, _ = catalog_ids()
    profile = make_profile.build(CATALOG, out_dir=tmp_path)
    assert profile.imports[0].include_controls[0].with_ids == ids
    r = validate_oscal.validate(as_doc(profile))
    assert r["valid"], r["errors"]


def test_shall_profile_is_a_tailored_subset():
    ids, cat = catalog_ids()
    shall = [c["id"] for g in cat["groups"] for c in g["controls"]
             if any(p["name"] == "obligation" and p["value"].startswith("SHALL") for p in c["props"])]
    profile = make_profile.build(CATALOG, obligation="SHALL")
    assert profile.imports[0].include_controls[0].with_ids == shall
    assert 0 < len(shall) < len(ids)


def test_profile_uuid_is_stable_and_follows_the_scope():
    a, b = make_profile.build(CATALOG), make_profile.build(CATALOG)
    assert a.uuid == b.uuid != make_profile.build(CATALOG, obligation="SHALL").uuid


def test_repo_oscal_files_are_valid():
    for name in ("profile.json", "assessment-plan.json", "assessment-results.json", "poam.json", "Controls/EntraID-catalog-full.json"):
        r = validate_oscal.validate_file(ROOT / "oscal" / name)
        assert r["valid"], (name, r["errors"])


def test_validator_reports_what_is_wrong():
    doc = json.loads((ROOT / "oscal" / "poam.json").read_text(encoding="utf-8-sig"))
    body = doc["plan-of-action-and-milestones"]
    del body["poam-items"][0]["title"]
    body["risks"][0]["uuid"] = body["findings"][0]["uuid"]      # same uuid twice
    r = validate_oscal.validate(doc)
    assert not r["valid"]
    assert any("title" in e for e in r["errors"]) and any("more than once" in e for e in r["errors"])


def test_validator_rejects_non_oscal_json():
    r = validate_oscal.validate({"summary": {}})
    assert not r["valid"] and r["model"] is None
