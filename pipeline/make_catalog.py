"""Build the SCuBA MS.AAD OSCAL catalog from aad.md.

Inputs : vendor/ScubaGear/PowerShell/ScubaGear/baselines/aad.md
         vendor/NIST_SP-800-53_rev5_catalog.json   (used to check every 800-53 link target exists)
Output : oscal/catalog.json
Contract: docs/CONTRACTS.md

ID convention (team contract, do not change):
  control id   = policy id lowercased           MS.AAD.1.1v1 -> ms.aad.1.1v1
  statement id = control id + "_smt"            ms.aad.1.1v1_smt   (findings point here)
  guidance id  = control id + "_gdn"            ms.aad.1.1v1_gdn   (rationale: why it matters)
  remediation  = control id + "_rem"            ms.aad.1.1v1_rem   (how to fix: the aad.md Instructions)
  group id     = "ms.aad." + section number     ms.aad.1
  obligation   = prop name="obligation", value SHALL | SHALL NOT | SHOULD
"""
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from trestle.oscal.catalog import Catalog, Control, Group2 as Group
from trestle.oscal.common import Link, Metadata, Part, Property

# ---- The policies our team chose, with short display titles ----
SCOPE = {
    "MS.AAD.1.1v1": "Block legacy authentication",
    "MS.AAD.2.1v1": "Block high-risk users",
    "MS.AAD.2.3v1": "Block high-risk sign-ins",
    "MS.AAD.3.8v1": "Require managed devices to register MFA",
    "MS.AAD.5.2v1": "Restrict user consent to applications",
    "MS.AAD.7.1v1": "Limit Global Administrators to 2-8 users",
    "MS.AAD.7.4v1": "No permanent active privileged role assignments",
    "MS.AAD.8.1v1": "Limit guest access to directory objects",
}

NS = uuid.UUID("6f1c2d3e-0000-4000-8000-5c0ba0000001")  # fixed -> same uuid every run
PROP_NS = "https://scuba.example/ns"
NIST = ("https://raw.githubusercontent.com/usnistgov/oscal-content/main/"
        "nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json")
FIXED_TIME = datetime(2026, 9, 23, tzinfo=timezone.utc)  # fixed so reruns give identical files

HERE = Path(__file__).resolve().parent          # pipeline/
ROOT = HERE.parent                              # repo root
AAD_MD = ROOT / "vendor" / "ScubaGear" / "PowerShell" / "ScubaGear" / "baselines" / "aad.md"
NIST_CATALOG = ROOT / "vendor" / "NIST_SP-800-53_rev5_catalog.json"
OUT = ROOT / "oscal" / "catalog.json"


# ---------- Step 1: read aad.md into simple records ----------
def parse_aad(text):
    sections = {}  # "1" -> "Legacy Authentication"
    records = []
    section = None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^## (\d+)\. (.+)$", line)
        if m:
            section = m.group(1)
            sections[section] = m.group(2).strip()
            continue
        m = re.match(r"^####\s+(MS\.AAD\.\d+\.\d+v\d+)\s*$", line)  # policy heading (not "Instructions")
        if not m:
            continue
        pid = m.group(1)
        # the statement is the first non-blank line after the heading
        statement = next(l.strip() for l in lines[i + 1:] if l.strip())
        # read the block until the next heading
        block = []
        for l in lines[i + 1:]:
            if l.startswith("#"):
                break
            block.append(l)
        block = "\n".join(block)
        rationale = re.search(r"_Rationale:_\s*(.+)", block).group(1).strip()
        nist_line = re.search(r"Baseline Mapping:_\s*(.+)", block).group(1)
        nist = [x.strip() for x in nist_line.split(",") if x.strip()]
        if "SHALL NOT" in statement:
            obligation = "SHALL NOT"
        elif "SHALL" in statement:
            obligation = "SHALL"
        else:
            obligation = "SHOULD"
        records.append(dict(id=pid, section=section, statement=statement,
                            rationale=rationale, obligation=obligation, nist_80053=nist))
    instructions = parse_instructions(lines)
    for r in records:
        r["remediation"] = instructions.get(r["id"], "")
    return sections, records


def parse_instructions(lines):
    """Text under each '#### MS.AAD.x.yvN Instructions' heading, up to the next heading.
    Lines starting with '#' inside ``` code blocks (e.g. PowerShell comments) are not headings."""
    found = {}
    current, buf, in_code = None, [], False
    def flush():
        if current:
            found[current] = "\n".join(buf).strip()
    for line in lines:
        if line.startswith("```"):
            in_code = not in_code
        if not in_code and line.startswith("#"):
            flush()
            m = re.match(r"^####\s+(MS\.AAD\.\d+\.\d+v\d+)\s+Instructions\s*$", line)
            current, buf = (m.group(1) if m else None), []
            continue
        if current:
            buf.append(line)
    flush()
    return found


# ---------- Step 2: turn records into OSCAL ----------
def nist_anchor(ref):
    """'AC-2(12)' -> 'ac-2.12', 'AC-20b' -> 'ac-20', 'CM-7' -> 'cm-7'."""
    m = re.match(r"^([A-Za-z]{2})-(\d+)(?:\((\d+)\))?", ref)
    fam, num, enh = m.groups()
    return f"{fam.lower()}-{num}" + (f".{enh}" if enh else "")


def load_nist_ids(path):
    """Every control and enhancement id in the NIST 800-53 Rev 5 catalog (e.g. 'ac-2', 'ac-2.12')."""
    ids = set()
    def walk(node):
        for c in node.get("controls", []):
            ids.add(c["id"])
            walk(c)
        for g in node.get("groups", []):
            walk(g)
    walk(json.loads(path.read_text(encoding="utf-8"))["catalog"])
    return ids


def build_control(r):
    cid = r["id"].lower()
    return Control(
        id=cid,
        title=SCOPE[r["id"]],
        props=[Property(name="obligation", value=r["obligation"], ns=PROP_NS),
               Property(name="label", value=r["id"])],
        links=[Link(href=f"{NIST}#{nist_anchor(n)}", rel="related",
                    text=f"NIST SP 800-53 Rev 5 {n}") for n in r["nist_80053"]],
        parts=[Part(id=f"{cid}_smt", name="statement", prose=r["statement"]),
               Part(id=f"{cid}_gdn", name="guidance", prose=r["rationale"]),
               Part(id=f"{cid}_rem", name="remediation", ns=PROP_NS, prose=r["remediation"])],
    )


def main():
    for f in (AAD_MD, NIST_CATALOG):
        if not f.exists():
            raise SystemExit(f"Missing input: {f.relative_to(ROOT)}")
    sections, records = parse_aad(AAD_MD.read_text(encoding="utf-8"))
    nist_ids = load_nist_ids(NIST_CATALOG)
    chosen = [r for r in records if r["id"] in SCOPE]
    missing = set(SCOPE) - {r["id"] for r in chosen}
    if missing:
        raise SystemExit(f"Not found in aad.md (check version suffix): {sorted(missing)}")
    for r in chosen:
        if not r["remediation"]:
            raise SystemExit(f"{r['id']}: no Instructions section found in aad.md")
        bad = [n for n in r["nist_80053"] if nist_anchor(n) not in nist_ids]
        if bad:
            raise SystemExit(f"{r['id']}: 800-53 ids not in NIST catalog: {bad}")

    groups = []
    for sec in sorted({r["section"] for r in chosen}, key=int):
        controls = [build_control(r) for r in chosen if r["section"] == sec]
        groups.append(Group(id=f"ms.aad.{sec}", title=sections[sec], controls=controls))

    cat = Catalog(
        uuid=str(uuid.uuid5(NS, "catalog:ms.aad")),
        metadata=Metadata(title="CISA SCuBA Microsoft Entra ID (MS.AAD) Baseline",
                          last_modified=FIXED_TIME, version="0.3.0", oscal_version="1.1.2"),
        groups=groups,
    )
    out = OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    cat.oscal_write(out)
    for r in chosen:
        print(f"  {r['id']:14} {r['obligation']:9} -> {r['id'].lower()}_smt")
    print(f"wrote {out.relative_to(ROOT)} ({len(chosen)} controls)")


if __name__ == "__main__":
    main()