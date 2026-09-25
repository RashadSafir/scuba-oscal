"""Build OSCAL Assessment Results (and the Assessment Plan they refer to) from a ScubaGear results file.

Inputs : scuba_results_sample.json   ScubaGear's results report (ScubaResults.json format):
                                     MetaData (tenant, tool version, scan time) and Results,
                                     grouped by product, one record per control
                                     (Control ID, Requirement, Result, Criticality, Details)
         catalog.json                our OSCAL catalog; it decides which policies are in scope
Output : assessment-results.json     what the scan found
         assessment-plan.json        what was to be assessed, by what method, with what tool
                                     (built from the catalog alone; the results link to it via import-ap)

The plan must link to a System Security Plan (the system owner's description of the system).
None exists yet, so --ssp-href defaults to a placeholder path. Point it at the real SSP once
the system owner supplies one.

How a scan record becomes OSCAL (one set per in-scope policy):
  observation  what was checked + the evidence   (Details, product, group, the report it came from)
  finding      the verdict, pointing at the catalog statement part
                 target-id = policy id lowercased + "_smt"   (the catalog's ID contract)
  risk         only for failures; the catalog's guidance text says why it matters

ScubaGear Result -> OSCAL (see VERDICTS below):
  Pass              satisfied
  Fail              not-satisfied   (a failed SHALL)
  Warning           not-satisfied   (ScubaGear's word for a failed SHOULD)
  N/A, Error, Omitted   no verdict: skipped with a warning (the control was not actually
                        evaluated, and OSCAL has no "not checked" state; calling it a failure
                        or a pass would both be wrong)
Any other Result value stops the script rather than being guessed at.

Rules this script enforces (it stops with a clear message rather than guessing):
  - every catalog control must have a record in the scan, unless --allow-missing is given: then a
    control with no record is skipped with a warning, like N/A (a catalog can be newer than the
    ScubaGear version that ran, e.g. ScubaGear 1.8.0 has no MS.AAD.5.5v1-5.7v1 or MS.AAD.9.1v1)
  - each Control ID appears only once
Scan records for policies outside the catalog are ignored.

Time: the scan time is MetaData.TimestampZulu from the results file. --scan-time overrides it.
The document's own last-modified is the moment it was generated. No end time is recorded,
because the results file does not say when the scan finished.

Evidence: the original results file is recorded in back-matter as a resource with its SHA-256
hash, so an auditor can check the evidence was not altered; every observation links to it.

Ids: every uuid is derived from the scan itself (a hash of the results file plus the scan
time), so the same scan always gives the same ids, while two different scans never share ids.
"""
import argparse
import hashlib
import html
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from trestle.oscal.assessment_plan import AssessmentPlan, SystemComponent
from trestle.oscal.assessment_results import AssessmentResults, ImportAp, Result
from trestle.oscal.common import (AssessmentAssets, AssessmentPlatform, AssociatedRisk, BackMatter,
                                  ControlSelections, Finding, FindingTarget, Hash, ImportSsp, Link, Metadata,
                                  ObjectiveStatus, Observation, Property, RelatedObservation, RelevantEvidence,
                                  ReviewedControls, Resource, Risk, Rlink, SelectControlById, Status, Task,
                                  UsesComponent)

NS = uuid.UUID("6f1c2d3e-0000-4000-8000-5c0ba0000002")   # namespace for our uuid5 ids
PROP_NS = "https://scuba.example/ns"                       # same namespace the catalog uses
OSCAL_VERSION = "1.1.2"                                    # same as the catalog

# ScubaGear Result -> OSCAL objective status; None means "not evaluated, skip it".
VERDICTS = {"Pass": "satisfied", "Fail": "not-satisfied", "Warning": "not-satisfied",
            "N/A": None, "Error": None, "Omitted": None}

HERE = Path(__file__).resolve().parent


def first_existing(*paths):
    """First path that exists (so the script works flat or inside the repo layout)."""
    return next((p for p in paths if p.exists()), paths[0])


def default_results():
    return first_existing(HERE / "scuba_results_sample.json",
                          HERE.parent / "data" / "sample" / "scuba_results_sample.json")


def default_catalog():
    return first_existing(HERE / "catalog.json", HERE.parent / "oscal" / "catalog.json")


def default_out():
    catalog = default_catalog()
    return catalog.parent / "assessment-results.json"


def reviewed_controls(items):
    """items: (control id, statement part id) pairs -> OSCAL reviewed-controls."""
    return ReviewedControls(control_selections=[ControlSelections(include_controls=[
        SelectControlById(control_id=cid, statement_ids=[smt]) for cid, smt in items])])


def rel_href(target, from_dir):
    """Link to `target` as a relative path from the folder the linking file is written to,
    so the link still works when the files live in different folders (oscal/ vs data/sample/)."""
    return Path(os.path.relpath(Path(target).resolve(), Path(from_dir).resolve())).as_posix()


def now_utc():
    return datetime.now(timezone.utc).replace(microsecond=0)


def make_uid(results_path, scan_time):
    """uuid factory tied to this scan: same file + same time -> same ids; any other scan -> new ids."""
    digest = hashlib.sha256(Path(results_path).read_bytes()).hexdigest()
    run_key = f"{digest}:{scan_time.isoformat()}"
    return lambda kind, control_id: str(uuid.uuid5(NS, f"{run_key}:{kind}:{control_id}"))


def clean(text):
    """Keep the words, drop any HTML tags."""
    text = re.sub(r"<br\s*/?>", "\n", text or "")
    text = re.sub(r"<a\s[^>]*>(.*?)</a>", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))   # -sig: file may start with a BOM


def load_catalog(path):
    """control id -> {title, label, obligation, smt_id, statement, guidance, remediation}, in catalog order."""
    cat = load_json(path)["catalog"]
    controls = {}
    for group in cat["groups"]:
        for c in group["controls"]:
            parts = {p["name"]: p for p in c["parts"]}
            props = {p["name"]: p["value"] for p in c["props"]}
            controls[c["id"]] = dict(title=c["title"], label=props["label"], obligation=props["obligation"],
                                     smt_id=parts["statement"]["id"], statement=parts["statement"]["prose"],
                                     guidance=parts["guidance"]["prose"],
                                     remediation=parts["remediation"]["prose"])
    return cat["metadata"]["title"], controls


def load_scan(path):
    """Returns (MetaData dict, {control id lowercased -> record}).

    Each record is the ScubaGear control plus the product and group it was filed under."""
    doc = load_json(path)
    if "Results" not in doc or "MetaData" not in doc:
        raise SystemExit(f"{path} is not a ScubaGear results report (no MetaData / Results sections)")
    records = {}
    for product, groups in doc["Results"].items():
        for group in groups:
            for c in group["Controls"]:
                key = c["Control ID"].lower()
                if key in records:
                    raise SystemExit(f"Duplicate Control ID in scan results: {c['Control ID']}")
                records[key] = dict(c, product=product, group_number=group["GroupNumber"],
                                    group_name=group["GroupName"])
    return doc["MetaData"], records


def scan_time_of(meta):
    stamp = meta.get("TimestampZulu")
    if not stamp:
        raise SystemExit("The results file has no MetaData.TimestampZulu; pass --scan-time")
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).replace(microsecond=0)


def build_plan(catalog_path, ssp_href, out_dir="."):
    """The assessment plan: what gets assessed (the catalog's controls), how (TEST) and with what (ScubaGear).

    It is built from the catalog alone, so it does not change from scan to scan; its uuids come
    from a hash of the catalog, so they only change when the scope does."""
    catalog_title, controls = load_catalog(catalog_path)
    digest = hashlib.sha256(Path(catalog_path).read_bytes()).hexdigest()
    pid = lambda kind: str(uuid.uuid5(NS, f"plan:{digest}:{kind}"))  # noqa: E731

    component_id = pid("component")
    return AssessmentPlan(
        uuid=pid("assessment-plan"),
        metadata=Metadata(title=f"SCuBA Assessment Plan: {catalog_title}",
                          last_modified=now_utc(), version="0.1.0", oscal_version=OSCAL_VERSION,
                          links=[Link(href=rel_href(catalog_path, out_dir), rel="reference", text=catalog_title)]),
        # OSCAL requires a link to the System Security Plan (the system owner's description of the
        # system being assessed). We do not have one, so this points at where it would live.
        import_ssp=ImportSsp(href=ssp_href,
                             remarks="External placeholder: the system owner's SSP has not been supplied."),
        reviewed_controls=reviewed_controls((cid, c["smt_id"]) for cid, c in controls.items()),
        assessment_assets=AssessmentAssets(
            components=[SystemComponent(
                uuid=component_id, type="software", title="CISA ScubaGear",
                description="The tool that runs the SCuBA baseline checks and produces the results "
                            "report (ScubaResults.json) this assessment is built from.",
                status=Status(state="operational"))],
            assessment_platforms=[AssessmentPlatform(
                uuid=pid("platform"), title="ScubaGear",
                uses_components=[UsesComponent(component_uuid=component_id)])]),
        tasks=[Task(
            uuid=pid("task"), type="action", title="Run ScubaGear and evaluate the results",
            description=f"Run ScubaGear against the tenant, then check each in-scope policy in "
                        f"'{catalog_title}' against its result (method: TEST).")])


def build(results_path, catalog_path, scan_time, plan_href, out_dir=".", allow_missing=False):
    catalog_title, controls = load_catalog(catalog_path)
    meta, scan = load_scan(results_path)
    uid = make_uid(results_path, scan_time)
    report = rel_href(results_path, out_dir)   # e.g. ../data/sample/scuba_results_sample.json
    scan_resource = uid("resource", "scubagear-results")
    scan_hash = hashlib.sha256(Path(results_path).read_bytes()).hexdigest()

    missing = [c for c in controls if c not in scan]
    if missing and not allow_missing:
        raise SystemExit(f"In the catalog but not in the scan results: {missing} "
                         "(use --allow-missing to record them as not assessed)")
    for cid in missing:
        print(f"  warning: {controls[cid]['label']} has no record in the scan results; skipped", file=sys.stderr)

    observations, findings, risks, reviewed = [], [], [], []
    for cid, ctl in controls.items():
        if cid not in scan:
            continue
        rec = scan[cid]
        pid, outcome = rec["Control ID"], rec["Result"]
        if outcome not in VERDICTS:
            raise SystemExit(f"{pid}: unknown ScubaGear Result {outcome!r} (expected one of {sorted(VERDICTS)})")
        state = VERDICTS[outcome]
        if state is None:
            print(f"  warning: {pid} was not evaluated by ScubaGear (Result {outcome!r}); skipped",
                  file=sys.stderr)
            continue
        met = state == "satisfied"
        details = clean(rec["Details"])
        obs_id, risk_id = uid("observation", cid), uid("risk", cid)

        props = [Property(name="scuba-policy-id", value=pid, ns=PROP_NS),
                 Property(name="scuba-result", value=outcome, ns=PROP_NS),
                 Property(name="scuba-criticality", value=rec["Criticality"], ns=PROP_NS),
                 Property(name="scuba-product", value=rec["product"], ns=PROP_NS)]
        observations.append(Observation(
            uuid=obs_id, title=f"{pid} check: {ctl['title']}", description=details,
            props=props, methods=["TEST"], types=["finding"], collected=scan_time,
            links=[Link(href=f"#{scan_resource}", rel="evidence", text="Original ScubaGear results file")],
            relevant_evidence=[RelevantEvidence(
                href=report,
                description=f"ScubaGear report {meta.get('ReportUUID', '')}, {rec['product']} "
                            f"group {rec['group_number']} ({rec['group_name']}): Result {outcome}.")]))

        findings.append(Finding(
            uuid=uid("finding", cid),
            title=f"{pid} {'satisfied' if met else 'not satisfied'}",
            description=details,
            target=FindingTarget(
                type="statement-id", target_id=ctl["smt_id"],
                status=ObjectiveStatus(state=state, reason="pass" if met else "fail")),
            related_observations=[RelatedObservation(observation_uuid=obs_id)],
            related_risks=None if met else [AssociatedRisk(risk_uuid=risk_id)]))

        if not met:
            risks.append(Risk(
                uuid=risk_id, title=f"Risk: {ctl['title']}",
                description=f"{pid} was not met in this scan ({outcome}). {details}",
                statement=ctl["guidance"], status="open",
                related_observations=[RelatedObservation(observation_uuid=obs_id)]))

        reviewed.append((cid, ctl["smt_id"]))

    tenant = [("tenant-id", meta.get("TenantId")), ("tenant-name", meta.get("DisplayName")),
              ("tenant-domain", meta.get("DomainName")), ("scuba-tool-version", meta.get("ToolVersion")),
              ("scuba-report-uuid", meta.get("ReportUUID"))]
    result = Result(
        uuid=uid("result", "ms.aad"),
        title="ScubaGear scan results",
        description=f"Results of a ScubaGear scan of tenant {meta.get('DisplayName', '')} "
                    f"({meta.get('DomainName', '')}), assessed against: {catalog_title}.",
        start=scan_time,   # no end: the results file does not say when the scan finished
        props=[Property(name=n, value=str(v), ns=PROP_NS) for n, v in tenant if v] or None,
        reviewed_controls=reviewed_controls(reviewed),
        observations=observations, findings=findings, risks=risks or None)

    return AssessmentResults(
        uuid=uid("assessment-results", "ms.aad"),
        metadata=Metadata(title=f"SCuBA Assessment Results: {catalog_title}",
                          last_modified=now_utc(), version="0.1.0", oscal_version=OSCAL_VERSION,
                          links=[Link(href=rel_href(catalog_path, out_dir), rel="reference", text=catalog_title),
                                 Link(href=report, rel="reference", text="ScubaGear results report")]),
        import_ap=ImportAp(href=plan_href),   # the plan generated alongside these results
        results=[result],
        back_matter=BackMatter(resources=[Resource(
            uuid=scan_resource, title="ScubaGear results file",
            description=f"The ScubaGear results report this assessment was built from ({Path(results_path).name}).",
            rlinks=[Rlink(href=report, media_type="application/json",
                          hashes=[Hash(algorithm="SHA-256", value=scan_hash)])])])),         (len(observations), len(findings), len(risks))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--results", type=Path, default=default_results())
    ap.add_argument("--catalog", type=Path, default=default_catalog())
    ap.add_argument("--out", type=Path, default=default_out())
    ap.add_argument("--plan-out", type=Path, default=None,
                    help="where to write the assessment plan (default: next to --out)")
    ap.add_argument("--ssp-href", default="./system-security-plan.json",
                    help="where the system owner's SSP lives; the plan must link to it "
                         "(default: a placeholder path, since no SSP has been supplied)")
    ap.add_argument("--scan-time", type=datetime.fromisoformat, default=None,
                    help="override the scan time, ISO format, e.g. 2026-09-23T14:30:00+00:00 "
                         "(default: MetaData.TimestampZulu from the results file)")
    ap.add_argument("--allow-missing", action="store_true",
                    help="skip catalog controls that have no record in the scan (they come out as not "
                         "assessed) instead of stopping")
    args = ap.parse_args()
    for f in (args.results, args.catalog):
        if not f.exists():
            raise SystemExit(f"Missing input: {f}")
    scan_time = args.scan_time or scan_time_of(load_scan(args.results)[0])
    if scan_time.tzinfo is None:
        scan_time = scan_time.replace(tzinfo=timezone.utc)   # a time with no zone is taken as UTC
    plan_out = args.plan_out or args.out.parent / "assessment-plan.json"
    plan_href = Path(os.path.relpath(plan_out, args.out.parent)).as_posix()   # how the results find the plan
    plan = build_plan(args.catalog, args.ssp_href, out_dir=plan_out.parent)
    doc, (n_obs, n_find, n_risk) = build(args.results, args.catalog, scan_time, plan_href,
                                         out_dir=args.out.parent, allow_missing=args.allow_missing)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    plan_out.parent.mkdir(parents=True, exist_ok=True)
    plan.oscal_write(plan_out)
    doc.oscal_write(args.out)
    print(f"wrote {plan_out}  (scope: {len(plan.reviewed_controls.control_selections[0].include_controls)} controls)")
    print(f"wrote {args.out}  ({n_obs} observations, {n_find} findings, {n_risk} risks)")


if __name__ == "__main__":
    main()
