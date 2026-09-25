"""Build one OSCAL catalog PER PRODUCT for EVERY SCuBA baseline in the ScubaGear repo
(Entra ID, Defender, Exchange Online, Power BI, Power Platform, Security Suite, SharePoint,
Teams, and any baseline CISA adds later), and write each one into oscal/Controls/.

The app merges every file in oscal/Controls into one catalog, so each product gets exactly
one file and every policy appears exactly once.

Every file has the same layout:  catalog -> one group per section (ms.exo.1) -> controls
and the same id convention as make_catalog.py:
  control id   = policy id lowercased      MS.EXO.1.1v2 -> ms.exo.1.1v2
  statement    = control id + "_smt"       (findings point here)
  guidance     = control id + "_gdn"       (rationale: why it matters)
  remediation  = control id + "_rem"       (how to fix)
  obligation   = prop, SHALL | SHALL NOT | SHOULD | SHOULD NOT
                 (SHOULD NOT only appears outside Entra ID, e.g. in Teams)

Inputs : every *.md in vendor/ScubaGear/PowerShell/ScubaGear/baselines/ (except removedpolicies.md)
         vendor/NIST_SP-800-53_rev5_catalog.json
Output : oscal/Controls/<Product>-catalog-full.json, e.g. oscal/Controls/EntraID-catalog-full.json
Run    : python pipeline/make_catalog_full.py
"""
import re                                      # "regular expressions": finds text patterns like "#### MS.EXO.1.1v2"
import uuid                                    # makes the catalog's unique id

from trestle.oscal.catalog import Catalog, Control, Group2 as Group  # OSCAL building blocks
from trestle.oscal.common import Link, Metadata, Part, Property      # more OSCAL building blocks

# Reuse the shared settings and helpers from the scoped script, so every catalog uses identical rules
from make_catalog import (FIXED_TIME, NIST, NIST_CATALOG, NS, PROP_NS, ROOT,
                          load_nist_ids, nist_anchor)

BASELINES = ROOT / "vendor" / "ScubaGear" / "PowerShell" / "ScubaGear" / "baselines"  # folder with the rulebooks
SKIP = {"removedpolicies.md"}                  # this file lists retired policies, not current ones, so leave it out
OUT_DIR = ROOT / "oscal" / "Controls"          # the folder the app and AI read catalogs from

# File-name prefix for each product, matching the team's "EntraID-catalog-full.json" style.
# A product CISA adds later that isn't listed here gets its code as the name, e.g. "Newproduct".
FILE_NAMES = {
    "aad": "EntraID",
    "defender": "Defender",
    "exo": "ExchangeOnline",
    "powerbi": "PowerBI",
    "powerplatform": "PowerPlatform",
    "securitysuite": "SecuritySuite",
    "sharepoint": "SharePoint",
    "teams": "Teams",
}

POLICY_ID = r"MS\.[A-Z]+\.\d+\.\d+v\d+"         # pattern for any policy id, e.g. MS.AAD.1.1v1 or MS.POWERBI.2.1v1

# Short display titles for the Entra ID policies (the team's product). Every other policy
# uses its rule sentence as its title. A policy CISA adds later that isn't listed here
# still gets built, with its rule sentence as the title until a short one is added.
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


# ---------- Step 1: read one baseline file into simple records ----------
def parse_baseline(text):
    """Return (product title, {section number: section name}, [one record per policy])."""
    lines = text.splitlines()                  # split the file into a list of lines

    # the first line starting with "# " is the document title, e.g.
    # "# CISA M365 Secure Configuration Baseline for Exchange Online" -> "Exchange Online"
    title_line = next(l for l in lines if l.startswith("# "))
    product_title = re.sub(r"^# CISA M365 Secure Configuration (Baseline for )?", "", title_line)
    product_title = re.sub(r"\s+Baseline$", "", product_title.strip())   # "Security Suite Baseline" -> "Security Suite"

    sections, records, section = {}, [], None  # empty containers to fill as we read
    for i, line in enumerate(lines):           # go through the file one line at a time
        m = re.match(r"^## (\d+)\. (.+)$", line)            # is this a section heading like "## 1. External Sharing"?
        if m:
            section = m.group(1)                             # remember the section number...
            sections[section] = m.group(2).strip()           # ...and its name
            continue                                         # move on to the next line
        m = re.match(rf"^####\s+({POLICY_ID})\s*$", line)   # is this a policy heading like "#### MS.EXO.1.1v2"?
        if not m:
            continue                                         # not a policy heading, skip it
        pid = m.group(1)                                     # the policy id, e.g. "MS.EXO.1.1v2"

        # the rule sentence is the first non-blank line after the heading
        statement = next(l.strip() for l in lines[i + 1:] if l.strip())

        # collect the policy's block of text, up to the next heading
        block = []
        for l in lines[i + 1:]:
            if l.startswith("#"):                            # a new heading means this policy's block has ended
                break
            block.append(l)
        block = "\n".join(block)                             # glue the block back into one piece of text

        rationale = re.search(r"_Rationale:_\s*(.+)", block).group(1).strip()      # the "why it matters" line
        nist_line = re.search(r"Baseline Mapping:_\s*(.+)", block).group(1)        # the NIST 800-53 mapping line
        nist = [x.strip() for x in nist_line.split(",") if x.strip()]               # split "AC-2, CM-7" into a list

        # work out how strict the rule is; check the "NOT" versions first, because
        # "SHALL NOT" also contains "SHALL" and "SHOULD NOT" also contains "SHOULD"
        if "SHALL NOT" in statement:
            obligation = "SHALL NOT"
        elif "SHOULD NOT" in statement:
            obligation = "SHOULD NOT"          # appears in some non-AAD baselines, e.g. MS.TEAMS.1.1v1
        elif "SHALL" in statement:
            obligation = "SHALL"
        else:
            obligation = "SHOULD"

        records.append(dict(id=pid, section=section, statement=statement, rationale=rationale,
                            obligation=obligation, nist_80053=nist))   # save everything about this policy

    fixes = parse_instructions(lines)                        # find every policy's "how to fix" steps
    for r in records:
        r["remediation"] = fixes.get(r["id"], "")             # attach the fix steps to their policy
    return product_title, sections, records


def parse_instructions(lines):
    """Return {policy id: fix steps} from each '#### MS.X.y.zvN Instructions' section."""
    found, current, buf, in_code = {}, None, [], False

    def flush():                               # save the text collected so far for the current policy
        if current:
            found[current] = "\n".join(buf).strip()

    for line in lines:
        if line.startswith("```"):             # lines starting with ``` open or close a code example...
            in_code = not in_code              # ...and "#" inside code (e.g. a PowerShell comment) is NOT a heading
        if not in_code and line.startswith("#"):              # a real heading: the previous section has ended
            flush()
            m = re.match(rf"^####\s+({POLICY_ID})\s+Instructions\s*$", line)   # is it an "Instructions" heading?
            current, buf = (m.group(1) if m else None), []   # start collecting for that policy (or stop collecting)
            continue
        if current:
            buf.append(line)                   # keep collecting fix-step lines
    flush()                                    # save the last one at the end of the file
    return found


# ---------- Step 2: turn one record into an OSCAL control ----------
def build_control(r):
    cid = r["id"].lower()                      # control id, e.g. "MS.EXO.1.1v2" -> "ms.exo.1.1v2"
    return Control(
        id=cid,
        title=TITLES.get(r["id"], r["statement"]),          # short title if we have one, otherwise the rule sentence
        props=[Property(name="obligation", value=r["obligation"], ns=PROP_NS),   # SHALL / SHALL NOT / SHOULD / SHOULD NOT
               Property(name="label", value=r["id"])],                          # the original id, uppercase
        links=[Link(href=f"{NIST}#{nist_anchor(n)}", rel="related",           # one link per NIST 800-53 control
                    text=f"NIST SP 800-53 Rev 5 {n}") for n in r["nist_80053"]],
        parts=[Part(id=f"{cid}_smt", name="statement", prose=r["statement"]),  # the rule itself
               Part(id=f"{cid}_gdn", name="guidance", prose=r["rationale"]),   # why it matters
               Part(id=f"{cid}_rem", name="remediation", ns=PROP_NS, prose=r["remediation"])],   # how to fix
    )


# ---------- Step 3: read every baseline, check it, and write one catalog per product ----------
def main():
    if not BASELINES.exists():                 # stop early with a clear message if inputs are missing
        raise SystemExit(f"Missing input folder: {BASELINES.relative_to(ROOT)} (run scripts/fetch_sources.sh)")
    if not NIST_CATALOG.exists():
        raise SystemExit(f"Missing input: {NIST_CATALOG.relative_to(ROOT)} (run scripts/fetch_sources.sh)")
    nist_ids = load_nist_ids(NIST_CATALOG)     # every real 800-53 id, used to check our links
    OUT_DIR.mkdir(parents=True, exist_ok=True) # make sure oscal/Controls exists

    files, total = 0, 0
    for path in sorted(BASELINES.glob("*.md")):              # every rulebook file, in alphabetical order
        if path.name in SKIP:
            continue                                         # skip the retired-policies file
        product_title, sections, records = parse_baseline(path.read_text(encoding="utf-8"))
        if not records:
            continue                                         # a file with no policies isn't a baseline, skip it

        # safety checks: every policy must have fix steps and only real NIST ids
        for r in records:
            if not r["remediation"]:
                raise SystemExit(f"{r['id']}: no Instructions section found in {path.name}")
            bad = [n for n in r["nist_80053"] if nist_anchor(n) not in nist_ids]
            if bad:
                raise SystemExit(f"{r['id']}: 800-53 ids not in NIST catalog: {bad}")

        product = records[0]["id"].split(".")[1].lower()     # "MS.EXO.1.1v2" -> "exo"

        # one group per section, e.g. ms.exo.1 "Automatic Forwarding to External Domains"
        groups = []
        for sec in sorted({r["section"] for r in records}, key=int):
            controls = [build_control(r) for r in records if r["section"] == sec]
            groups.append(Group(id=f"ms.{product}.{sec}", title=sections[sec], controls=controls))

        cat = Catalog(
            uuid=str(uuid.uuid5(NS, f"catalog:ms.{product}:full")),   # fixed seed -> same id every run
            metadata=Metadata(title=f"CISA SCuBA {product_title} (MS.{product.upper()}) Baseline - all policies",
                              last_modified=FIXED_TIME, version="0.3.0", oscal_version="1.1.2"),
            groups=groups,
        )
        name = FILE_NAMES.get(product, product.capitalize())  # e.g. "exo" -> "ExchangeOnline"
        out = OUT_DIR / f"{name}-catalog-full.json"
        cat.oscal_write(out)                                 # write this product's catalog file
        files += 1
        total += len(records)
        print(f"  {path.name:20} -> {out.relative_to(ROOT)}  ({len(records)} policies)")

    print(f"wrote {files} catalogs to {OUT_DIR.relative_to(ROOT)} ({total} controls)")


if __name__ == "__main__":                     # runs main() when you do: python pipeline/make_catalog_full.py
    main()
    