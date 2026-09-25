"""Check OSCAL files against the OSCAL 1.1.2 models.

compliance-trestle's models are generated from the NIST OSCAL JSON schemas, so loading a file into
its model checks the structure, required fields, data types and allowed values. On top of that we
check that no uuid is used twice in one file.

Usage: python validate_oscal.py FILE [FILE ...]      exits 1 if any file is invalid
"""
import json
import sys
from collections import Counter
from pathlib import Path

from trestle.oscal.assessment_plan import AssessmentPlan
from trestle.oscal.assessment_results import AssessmentResults
from trestle.oscal.catalog import Catalog
from trestle.oscal.poam import PlanOfActionAndMilestones
from trestle.oscal.profile import Profile

MODELS = {"catalog": Catalog, "profile": Profile, "assessment-plan": AssessmentPlan,
          "assessment-results": AssessmentResults, "plan-of-action-and-milestones": PlanOfActionAndMilestones}


def own_uuids(node, found=None):
    """Every "uuid" field in the document (the ids it defines, not the ones it links to)."""
    found = [] if found is None else found
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "uuid" and isinstance(v, str):
                found.append(v)
            else:
                own_uuids(v, found)
    elif isinstance(node, list):
        for v in node:
            own_uuids(v, found)
    return found


def validate(doc):
    """doc: a parsed OSCAL JSON document. Returns a dict:
    valid (bool), errors (list of str), model, uuid, title, version, oscal_version."""
    info = {"valid": False, "errors": [], "model": None, "uuid": None, "title": None,
            "version": None, "oscal_version": None}
    if not isinstance(doc, dict) or len(doc) != 1 or next(iter(doc)) not in MODELS:
        info["errors"].append(f"Not an OSCAL document: expected one top-level key from {sorted(MODELS)}")
        return info
    model, body = next(iter(doc.items()))
    meta = body.get("metadata", {}) if isinstance(body, dict) else {}
    info.update(model=model, uuid=body.get("uuid"), title=meta.get("title"), version=meta.get("version"),
                oscal_version=meta.get("oscal-version"))
    try:
        MODELS[model].model_validate(body)
    except Exception as e:   # pydantic ValidationError: one line per problem
        info["errors"] += [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in getattr(e, "errors", lambda: [])()] \
            or [str(e)]
    dupes = [u for u, n in Counter(own_uuids(body)).items() if n > 1]
    info["errors"] += [f"uuid used more than once: {u}" for u in dupes]
    info["valid"] = not info["errors"]
    return info


def validate_file(path):
    return validate(json.loads(Path(path).read_text(encoding="utf-8-sig")))


def main():
    bad = 0
    for path in sys.argv[1:]:
        r = validate_file(path)
        print(f"{'VALID  ' if r['valid'] else 'INVALID'} {path}  ({r['model']}, OSCAL {r['oscal_version']})")
        for e in r["errors"][:20]:
            print(f"    {e}")
        bad += not r["valid"]
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
