"""Compare a SCuBA OSCAL catalog with ScubaGear OSCAL assessment results and write findings.json.

Deterministic, no AI. Each ScubaGear result is matched to a SCuBA control by OSCAL control id
(the finding's target-id is <control id>_smt, per docs/CONTRACTS.md). ScubaGear's PASS/FAIL is
kept as-is (PASS, FAIL, or WARNING when ScubaGear said Warning); a control with no result is
NOT_ASSESSED. Every FAIL and WARNING becomes a finding.

Run:
  python comparison/compare_oscal.py \
      --scuba oscal/Controls \
      --scubagear oscal/assessment-results.json \
      --output oscal/findings.json

--scuba takes a catalog file, or a folder whose *.json files are all catalogs.

Sections: models, SCuBA parser, ScubaGear parser, comparison, output, command line.
The parsers turn OSCAL into plain records, so the comparison never touches OSCAL nesting.
"""
import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

PASS, FAIL, WARNING, NOT_ASSESSED = "PASS", "FAIL", "WARNING", "NOT_ASSESSED"
FINDING_STATUSES = (FAIL, WARNING)


# ---------------------------------------------------------------- models

class CompareError(Exception):
    """Bad input: missing file, invalid JSON, wrong OSCAL model, missing id, duplicate id."""


@dataclass(frozen=True)
class Control:
    """One SCuBA control: the expected security state. Optional fields are None when absent."""
    control_id: str                    # OSCAL control id, e.g. ms.aad.7.4v1
    label: str | None                  # policy id as SCuBA writes it, e.g. MS.AAD.7.4v1
    title: str | None
    group: str | None                  # catalog group title, e.g. "Privileged Access"
    obligation: str | None             # SHALL | SHALL NOT | SHOULD
    description: str | None
    requirement: str | None            # the statement part
    guidance: str | None               # why the control matters
    recommendation: str | None
    remediation_guidance: str | None   # how to fix it
    nist: list[str] = field(default_factory=list)          # related NIST SP 800-53 controls
    source: str = ""                   # catalog file name


@dataclass(frozen=True)
class AssessmentResult:
    """One ScubaGear result: the actual assessed state. ScubaGear's verdict is the source of truth."""
    control_id: str                    # OSCAL control id this result is for
    status: str                        # PASS | FAIL | WARNING (see the ScubaGear parser notes)
    scubagear_result: str | None       # ScubaGear's own word: Pass | Fail | Warning
    finding: str | None                # ScubaGear's details for this tenant
    evidence: list[str] = field(default_factory=list)
    affected_resources: list[str] = field(default_factory=list)
    collected: str | None = None       # when the observation was made
    finding_uuid: str | None = None    # the OSCAL finding this result came from
    observation_uuids: list[str] = field(default_factory=list)   # its related OSCAL observations
    risk_uuids: list[str] = field(default_factory=list)          # its related OSCAL risks (failures only)


def load_json(path):
    path = Path(path)
    if not path.is_file():
        raise CompareError(f"file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise CompareError(f"{path.name} is not valid JSON: {e}") from None


def props_dict(props):
    return {p["name"]: p.get("value") for p in props or [] if p.get("name")}


# ---------------------------------------------------------------- SCuBA parser
#
# Where things live in the catalog (checked against oscal/Controls/EntraID-catalog-full.json):
#   catalog.groups[].controls[]       controls (groups and controls may nest; both are walked)
#   control.id                        control id, e.g. ms.aad.7.4v1
#   control.props[name=label]         policy id as SCuBA writes it, e.g. MS.AAD.7.4v1
#   control.props[name=obligation]    SHALL | SHALL NOT | SHOULD
#   control.parts[name=statement]     the requirement       (id <control id>_smt)
#   control.parts[name=guidance]      why it matters        (id <control id>_gdn)
#   control.parts[name=remediation]   how to fix it         (id <control id>_rem)
#   control.links[rel=related]        related NIST SP 800-53 controls
# Description and recommendation parts are read too if a catalog has them; the SCuBA one does not.

def _prose(part):
    """A part's prose plus the prose of any sub-parts (OSCAL statements can have items)."""
    texts = [part.get("prose", "")] + [_prose(p) for p in part.get("parts", [])]
    return "\n".join(t for t in texts if t)


def _text(parts, *names):
    for name in names:
        if name in parts:
            return _prose(parts[name]) or None
    return None


def parse_catalog(path):
    path = Path(path)
    data = load_json(path)
    catalog = data.get("catalog") if isinstance(data, dict) else None
    if not isinstance(catalog, dict):
        raise CompareError(f"{path.name} is not an OSCAL catalog (no top-level 'catalog' object)")

    controls = {}

    def add(c, group):
        cid = (c.get("id") or "").strip()
        if not cid:
            raise CompareError(f"{path.name}: control {c.get('title')!r} has no id")
        if cid in controls:
            raise CompareError(f"{path.name}: duplicate control id {cid}")
        parts = {}
        for p in c.get("parts", []):
            parts.setdefault(p.get("name"), p)
        props = props_dict(c.get("props"))
        controls[cid] = Control(
            control_id=cid, label=props.get("label"), title=c.get("title"), group=group,
            obligation=props.get("obligation"),
            description=_text(parts, "overview", "description"),
            requirement=_text(parts, "statement"),
            guidance=_text(parts, "guidance"),
            recommendation=_text(parts, "recommendation", "recommendations"),
            remediation_guidance=_text(parts, "remediation"),
            nist=[link["text"] for link in c.get("links", []) if link.get("rel") == "related" and link.get("text")],
            source=path.name)

    def walk(node, group):
        for g in node.get("groups", []):
            walk(g, g.get("title") or group)
        for c in node.get("controls", []):
            add(c, group)
            walk(c, group)  # control enhancements nest under their parent control

    walk(catalog, None)
    if not controls:
        raise CompareError(f"{path.name}: catalog has no controls")
    return controls


def parse_catalogs(paths):
    """Several catalogs merged into one dict. A control id in two catalogs is an error."""
    merged = {}
    for path in paths:
        for cid, control in parse_catalog(path).items():
            if cid in merged:
                raise CompareError(f"duplicate control id {cid} in {merged[cid].source} and {control.source}")
            merged[cid] = control
    return merged


# ---------------------------------------------------------------- ScubaGear parser
#
# Where things live (checked against oscal/assessment-results.json):
#   assessment-results.results[]              one per scan
#   result.findings[]                         one per assessed policy
#   finding.target.target-id                  statement id, <control id>_smt (docs/CONTRACTS.md)
#   finding.target.status.state               satisfied | not-satisfied
#   finding.description                       ScubaGear's details for this tenant
#   finding.related-observations[]            -> result.observations[] by uuid
#   observation.props[name=scuba-policy-id]   policy id, e.g. MS.AAD.7.4v1 (cross-checked)
#   observation.props[name=scuba-result]      ScubaGear's own word: Pass | Fail | Warning
#   observation.relevant-evidence[]           where the verdict came from
#   observation.subjects[]                    affected resources (current scans have none)
#   finding.uuid, observation.uuid, finding.related-risks[]   kept so answers can cite the OSCAL records
#
# Status is ScubaGear's, carried over as-is:
#   satisfied                                  -> PASS
#   not-satisfied, ScubaGear result Warning    -> WARNING  (ScubaGear's word for a failed SHOULD)
#   not-satisfied, any other ScubaGear result  -> FAIL
# OSCAL has only satisfied / not-satisfied, so pipeline/make_assessment_results.py records both
# Fail and Warning as not-satisfied; the observation's scuba-result prop keeps the difference,
# and this parser restores it. Both FAIL and WARNING are findings.
# Any other state is an error, not a guess. ScubaGear N/A, Error and Omitted results have no
# finding here, so those controls come out NOT_ASSESSED.

STATES = {"satisfied": PASS, "not-satisfied": FAIL}
STATEMENT_SUFFIX = "_smt"


def _control_id(finding, name):
    target = finding.get("target") or {}
    tid = (target.get("target-id") or "").strip()
    if not tid:
        raise CompareError(f"{name}: finding {finding.get('title') or finding.get('uuid')!r} has no target-id")
    if target.get("type") == "statement-id" and tid.endswith(STATEMENT_SUFFIX):
        return tid[:-len(STATEMENT_SUFFIX)]
    return tid


def parse_results(path):
    """Returns (results keyed by control id, scan info)."""
    path = Path(path)
    data = load_json(path)
    ar = data.get("assessment-results") if isinstance(data, dict) else None
    if not isinstance(ar, dict):
        raise CompareError(f"{path.name} is not OSCAL assessment results (no top-level 'assessment-results' object)")
    scans = ar.get("results")
    if not scans:
        raise CompareError(f"{path.name}: assessment results have no 'results' section")

    results = {}
    for scan in scans:
        observations = {o.get("uuid"): o for o in scan.get("observations", [])}
        for f in scan.get("findings", []):
            cid = _control_id(f, path.name)
            if cid in results:
                raise CompareError(f"{path.name}: more than one result for control {cid}")

            state = ((f.get("target") or {}).get("status") or {}).get("state")
            if state not in STATES:
                raise CompareError(f"{path.name}: {cid} has unexpected finding state {state!r}")

            obs = []
            for ref in f.get("related-observations", []):
                uuid = ref.get("observation-uuid")
                if uuid not in observations:
                    raise CompareError(f"{path.name}: {cid} links to missing observation {uuid}")
                obs.append(observations[uuid])
            obs_props = [props_dict(o.get("props")) for o in obs]

            policy = next((p["scuba-policy-id"] for p in obs_props if p.get("scuba-policy-id")), None)
            if policy and policy.lower() != cid:
                raise CompareError(f"{path.name}: finding targets {cid} but its observation is for {policy}")

            scubagear_result = next((p["scuba-result"] for p in obs_props if p.get("scuba-result")), None)
            status = STATES[state]
            if status == FAIL and (scubagear_result or "").lower() == "warning":
                status = WARNING

            results[cid] = AssessmentResult(
                control_id=cid, status=status, scubagear_result=scubagear_result,
                finding=f.get("description") or None,
                evidence=[e["description"] for o in obs for e in o.get("relevant-evidence", []) if e.get("description")],
                affected_resources=[s.get("title") or s.get("subject-uuid")
                                    for o in obs for s in o.get("subjects", []) if s.get("title") or s.get("subject-uuid")],
                collected=next((o["collected"] for o in obs if o.get("collected")), None),
                finding_uuid=f.get("uuid"),
                observation_uuids=[o["uuid"] for o in obs if o.get("uuid")],
                risk_uuids=[r["risk-uuid"] for r in f.get("related-risks", []) if r.get("risk-uuid")])

    props = props_dict(scans[0].get("props"))
    scan_info = dict(tenant=props.get("tenant-name"), domain=props.get("tenant-domain"),
                     scan_time=scans[0].get("start"), tool_version=props.get("scuba-tool-version"),
                     assessment_results_uuid=ar.get("uuid"), result_uuid=scans[0].get("uuid"))
    return results, scan_info


# ---------------------------------------------------------------- comparison

def combine(control, result, scubagear_source):
    """One control's requirement joined with its assessment result. Facts only."""
    return {
        "control_id": control.label or control.control_id,
        "oscal_control_id": control.control_id,
        "title": control.title,
        "group": control.group,
        "obligation": control.obligation,
        "status": result.status if result else NOT_ASSESSED,
        "scubagear_result": result.scubagear_result if result else None,
        "requirement": control.requirement,
        "description": control.description,
        "guidance": control.guidance,
        "recommendation": control.recommendation,
        "finding": result.finding if result else None,
        "evidence": result.evidence if result else [],
        "affected_resources": result.affected_resources if result else [],
        "remediation_guidance": control.remediation_guidance,
        "nist": control.nist,
        "oscal": {"finding_uuid": result.finding_uuid if result else None,
                  "observation_uuids": result.observation_uuids if result else [],
                  "risk_uuids": result.risk_uuids if result else []},
        "source": {"scuba": control.source, "scubagear": scubagear_source},
    }


def compare(controls, results, scubagear_source):
    """Returns (assessments for every control, findings = FAILs and WARNINGs, results with no control)."""
    assessments = [combine(c, results.get(cid), scubagear_source) for cid, c in controls.items()]
    findings = [a for a in assessments if a["status"] in FINDING_STATUSES]
    unmatched = [r for cid, r in results.items() if cid not in controls]
    return assessments, findings, unmatched


# ---------------------------------------------------------------- output
#
#   metadata             when it was made, which files it came from, scan facts
#   summary              counts of PASS, FAIL, WARNING, NOT_ASSESSED, findings, unmatched results
#   assessments          every catalog control with its status
#   findings             the FAIL and WARNING subset of assessments
#   unmatched_results    ScubaGear results for controls that are not in the catalog

def build_document(assessments, findings, unmatched, scan_info, scuba_sources, scubagear_source):
    count = lambda s: sum(a["status"] == s for a in assessments)  # noqa: E731
    return {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "scuba_sources": scuba_sources,
            "scubagear_source": scubagear_source,
            "scan": scan_info,
        },
        "summary": {
            "total_controls": len(assessments),
            "passed": count(PASS),
            "failed": count(FAIL),
            "warnings": count(WARNING),
            "not_assessed": count(NOT_ASSESSED),
            "findings": len(findings),
            "unmatched_results": len(unmatched),
        },
        "assessments": assessments,
        "findings": findings,
        "unmatched_results": [asdict(r) for r in unmatched],
    }


def write_json(doc, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- command line

def catalog_paths(path):
    path = Path(path)
    if path.is_dir():
        files = sorted(path.glob("*.json"))
        if not files:
            raise CompareError(f"no catalog *.json files in {path}")
        return files
    return [path]


def run(scuba, scubagear, output):
    paths = catalog_paths(scuba)
    print("Loading SCuBA OSCAL catalog...")
    controls = parse_catalogs(paths)
    print(f"Found {len(controls)} controls in {', '.join(p.name for p in paths)}.\n")

    print("Loading ScubaGear OSCAL assessment...")
    results, scan_info = parse_results(scubagear)
    print(f"Found {len(results)} assessment results.\n")

    print("Matching controls...")
    assessments, findings, unmatched = compare(controls, results, Path(scubagear).name)
    print(f"Matched {len(results) - len(unmatched)} controls.")
    print(f"Unmatched ScubaGear controls: {len(unmatched)}")
    for r in unmatched:
        print(f"  WARNING: ScubaGear result {r.control_id} ({r.status}) has no control in the catalog")

    doc = build_document(assessments, findings, unmatched, scan_info,
                         [p.name for p in paths], Path(scubagear).name)
    s = doc["summary"]
    print(f"\nAssessment summary:\n  PASS: {s['passed']}\n  FAIL: {s['failed']}\n  WARNING: {s['warnings']}\n  NOT ASSESSED: {s['not_assessed']}\n")
    print(f"Generated {len(findings)} findings.\n")

    write_json(doc, output)
    print(f"Saved findings to:\n{output}")
    return doc


def main(argv=None):
    ap = argparse.ArgumentParser(description="Compare a SCuBA OSCAL catalog with ScubaGear OSCAL assessment results.")
    ap.add_argument("--scuba", required=True, help="SCuBA OSCAL catalog file, or a folder of catalog files")
    ap.add_argument("--scubagear", required=True, help="ScubaGear OSCAL assessment results file")
    ap.add_argument("--output", required=True, help="path for the generated findings JSON")
    args = ap.parse_args(argv)
    try:
        run(args.scuba, args.scubagear, args.output)
    except CompareError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
