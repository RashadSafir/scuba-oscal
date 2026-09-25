"""The POA&M's plan per control, and the POA&M with the target dates people set.

Plain code, no AI. make_poam.py gives every item three undated milestones; a person's target date
becomes the item's risk deadline and the date of its last milestone ("Verify with a ScubaGear rescan").
"""
import json


def poam_plan(poam_doc):
    """{control id: {"item_uuid", "risk_uuids", "milestones": [title]}} from this scan's POA&M."""
    poam = (poam_doc or {}).get("plan-of-action-and-milestones", {})
    risks = {r["uuid"]: r for r in poam.get("risks", [])}
    plan = {}
    for item in poam.get("poam-items", []):
        cid = item["title"].split(":")[0]
        risk_ids = [r["risk-uuid"] for r in item.get("related-risks", [])]
        milestones = [t["title"] for rid in risk_ids for rem in risks.get(rid, {}).get("remediations", [])
                      for t in rem.get("tasks", []) if t.get("type") == "milestone"]
        plan[cid] = {"item_uuid": item["uuid"], "risk_uuids": risk_ids, "milestones": milestones}
    return plan


def poam_with_dates(doc, targets):
    """The POA&M with each person-set target date as its risk's deadline and the last milestone's date."""
    if not targets:
        return doc
    doc = json.loads(json.dumps(doc))
    poam = doc["plan-of-action-and-milestones"]
    risks = {r["uuid"]: r for r in poam.get("risks", [])}
    for item in poam.get("poam-items", []):
        target = targets.get(item["title"].split(":")[0])
        if not target:
            continue
        when = f"{target.isoformat()}T23:59:59+00:00"
        for rr in item.get("related-risks", []):
            risk = risks.get(rr["risk-uuid"])
            if not risk:
                continue
            risk["deadline"] = when
            for rem in risk.get("remediations", []):
                milestones = [t for t in rem.get("tasks", []) if t.get("type") == "milestone"]
                if milestones:
                    milestones[-1]["timing"] = {"on-date": {"date": when}}
    return doc
