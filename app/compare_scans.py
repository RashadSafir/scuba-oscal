"""Compare this scan with an earlier one, from the earlier scan's OSCAL assessment results.

Plain code, no AI. Each control is matched by its catalog id (the finding's target, <id>_smt) and
classified by its result then and now:

  resolved     failed before, passes now
  regressed    passed before, fails now
  new          not checked before (or not in that scan), fails now
  still failing  failed before and now
  unchanged    passed before and now, or not assessed in either

A resolved control's POA&M item from the earlier scan is proposed for closure; a person confirms it.
make_poam.py derives each item's uuid from the assessment results' uuid and the control id, so the
earlier item's uuid can be worked out from the earlier assessment results alone.
"""
import uuid
from datetime import datetime

from validate_oscal import validate

NS = uuid.UUID("6f1c2d3e-0000-4000-8000-5c0ba0000002")   # same namespace as pipeline/make_poam.py
STATES = {"satisfied": "PASS", "not-satisfied": "FAIL"}
KINDS = ["resolved", "regressed", "new", "still failing", "unchanged"]


class ComparisonError(ValueError):
    pass


def props(items):
    return {p.get("name"): p.get("value") for p in items or []}


def read_previous(doc):
    """{"uuid", "tenant_id", "tenant", "scan_time", "results": {control id: "PASS" | "FAIL"}} from an OSCAL
    assessment-results document. Raises ComparisonError when it is not one."""
    check = validate(doc)
    if check["model"] != "assessment-results":
        raise ComparisonError("This is not an OSCAL assessment results file. Upload the assessment-results.json "
                              "downloaded from the OSCAL tab for the earlier scan.")
    if not check["valid"]:
        raise ComparisonError("The earlier assessment results are not valid OSCAL: " + "; ".join(check["errors"][:3]))
    ar = doc["assessment-results"]
    result = ar["results"][0]
    info = props(result.get("props"))
    results = {}
    for f in result.get("findings", []):
        target = f["target"]["target-id"]
        cid = target[:-4] if target.endswith("_smt") else target
        results[cid] = STATES.get(f["target"]["status"]["state"], "FAIL")
    return {"uuid": ar["uuid"], "tenant_id": info.get("tenant-id"), "tenant": info.get("tenant-name"),
            "scan_time": result.get("start"), "results": results}


def classify(before, now):
    if before == "FAIL" and now == "PASS":
        return "resolved"
    if before == "PASS" and now == "FAIL":
        return "regressed"
    if before == "FAIL" and now == "FAIL":
        return "still failing"
    if before is None and now == "FAIL":
        return "new"
    return "unchanged"


def compare(previous, findings):
    """{kind: [Finding]} for the current findings against the earlier results."""
    out = {k: [] for k in KINDS}
    for f in findings:
        cid = f.oscal_control_id or f.control_id.lower()
        now = f.status if f.status in ("PASS", "FAIL") else None
        out[classify(previous["results"].get(cid), now)].append(f)
    return out


def previous_poam_item(previous, f):
    """The uuid make_poam.py gave this control's item in the earlier POA&M."""
    return str(uuid.uuid5(NS, f"{previous['uuid']}:poam-item:{f.oscal_control_id or f.control_id.lower()}"))


def counts(results):
    vals = list(results.values())
    return {"PASS": vals.count("PASS"), "FAIL": vals.count("FAIL")}


def warnings(previous, current_tenant_id, current_scan_time):
    """Plain-language problems with the pairing: a different tenant, or an earlier scan that is not earlier."""
    out = []
    if previous["tenant_id"] and current_tenant_id and previous["tenant_id"] != current_tenant_id:
        out.append(f"The earlier results are for a different tenant ({previous['tenant'] or previous['tenant_id']}).")
    try:
        if datetime.fromisoformat(previous["scan_time"]) >= datetime.fromisoformat(current_scan_time):
            out.append("The earlier results are not from an earlier scan.")
    except (TypeError, ValueError):
        pass
    return out


def closing_observation(f, previous, closure, ar_uuid):
    """The OSCAL observation recording that a person confirmed an earlier POA&M item is closed."""
    item = previous_poam_item(previous, f)
    return {
        "uuid": str(uuid.uuid5(NS, f"{ar_uuid}:closes:{item}")),
        "title": f"{f.control_id} resolved: earlier POA&M item closed",
        "description": f"{f.control_id} failed in the scan of {previous['scan_time']} and passes in this scan. "
                       f"{closure['by']} confirmed the POA&M item {item} is closed.",
        "props": [{"name": "scuba-policy-id", "value": f.control_id, "ns": "https://scuba.example/ns"},
                  {"name": "closes-poam-item", "value": item, "ns": "https://scuba.example/ns"}],
        "methods": ["EXAMINE"],
        "types": ["finding"],
        "collected": closure["at"],
        "remarks": "Closure confirmed by a person in the SCuBA posture assistant; the pass comes from the scan.",
    }
