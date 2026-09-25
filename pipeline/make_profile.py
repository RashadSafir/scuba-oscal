"""Build an OSCAL Profile: the controls from our catalog that are in scope for an assessment.

Input  : a SCuBA OSCAL catalog      e.g. oscal/Controls/EntraID-catalog-full.json (the default), or
                                     several products' catalogs combined into one (as the app does)
Output : profile.json                imports the catalog and selects the in-scope controls

A profile is how OSCAL tailors a catalog. By default every catalog control is selected. With
--obligation SHALL only the required policies are (SHALL and SHALL NOT), which shows tailoring:
the same catalog, a narrower baseline.

Ids come from a hash of the catalog plus the selection, so the same scope always gives the
same profile uuid.
"""
import argparse
import hashlib
import json
import uuid
from pathlib import Path

from trestle.oscal.common import Link, Metadata
from trestle.oscal.profile import Import2, Merge2, Profile, SelectControl

from make_assessment_results import NS, OSCAL_VERSION, now_utc, rel_href

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_CATALOG = ROOT / "oscal" / "Controls" / "EntraID-catalog-full.json"
DEFAULT_OUT = ROOT / "oscal" / "profile.json"


def catalog_controls(catalog_path):
    """(catalog title, [(control id, obligation)]) in catalog order."""
    cat = json.loads(Path(catalog_path).read_text(encoding="utf-8-sig"))["catalog"]
    controls = [(c["id"], next((p["value"] for p in c.get("props", []) if p["name"] == "obligation"), ""))
                for g in cat["groups"] for c in g["controls"]]
    return cat["metadata"]["title"], controls


def build(catalog_path, out_dir=".", obligation=None, href=None):
    """The profile; `obligation` = "SHALL" keeps only required policies. `href` overrides the catalog link."""
    title, controls = catalog_controls(catalog_path)
    selected = [cid for cid, ob in controls if obligation is None or ob.startswith(obligation)]
    if not selected:
        raise SystemExit(f"No catalog controls have obligation {obligation!r}")
    digest = hashlib.sha256(Path(catalog_path).read_bytes()).hexdigest()
    scope = "all" if obligation is None else obligation
    href = href or rel_href(catalog_path, out_dir)
    return Profile(
        uuid=str(uuid.uuid5(NS, f"profile:{digest}:{scope}")),
        metadata=Metadata(
            title=f"SCuBA Profile ({'all policies' if obligation is None else obligation + ' policies'}): {title}",
            last_modified=now_utc(), version="0.1.0", oscal_version=OSCAL_VERSION,
            links=[Link(href=href, rel="reference", text=title)],
            remarks=f"Selects {len(selected)} of {len(controls)} controls from the catalog."),
        imports=[Import2(href=href, include_controls=[SelectControl(with_ids=selected)])],
        merge=Merge2(as_is=True))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--obligation", choices=["SHALL", "SHOULD"], default=None,
                    help="select only policies with this obligation (default: all)")
    args = ap.parse_args()
    if not args.catalog.exists():
        raise SystemExit(f"Missing input: {args.catalog}")
    profile = build(args.catalog, out_dir=args.out.parent, obligation=args.obligation)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    profile.oscal_write(args.out)
    print(f"wrote {args.out}  ({len(profile.imports[0].include_controls[0].with_ids)} controls)")


if __name__ == "__main__":
    main()
