"""Build an OSCAL POA&M (Plan of Action and Milestones) from assessment-results.json.

Inputs : assessment-results.json   what the scan found (from make_assessment_results.py)
         catalog.json              our OSCAL catalog: rule text, obligation, remediation steps
Output : poam.json                 the fix-list: one item per failed check

For every not-satisfied finding in the results, the POA&M gets:
  poam-item   title, description, priority, and links to the finding, observation and risk
  finding, observation, risk   copies of the results' own records, with the SAME uuids, so every
                               link resolves inside this one file. The risk also gets the
                               catalog's remediation steps.
Satisfied findings are left out: nothing to fix.
The results' back-matter (the original scan file and its SHA-256) is copied too, so evidence links resolve.

Priority comes from the catalog's obligation for the control (not the scan's Criticality field,
which the catalog is the authority over). See PRIORITY below; change the mapping there.

If nothing failed there is nothing to plan, and OSCAL does not allow an empty POA&M, so no
file is written.

The POA&M does not link to a System Security Plan: OSCAL makes that optional here.
Deadlines and milestones are left out on purpose; only the people fixing the issues can set them.
Rerunning regenerates the file, so edits made by hand (status, milestones) would be replaced.
"""
import argparse
import sys
import uuid
from pathlib import Path

from trestle.oscal.assessment_results import AssessmentResults
from trestle.oscal.common import AssociatedRisk, Link, Metadata, Property, Response
from trestle.oscal.poam import PlanOfActionAndMilestones, PoamItem, RelatedFinding
from trestle.oscal.poam import RelatedObservation as PoamRelatedObservation

from make_assessment_results import (NS, OSCAL_VERSION, PROP_NS, default_catalog, first_existing, load_catalog,
                                     now_utc, rel_href)

HERE = Path(__file__).resolve().parent

# obligation (from the catalog) -> POA&M priority. A missed SHALL / SHALL NOT is a hard requirement
# failing; a missed SHOULD / SHOULD NOT is a recommendation. Adjust here if the team ranks them differently.
PRIORITY = {"SHALL": "high", "SHALL NOT": "high", "SHOULD": "moderate", "SHOULD NOT": "moderate"}


def default_results():
    return first_existing(HERE / "assessment-results.json", HERE.parent / "oscal" / "assessment-results.json")


def build(results_path, catalog_path, out_dir="."):
    """Returns the POA&M, or None if the results contain no failures."""
    catalog_title, controls = load_catalog(catalog_path)
    by_target = {c["smt_id"]: (cid, c) for cid, c in controls.items()}
    ar = AssessmentResults.oscal_read(Path(results_path))
    result = ar.results[0]
    observations = {o.uuid: o for o in result.observations or []}
    risks = {r.uuid: r for r in result.risks or []}
    ar_id = str(ar.uuid)

    items, findings, used_obs, used_risks = [], [], [], []
    for f in result.findings or []:
        if f.target.status.state.value != "not-satisfied":   # .value: the state is an enum, not a string
            continue
        target = f.target.target_id
        if target not in by_target:
            raise SystemExit(f"Finding {f.title!r} points at {target!r}, which is not in the catalog")
        cid, ctl = by_target[target]
        if ctl["obligation"] not in PRIORITY:
            raise SystemExit(f"{cid}: obligation {ctl['obligation']!r} has no priority mapping")
        if not f.related_risks or not f.related_observations:
            raise SystemExit(f"Finding {f.title!r} lacks a related risk or observation")

        obs_ids = [str(r.observation_uuid) for r in f.related_observations]
        risk_ids = [str(r.risk_uuid) for r in f.related_risks]
        for u in obs_ids:
            used_obs.append(observations[u])
        for u in risk_ids:
            rem = Response(
                uuid=str(uuid.uuid5(NS, f"{ar_id}:remediation:{cid}")), lifecycle="recommendation",
                title=f"Remediation for {ctl['label']}", description=ctl["remediation"])
            used_risks.append(risks[u].model_copy(update={"remediations": [rem]}))
        findings.append(f)

        items.append(PoamItem(
            uuid=str(uuid.uuid5(NS, f"{ar_id}:poam-item:{cid}")),
            title=f"{ctl['label']}: {ctl['title']}",
            description=f"{ctl['statement']} Not met in the scan: {f.description}",
            props=[Property(name="priority", value=PRIORITY[ctl["obligation"]], ns=PROP_NS),
                   Property(name="obligation", value=ctl["obligation"], ns=PROP_NS)],
            related_findings=[RelatedFinding(finding_uuid=str(f.uuid))],
            related_observations=[PoamRelatedObservation(observation_uuid=u) for u in obs_ids],
            related_risks=[AssociatedRisk(risk_uuid=u) for u in risk_ids]))

    if not items:
        return None
    return PlanOfActionAndMilestones(
        uuid=str(uuid.uuid5(NS, f"poam:{ar_id}")),
        metadata=Metadata(
            title=f"SCuBA Plan of Action and Milestones: {catalog_title}",
            last_modified=now_utc(), version="0.1.0", oscal_version=OSCAL_VERSION,
            links=[Link(href=rel_href(results_path, out_dir), rel="reference",
                        text="Assessment results this POA&M was built from"),
                   Link(href=rel_href(catalog_path, out_dir), rel="reference", text=catalog_title)]),
        observations=used_obs, risks=used_risks, findings=findings, poam_items=items,
        back_matter=ar.back_matter)   # the scan file (with its hash) that the observations link to


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--assessment-results", type=Path, default=default_results())
    ap.add_argument("--catalog", type=Path, default=default_catalog())
    ap.add_argument("--out", type=Path, default=None, help="default: poam.json next to the results")
    args = ap.parse_args()
    for f in (args.assessment_results, args.catalog):
        if not f.exists():
            raise SystemExit(f"Missing input: {f}")
    out = args.out or args.assessment_results.parent / "poam.json"
    poam = build(args.assessment_results, args.catalog, out_dir=out.parent)
    if poam is None:
        print("No failed checks in the results, so there is nothing to plan. No POA&M written.",
              file=sys.stderr)
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    poam.oscal_write(out)
    for item in poam.poam_items:
        prio = next(p.value for p in item.props if p.name == "priority")
        print(f"  {prio:9} {item.title}")
    print(f"wrote {out}  ({len(poam.poam_items)} items)")


if __name__ == "__main__":
    main()
