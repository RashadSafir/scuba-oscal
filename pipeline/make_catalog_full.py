"""Build an OSCAL catalog of EVERY policy in the SCuBA MS.AAD baseline (aad.md).

Same format and id convention as make_catalog.py (which builds the team's 8 scoped
controls into oscal/catalog.json). This script includes all policies and writes a
separate file, so the team's scoped catalog is untouched.

Inputs : vendor/ScubaGear/PowerShell/ScubaGear/baselines/aad.md
         vendor/NIST_SP-800-53_rev5_catalog.json
Output : oscal/Controls/EntraID-catalog-full.json
Run    : python pipeline/make_catalog_full.py
"""
import uuid

from trestle.oscal.catalog import Catalog, Control, Group2 as Group
from trestle.oscal.common import Link, Metadata, Part, Property

# Reuse the exact parsing and settings from the scoped script, so both catalogs match
from make_catalog import (AAD_MD, FIXED_TIME, NIST, NIST_CATALOG, NS, PROP_NS, ROOT,
                          load_nist_ids, nist_anchor, parse_aad)

OUT = ROOT / "oscal" / "Controls" / "EntraID-catalog-full.json"

# Short display titles for every policy. A policy CISA adds later that isn't listed
# here still gets built; its rule sentence is used as the title until one is added.
TITLES = {
    "MS.AAD.1.1v1": "Block legacy authentication",
    "MS.AAD.2.1v1": "Block high-risk users",
    "MS.AAD.2.2v1": "Notify admins of high-risk users",
    "MS.AAD.2.3v1": "Block high-risk sign-ins",
    "MS.AAD.3.1v1": "Phishing-resistant MFA for all users",
    "MS.AAD.3.2v2": "MFA for all users",
    "MS.AAD.3.3v2": "Show login context in Microsoft Authenticator",
    "MS.AAD.3.4v1": "Complete authentication methods migration",
    "MS.AAD.3.5v2": "Disable SMS, voice call, and email OTP",
    "MS.AAD.3.6v1": "Phishing-resistant MFA for privileged roles",
    "MS.AAD.3.7v1": "Require managed devices for authentication",
    "MS.AAD.3.8v1": "Require managed devices to register MFA",
    "MS.AAD.3.9v1": "Block device code authentication",
    "MS.AAD.4.1v1": "Send security logs to the SOC",
    "MS.AAD.5.1v1": "Only admins register applications",
    "MS.AAD.5.2v1": "Restrict user consent to applications",
    "MS.AAD.5.3v1": "Configure admin consent workflow",
    "MS.AAD.5.5v1": "Block application password addition",
    "MS.AAD.5.6v1": "Limit application password lifetime to 180 days",
    "MS.AAD.5.7v1": "Limit application certificate lifetime to 365 days",
    "MS.AAD.6.1v1": "User passwords do not expire",
    "MS.AAD.7.1v1": "Limit Global Administrators to 2-8 users",
    "MS.AAD.7.2v1": "Use finer-grained roles instead of Global Administrator",
    "MS.AAD.7.3v1": "Cloud-only accounts for privileged users",
    "MS.AAD.7.4v1": "No permanent active privileged role assignments",
    "MS.AAD.7.5v1": "Provision privileged roles only through PAM",
    "MS.AAD.7.6v1": "Require approval for Global Administrator activation",
    "MS.AAD.7.7v1": "Alert on privileged role assignments",
    "MS.AAD.7.8v1": "Alert on Global Administrator activation",
    "MS.AAD.7.9v1": "Alert on other privileged role activation",
    "MS.AAD.8.1v1": "Limit guest access to directory objects",
    "MS.AAD.8.2v1": "Only Guest Inviters can invite guests",
    "MS.AAD.8.3v1": "Allow guest invites only to approved domains",
    "MS.AAD.9.1v1": "Block risky AI agents",
}


def build_control(r):
    cid = r["id"].lower()
    return Control(
        id=cid,
        title=TITLES.get(r["id"], r["statement"]),
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
    for r in records:
        if not r["remediation"]:
            raise SystemExit(f"{r['id']}: no Instructions section found in aad.md")
        bad = [n for n in r["nist_80053"] if nist_anchor(n) not in nist_ids]
        if bad:
            raise SystemExit(f"{r['id']}: 800-53 ids not in NIST catalog: {bad}")
    untitled = [r["id"] for r in records if r["id"] not in TITLES]
    if untitled:
        print(f"  note: no short title yet for {untitled}; using the rule sentence")

    groups = []
    for sec in sorted({r["section"] for r in records}, key=int):
        controls = [build_control(r) for r in records if r["section"] == sec]
        groups.append(Group(id=f"ms.aad.{sec}", title=sections[sec], controls=controls))

    cat = Catalog(
        uuid=str(uuid.uuid5(NS, "catalog:ms.aad:full")),
        metadata=Metadata(title="CISA SCuBA Microsoft Entra ID (MS.AAD) Baseline - all policies",
                          last_modified=FIXED_TIME, version="0.3.0", oscal_version="1.1.2"),
        groups=groups,
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    cat.oscal_write(OUT)
    for r in records:
        print(f"  {r['id']:14} {r['obligation']:9} -> {r['id'].lower()}_smt")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(records)} controls in {len(groups)} sections)")


if __name__ == "__main__":
    main()