"""The "Fix first" order: a transparent score per failed control, from config/fix_first.toml.

Plain code, no AI. score(f) = obligation + exposure + 800-53 family + effort points; the reason
names every factor that scored. Controls one change fixes together (the config's "related" lists)
are shown as one item, ranked by the best-scoring control in it.
"""
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "config" / "fix_first.toml"
FAMILY = re.compile(r"\b([A-Z]{2})-\d")   # "NIST SP 800-53 Rev 5 AC-6(5)" -> AC


def load_config(path=CONFIG):
    return tomllib.loads(Path(path).read_text(encoding="utf-8"))


def families(f):
    """NIST SP 800-53 families of a control's related controls, e.g. ["AC", "IA"]."""
    return list(dict.fromkeys(m[1] for n in f.nist for m in [FAMILY.search(n)] if m))


@dataclass
class Score:
    points: int
    factors: list = field(default_factory=list)   # [(label, points)] for the factors that scored

    @property
    def reason(self):
        return ", ".join(label for label, _ in self.factors) or "no weighted factors"


def score(f, cfg):
    factors = []
    ob = cfg["obligation"].get(f.obligation, 0)
    factors.append(("Required (SHALL)" if ob else "Recommended (SHOULD)", ob))
    text = f" {f.title} {f.group} ".lower()
    exposure = max((e for e in cfg.get("exposure", []) if any(w in text for w in e["words"])),
                   key=lambda e: e["points"], default=None)
    if exposure:
        factors.append((exposure["label"], exposure["points"]))
    fams = [(fam, cfg.get("family", {}).get(fam, 0)) for fam in families(f)]
    fam, pts = max(fams, key=lambda x: x[1], default=(None, 0))
    if pts:
        factors.append((f"800-53 {fam}", pts))
    effort = cfg.get("effort", {})
    kind = effort.get("controls", {}).get(f.control_id)
    if kind:
        factors.append((f"{kind} fix", effort.get("points", {}).get(kind, 0)))
    return Score(sum(p for _, p in factors), factors)


@dataclass
class Item:
    """One row of the Fix first list: a single control, or controls one change fixes together."""
    name: str | None          # the related group's name, None for a single control
    findings: list            # failed controls in this item, best score first
    score: Score              # the best control's score


def fix_first(failures, cfg=None):
    """Failed controls as Fix first items, highest score first (ties keep catalog order)."""
    cfg = cfg or load_config()
    scores = {f.control_id: score(f, cfg) for f in failures}
    group_of = {cid: g["name"] for g in cfg.get("related", []) for cid in g["controls"]}
    items, seen = [], {}
    for f in failures:
        name = group_of.get(f.control_id)
        if name and name in seen:
            seen[name].findings.append(f)
            continue
        item = Item(name, [f], scores[f.control_id])
        items.append(item)
        if name:
            seen[name] = item
    for item in items:
        item.findings.sort(key=lambda f: -scores[f.control_id].points)
        item.score = scores[item.findings[0].control_id]
        if len(item.findings) == 1:
            item.name = None   # a related group with one failure is just that control
    order = {id(i): n for n, i in enumerate(items)}
    return sorted(items, key=lambda i: (-i.score.points, order[id(i)])), scores
