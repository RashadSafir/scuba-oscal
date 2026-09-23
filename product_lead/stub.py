"""Stub backend so the frontend runs before the real AI layer exists.

Implements the same contract as the real `capability` module:
    answer(question: str) -> {"text": str, "citations": [{"id": str, "kind": str}, ...]}
    generate_report() -> str  (markdown)

An optional "evidence" key (list of OSCAL snippet strings) is included for the
"Show evidence" expander; the frontend treats it as optional.
"""

from datetime import date

# The 11 in-scope SCuBA Entra ID controls: (id, meaning, obligation, result)
CONTROLS = [
    ("MS.AAD.1.1v1", "Block legacy auth", "Shall", "PASS"),
    ("MS.AAD.2.1v1", "Block high-risk users", "Shall", "PASS"),
    ("MS.AAD.2.3v1", "Block high-risk sign-ins", "Shall", "PASS"),
    ("MS.AAD.3.1v1", "Phishing-resistant MFA, all users", "Shall", "FAIL"),
    ("MS.AAD.3.8v1", "Managed devices to register MFA", "Should", "FAIL"),
    ("MS.AAD.3.9v1", "Block device code auth", "Should", "FAIL"),
    ("MS.AAD.5.2v1", "Restrict user app consent", "Shall", "FAIL"),
    ("MS.AAD.6.1v1", "Passwords shall not expire", "Shall", "FAIL"),
    ("MS.AAD.7.1v1", "Global Admin 2–8 users", "Shall", "PASS"),
    ("MS.AAD.7.4v1", "No permanent privileged assignments", "Shall", "FAIL"),
    ("MS.AAD.8.1v1", "Limit guest access", "Should", "PASS"),
]


def _cite(*ids: str) -> list[dict]:
    return [{"id": cid, "kind": "control"} for cid in ids]


def _evidence(control_id: str) -> str:
    _, meaning, obligation, result = next(c for c in CONTROLS if c[0] == control_id)
    state = "satisfied" if result == "PASS" else "not-satisfied"
    return (
        "{\n"
        f'  "finding": {{\n'
        f'    "title": "{control_id} — {meaning}",\n'
        f'    "target": {{\n'
        f'      "type": "objective-id",\n'
        f'      "target-id": "{control_id.lower()}_obj",\n'
        f'      "status": {{ "state": "{state}" }}\n'
        "    },\n"
        f'    "props": [{{ "name": "obligation", "value": "{obligation}" }}]\n'
        "  }\n"
        "}"
    )


_BIGGEST_RISK = (
    "Your biggest identity risk is that **phishing-resistant MFA is not enforced for all users** "
    "(`MS.AAD.3.1v1`, a **Shall** requirement — currently **FAIL**).\n\n"
    "Without phishing-resistant methods (FIDO2 keys, Windows Hello for Business, or "
    "certificate-based auth), accounts remain exposed to adversary-in-the-middle phishing "
    "and MFA-fatigue attacks. This is the single control most likely to prevent account takeover.\n\n"
    "**Good news:** your baseline sign-in protections are in place — legacy authentication is "
    "blocked (`MS.AAD.1.1v1`) and high-risk sign-ins are blocked (`MS.AAD.2.3v1`)."
)

_FIX_FIRST = (
    "Prioritize failed **Shall** requirements (mandatory) before failed **Should** requirements "
    "(recommended). Recommended order:\n\n"
    "**Shall — fix first**\n\n"
    "1. **`MS.AAD.3.1v1` — Enforce phishing-resistant MFA for all users.** Highest impact; "
    "directly blocks credential phishing.\n"
    "2. **`MS.AAD.7.4v1` — Remove permanent privileged role assignments.** Move admins to "
    "just-in-time activation via PIM to shrink the blast radius of a compromised account.\n"
    "3. **`MS.AAD.5.2v1` — Restrict user consent to applications.** Prevents illicit "
    "consent-grant attacks by malicious OAuth apps.\n"
    "4. **`MS.AAD.6.1v1` — Set passwords to never expire.** Low effort; aligns with NIST "
    "SP 800-63B guidance.\n\n"
    "**Should — fix next**\n\n"
    "5. **`MS.AAD.3.9v1` — Block device code authentication flow.** Closes a common phishing vector.\n"
    "6. **`MS.AAD.3.8v1` — Require managed devices to register MFA.** Hardens MFA enrollment.\n\n"
    "That's **4 failed Shalls** and **2 failed Shoulds** — 6 findings in total."
)

_MFA = (
    "MFA is your **weakest area** — 3 MFA-related controls are in scope, and all three are "
    "currently failing:\n\n"
    "| Control | Requirement | Obligation | Result |\n"
    "|---|---|---|---|\n"
    "| `MS.AAD.3.1v1` | Phishing-resistant MFA for all users | Shall | :red-badge[FAIL] |\n"
    "| `MS.AAD.3.8v1` | Managed devices to register MFA | Should | :red-badge[FAIL] |\n"
    "| `MS.AAD.3.9v1` | Block device code auth | Should | :red-badge[FAIL] |\n\n"
    "The most urgent gap is `MS.AAD.3.1v1` because it is a **Shall**. Supporting controls are "
    "healthy: legacy authentication — which bypasses MFA entirely — is blocked (`MS.AAD.1.1v1`)."
)

_DEFAULT = (
    "I answer questions using your tenant's **OSCAL assessment results** for the 11 in-scope "
    "SCuBA Entra ID (MS.AAD) controls — **5 passing, 6 failing**.\n\n"
    "Try asking about your biggest risk, what to fix first, or a specific area such as MFA, "
    "privileged access, or guest access."
)


def answer(question: str) -> dict:
    q = question.lower()
    if "risk" in q:
        ids = ("MS.AAD.3.1v1", "MS.AAD.1.1v1", "MS.AAD.2.3v1")
        text = _BIGGEST_RISK
    elif "fix" in q or "first" in q or "priorit" in q:
        ids = ("MS.AAD.3.1v1", "MS.AAD.7.4v1", "MS.AAD.5.2v1", "MS.AAD.6.1v1",
               "MS.AAD.3.9v1", "MS.AAD.3.8v1")
        text = _FIX_FIRST
    elif "mfa" in q or "multi-factor" in q or "multifactor" in q:
        ids = ("MS.AAD.3.1v1", "MS.AAD.3.8v1", "MS.AAD.3.9v1", "MS.AAD.1.1v1")
        text = _MFA
    else:
        return {"text": _DEFAULT, "citations": []}
    return {
        "text": text,
        "citations": _cite(*ids),
        "evidence": [_evidence(cid) for cid in ids],
    }


def generate_report() -> str:
    failing = [c for c in CONTROLS if c[3] == "FAIL"]
    failing.sort(key=lambda c: c[2] != "Shall")  # Shalls first
    passing = len(CONTROLS) - len(failing)

    lines = [
        "# SCuBA Compliance Report — Microsoft Entra ID",
        "",
        f"_Generated {date.today().isoformat()} from OSCAL assessment results._",
        "",
        "## Summary",
        "",
        f"- Controls assessed: **{len(CONTROLS)}**",
        f"- Passing: **{passing}**",
        f"- Failing: **{len(failing)}**",
        "",
        "## Failing controls (prioritized)",
        "",
        "| Control | Requirement | Obligation |",
        "|---|---|---|",
    ]
    lines += [f"| {cid} | {meaning} | {obl} |" for cid, meaning, obl, _ in failing]
    lines += [
        "",
        "## Recommendation",
        "",
        "Remediate failed **Shall** requirements first, starting with MS.AAD.3.1v1 "
        "(phishing-resistant MFA), then address failed **Should** requirements.",
        "",
    ]
    return "\n".join(lines)
