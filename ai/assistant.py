"""AI layer: answers questions about the assessment using the verified Finding records.

The AI never decides pass/fail. Code supplies the facts (findings.py); the model only explains,
analyses, prioritises within the fixed order, and summarises. Every reply keeps the two apart:

  answer(question, history=None) -> {
      "text":        str   AI-generated analysis (Markdown). Label it as AI output in the UI.
      "verified":    list  Finding dicts for the controls the answer cites, copied from OSCAL.
                           Show these as the facts behind the answer.
      "citations":   list  [{"id", "kind", "status", "title", "finding_uuid", "observation_uuid"}]
                           for the same controls ("" uuids for controls that were not assessed)
      "unverified_references": list  control ids the model mentioned that are NOT in the
                           assessment (e.g. a SCuBA policy outside our scope). Warn about these.
  }

Credentials come from the environment (.env via python-dotenv): AZURE_OPENAI_ENDPOINT,
AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT. They are only read, never logged or returned.

Try it from the command line:  python -m ai.assistant "What should we fix first?"
                               python -m ai.assistant --findings path/to/findings.json "..."
"""
import argparse
import json
import os
import re

from . import findings as fm
from .prompts import COMPLIANCE_REPORT_REQUEST, EXECUTIVE_SUMMARY_REQUEST, EXPLAIN_REQUEST, build_messages

ENV_VARS = ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT")
TIMEOUT_SECONDS = 60   # per request; a hung call fails instead of freezing the app
MAX_RETRIES = 2        # the client retries rate limits, timeouts and 5xx errors with backoff
HISTORY_TURNS = 6   # earlier messages sent along for follow-up questions ("why does that matter?")
CONTROL_ID = re.compile(r"\bMS\.[A-Z]+\.\d+\.\d+v\d+\b", re.IGNORECASE)


def make_client():
    """(client, deployment) for Azure OpenAI, configured the same way as scripts/check_llm.py."""
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv()
    missing = [v for v in ENV_VARS if not os.environ.get(v)]
    if missing:
        raise RuntimeError(f"Azure OpenAI is not configured; set {', '.join(missing)} in .env")
    client = OpenAI(base_url=os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/") + "/openai/v1/",
                    api_key=os.environ["AZURE_OPENAI_API_KEY"], timeout=TIMEOUT_SECONDS,
                    max_retries=MAX_RETRIES)
    return client, os.environ["AZURE_OPENAI_DEPLOYMENT"]


def health_check(client=None, model=None):
    """Is the AI actually reachable? Sends one tiny request. Returns {"ok": bool, "detail": str}.
    detail names the error type only, never credentials."""
    try:
        if client is None:
            client, model = make_client()
        r = client.chat.completions.create(model=model, messages=[{"role": "user", "content": "Reply with: ok"}])
        return {"ok": bool(r.choices), "detail": "connected"}
    except RuntimeError as e:          # not configured
        return {"ok": False, "detail": str(e)}
    except Exception as e:             # network, auth, wrong deployment name, quota ...
        return {"ok": False, "detail": f"{type(e).__name__}: the AI service did not answer"}


def _strings(value):
    """A list of non-empty strings from a JSON value that should be a string or a list of strings."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]


def parse_report(text, failed_ids):
    """The report JSON asked for by COMPLIANCE_REPORT_REQUEST, cleaned up, or None if unusable.

    Tolerates a reply wrapped in ```json fences or with text around the object. Findings keyed by
    anything other than a failed control's id are dropped: the analysis may only explain failures."""
    body = re.sub(r"^\s*```(?:json)?|```\s*$", "", text or "").strip()
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        data = json.loads(body[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    by_id = {cid.lower(): cid for cid in failed_ids}
    raw_findings = data.get("findings") if isinstance(data.get("findings"), dict) else {}
    findings = {}
    for key, value in raw_findings.items():
        cid = by_id.get(str(key).strip().strip("`").lower())
        if cid and isinstance(value, dict):
            findings[cid] = {"why_it_matters": " ".join(_strings(value.get("why_it_matters"))),
                             "how_to_fix": _strings(value.get("how_to_fix"))}
    report = {"executive_summary": _strings(data.get("executive_summary")), "findings": findings,
              "next_steps": _strings(data.get("next_steps"))}
    if not (report["executive_summary"] or findings or report["next_steps"]):
        return None
    return report


class Assistant:
    def __init__(self, findings_path=fm.FINDINGS, findings=None, info=None, client=None, model=None):
        """findings/info default to what findings_path (oscal/findings.json) holds. client/model default
        to Azure OpenAI from .env, created on first use (so building an Assistant never needs credentials)."""
        self.findings = findings if findings is not None else fm.load_findings(findings_path)
        self.info = info if info is not None else fm.load_assessment_info(findings_path)
        self.summary = fm.summarize(self.findings)
        self._by_id = {f.control_id.lower(): f for f in self.findings}
        self._client, self._model = client, model

    def _complete(self, messages):
        if self._client is None:
            self._client, self._model = make_client()
        r = self._client.chat.completions.create(model=self._model, messages=messages)
        return (r.choices[0].message.content or "").strip()

    def _reply(self, text):
        """Attach the verified records for every control id the text mentions, in order of mention."""
        cited, unknown = [], []
        for m in CONTROL_ID.finditer(text):
            f = self._by_id.get(m.group(0).lower())
            if f is None:
                unknown.append(m.group(0))
            elif f not in cited:
                cited.append(f)
        return {
            "text": text,
            "verified": [f.to_dict() for f in cited],
            "citations": [self._citation(f) for f in cited],
            "unverified_references": list(dict.fromkeys(unknown)),
        }

    @staticmethod
    def _citation(f):
        return dict(id=f.control_id, kind="control", status=f.status, title=f.title,
                    finding_uuid=f.finding_uuid, observation_uuid=f.observation_uuid)

    def answer(self, question, history=None):
        """Answer a free-form question. history: earlier [{"role": "user"|"assistant", "content"}]."""
        question = (question or "").strip()
        if not question:
            raise ValueError("question is empty")
        turns = [{"role": m["role"], "content": m["content"]} for m in (history or [])
                 if m.get("role") in ("user", "assistant") and m.get("content")][-HISTORY_TURNS:]
        return self._reply(self._complete(build_messages(question, self.findings, self.summary,
                                                         self.info, turns)))

    def explain(self, control_id):
        """"Ask AI about this finding": explanation, risk and fix for one control."""
        f = self._by_id.get(control_id.lower())
        if f is None:
            raise KeyError(f"{control_id} is not in the assessment")
        reply = self.answer(EXPLAIN_REQUEST.format(control_id=f.control_id))
        if f.to_dict() not in reply["verified"]:   # always show the facts for the control asked about
            reply["verified"].insert(0, f.to_dict())
            reply["citations"].insert(0, self._citation(f))
        return reply

    def executive_summary(self):
        return self.answer(EXECUTIVE_SUMMARY_REQUEST)

    def compliance_report(self):
        """The AI's analysis for the compliance report. Same reply shape as answer(), plus "report":
        {"executive_summary": [paragraph], "findings": {control id: {"why_it_matters": str,
        "how_to_fix": [step]}}, "next_steps": [action]}, or None when the model's reply is not the
        JSON asked for. Only failed controls can appear in "findings"; the facts stay with the caller."""
        failed = [f.control_id for f in fm.prioritized_failures(self.findings)]
        reply = self.answer(COMPLIANCE_REPORT_REQUEST.format(failed_ids=", ".join(failed) or "none"))
        reply["report"] = parse_report(reply["text"], failed)
        return reply


_default = None


def default_assistant():
    """Shared Assistant over oscal/findings.json. Call reload() after findings.json is regenerated."""
    global _default
    if _default is None:
        _default = Assistant()
    return _default


def reload():
    global _default
    _default = None


def answer(question, history=None):
    return default_assistant().answer(question, history)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Ask the AI about a SCuBA assessment.")
    ap.add_argument("--findings", default=fm.FINDINGS, help="findings.json from comparison/compare_oscal.py")
    ap.add_argument("question", nargs="*")
    args = ap.parse_args()
    q = " ".join(args.question) or "Give me an executive summary of our Entra ID security posture."
    reply = Assistant(args.findings).answer(q)
    print(reply["text"], "\n")
    print("Verified facts behind this answer (from OSCAL):")
    for f in reply["verified"]:
        print(f"  {f['control_id']:14} {f['status']:12} {f['obligation']:9} {f['title']}")
    if reply["unverified_references"]:
        print("Not in this assessment, treat with care:", ", ".join(reply["unverified_references"]))
