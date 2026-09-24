"""The Finding model: one record per catalog control, joined from the OSCAL files.

Inputs : oscal/catalog.json              what each control requires (statement, guidance, remediation)
         oscal/assessment-results.json   what the scan found (finding verdict + observation evidence)

This is plain code, not AI. Everything in a Finding is copied from the two files; nothing is
inferred. The AI layer (assistant.py) receives these records as its only facts.

Join: finding.target.target-id == the catalog statement part id (the "_smt" id contract).
Status:
  PASS           finding state satisfied
  FAIL           finding state not-satisfied (ScubaGear Fail, or Warning for a SHOULD)
  NOT ASSESSED   the control is in the catalog but the results have no finding for it
                 (ScubaGear returned N/A, Error or Omitted, and the results builder skipped it)
"""
import html
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "oscal" / "catalog.json"
RESULTS = ROOT / "oscal" / "assessment-results.json"

# Same obligation -> priority mapping as pipeline/make_poam.py. Keep the two in step.
PRIORITY = {"SHALL": "high", "SHALL NOT": "high", "SHOULD": "moderate"}
STATES = {"satisfied": "PASS", "not-satisfied": "FAIL"}
PRIORITY_ORDER = {"high": 0, "moderate": 1}


@dataclass(frozen=True)
class Finding:
    control_id: str        # policy id as SCuBA writes it, e.g. MS.AAD.7.4v1
    title: str
    group: str             # catalog group title, e.g. "Privileged Access"
    obligation: str        # SHALL | SHALL NOT | SHOULD
    status: str            # PASS | FAIL | NOT ASSESSED
    priority: str | None   # high | moderate for failures, None otherwise
    requirement: str       # catalog statement
    rationale: str         # catalog guidance: why the control matters
    finding: str           # ScubaGear's details for this tenant
    evidence: str          # where the verdict came from (report, product, group, raw result)
    scuba_result: str      # ScubaGear's own word: Pass | Fail | Warning | "" if not assessed
    remediation: str       # catalog remediation steps, HTML stripped
    nist: list[str]        # related NIST SP 800-53 controls from the catalog links

    def to_dict(self):
        return asdict(self)


def strip_html(text):
    """Keep the words and markdown links; drop HTML tags such as <pre> and <b>."""
    text = re.sub(r"<br\s*/?>", "\n", text or "")
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _prop(props, name, default=""):
    return next((p["value"] for p in props or [] if p["name"] == name), default)


def load_findings(catalog_path=CATALOG, results_path=RESULTS):
    """Every catalog control as a Finding, in catalog order."""
    catalog = _read(catalog_path)["catalog"]
    result = _read(results_path)["assessment-results"]["results"][0]
    observations = {o["uuid"]: o for o in result.get("observations", [])}
    by_target = {f["target"]["target-id"]: f for f in result.get("findings", [])}

    out = []
    for group in catalog["groups"]:
        for c in group["controls"]:
            parts = {p["name"]: p for p in c["parts"]}
            obligation = _prop(c["props"], "obligation")
            f = by_target.get(parts["statement"]["id"])
            if f is None:
                status, details, evidence, scuba_result = "NOT ASSESSED", "", "", ""
            else:
                state = f["target"]["status"]["state"]
                if state not in STATES:
                    raise ValueError(f"{c['id']}: unexpected finding state {state!r}")
                status, details = STATES[state], f.get("description", "")
                obs = [observations[r["observation-uuid"]] for r in f.get("related-observations", [])]
                evidence = " ".join(e["description"] for o in obs for e in o.get("relevant-evidence", []))
                scuba_result = next((_prop(o.get("props"), "scuba-result") for o in obs), "")
            out.append(Finding(
                control_id=_prop(c["props"], "label", c["id"]), title=c["title"], group=group["title"],
                obligation=obligation, status=status,
                priority=PRIORITY.get(obligation) if status == "FAIL" else None,
                requirement=parts["statement"]["prose"], rationale=parts["guidance"]["prose"],
                finding=details, evidence=evidence, scuba_result=scuba_result,
                remediation=strip_html(parts["remediation"]["prose"]),
                nist=[link["text"] for link in c.get("links", []) if link.get("rel") == "related"]))
    return out


def load_assessment_info(results_path=RESULTS):
    """Scan-level facts safe to show the AI: tenant display name, scan time, tool version.

    The tenant id and report uuid are left out on purpose; the AI does not need them."""
    result = _read(results_path)["assessment-results"]["results"][0]
    props = result.get("props")
    return dict(tenant=_prop(props, "tenant-name"), domain=_prop(props, "tenant-domain"),
                scan_time=result.get("start", ""), tool_version=_prop(props, "scuba-tool-version"))


def summarize(findings):
    """Counts the dashboard and the AI both use, so they can never disagree."""
    count = lambda s: sum(f.status == s for f in findings)  # noqa: E731
    return dict(assessed=count("PASS") + count("FAIL"), passed=count("PASS"), failed=count("FAIL"),
                not_assessed=count("NOT ASSESSED"), total=len(findings))


def prioritized_failures(findings):
    """Failed controls, high priority (SHALL / SHALL NOT) before moderate (SHOULD), catalog order within."""
    return sorted((f for f in findings if f.status == "FAIL"), key=lambda f: PRIORITY_ORDER[f.priority])
