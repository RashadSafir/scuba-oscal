import uuid
from datetime import datetime, timezone
from pathlib import Path
from trestle.oscal.catalog import Catalog, Control, Group2 as Group  # trestle 5.x: Group2 = group that holds controls
from trestle.oscal.common import Metadata, Part, Property, Link

NS = uuid.UUID("6f1c2d3e-0000-4000-8000-5c0ba0000001")   # fixed namespace -> deterministic uuids

def cid(policy_id):          # "MS.AAD.1.1v1" -> "ms.aad.1.1v1"
    return policy_id.lower()

ctrl = Control(
    id=cid("MS.AAD.1.1v1"),
    title="Block legacy authentication",
    props=[Property(name="obligation", value="SHALL", ns="https://scuba.example/ns"),
           Property(name="label", value="MS.AAD.1.1v1")],
    links=[Link(href="https://raw.githubusercontent.com/usnistgov/oscal-content/main/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json#cm-7",
                rel="related", text="NIST SP 800-53 Rev 5 CM-7")],
    parts=[
        Part(id="ms.aad.1.1v1_smt", name="statement",
             prose="Legacy authentication SHALL be blocked."),
        Part(id="ms.aad.1.1v1_gdn", name="guidance",
             prose="Legacy authentication protocols do not support MFA. Blocking them reduces the impact of user credential theft."),
    ],
)

cat = Catalog(
    uuid=str(uuid.uuid5(NS, "catalog:ms.aad")),
    metadata=Metadata(title="CISA SCuBA Microsoft Entra ID (MS.AAD) Baseline",
                      last_modified=datetime.now(timezone.utc), version="0.1.0", oscal_version="1.1.2"),
    groups=[Group(id="ms.aad.1", title="Legacy Authentication", controls=[ctrl])],
)
cat.oscal_write(Path("catalogs/scuba-aad/catalog.json"))
print("wrote catalogs/scuba-aad/catalog.json")
