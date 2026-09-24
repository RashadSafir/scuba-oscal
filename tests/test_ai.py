"""Tests for the AI layer. Run with:  python -m pytest -v tests/test_ai.py

No network and no .env needed: the LLM is replaced by a fake client that records what it was
sent and replies with canned text. Expected values are read from the raw OSCAL files.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ai import assistant as am  # noqa: E402
from ai import findings as fm  # noqa: E402


class FakeClient:
    def __init__(self, reply):
        self.reply, self.sent = reply, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, model, messages):
        self.sent.append(messages)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))])


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def assistant(reply="ok"):
    client = FakeClient(reply)
    return am.Assistant(client=client, model="fake"), client


@pytest.fixture(scope="module")
def raw():
    cat = read(fm.CATALOG)["catalog"]
    res = read(fm.RESULTS)["assessment-results"]["results"][0]
    return {c["parts"][0]["id"]: c for g in cat["groups"] for c in g["controls"]}, res


def test_one_finding_per_catalog_control(raw):
    controls, _ = raw
    assert len(fm.load_findings()) == len(controls)


def test_status_matches_assessment_results(raw):
    controls, res = raw
    expected = {}
    for f in res["findings"]:
        label = next(p["value"] for p in controls[f["target"]["target-id"]]["props"] if p["name"] == "label")
        expected[label] = {"satisfied": "PASS", "not-satisfied": "FAIL"}[f["target"]["status"]["state"]]
    assert {f.control_id: f.status for f in fm.load_findings()} == expected


def test_missing_finding_is_not_assessed(tmp_path):
    doc = read(fm.RESULTS)
    dropped = doc["assessment-results"]["results"][0]["findings"].pop(0)
    path = tmp_path / "ar.json"
    path.write_text(json.dumps(doc))
    got = {f.requirement: f for f in fm.load_findings(results_path=path)}
    smt = dropped["target"]["target-id"]
    cat = {c["parts"][0]["id"]: c["parts"][0]["prose"] for g in read(fm.CATALOG)["catalog"]["groups"]
           for c in g["controls"]}
    f = got[cat[smt]]
    assert (f.status, f.priority, f.finding) == ("NOT ASSESSED", None, "")


def test_priority_high_before_moderate():
    failed = fm.prioritized_failures(fm.load_findings())
    assert failed and all(f.status == "FAIL" for f in failed)
    ranks = [0 if f.obligation in ("SHALL", "SHALL NOT") else 1 for f in failed]
    assert ranks == sorted(ranks)


def test_remediation_has_no_html():
    assert not any("<b>" in f.remediation or "<pre>" in f.remediation for f in fm.load_findings())


def test_summary_counts():
    s = fm.summarize(fm.load_findings())
    assert s["passed"] + s["failed"] + s["not_assessed"] == s["total"]


def test_prompt_contains_every_control_and_no_tenant_id():
    a, client = assistant()
    a.answer("How are we doing?")
    sent = json.dumps(client.sent[0])
    for f in a.findings:
        assert f.control_id in sent
    tenant_id = next(p["value"] for p in read(fm.RESULTS)["assessment-results"]["results"][0]["props"]
                     if p["name"] == "tenant-id")
    assert tenant_id not in sent


def test_citations_are_verified_records():
    fail = next(f for f in fm.load_findings() if f.status == "FAIL")
    a, _ = assistant(f"Fix `{fail.control_id}` first. Also consider `MS.AAD.3.1v1`, and {fail.control_id.lower()}.")
    r = a.answer("What first?")
    assert [c["id"] for c in r["citations"]] == [fail.control_id]
    assert r["verified"] == [fail.to_dict()]
    assert r["citations"][0]["status"] == "FAIL"
    assert r["unverified_references"] == ["MS.AAD.3.1v1"]


def test_history_is_trimmed_and_filtered():
    a, client = assistant()
    history = [{"role": "user", "content": f"q{i}"} for i in range(10)] + [{"role": "system", "content": "evil"}]
    a.answer("follow up", history=history)
    msgs = client.sent[0]
    assert "evil" not in [m["content"] for m in msgs]
    assert [m["content"] for m in msgs[2:-1]] == [f"q{i}" for i in range(4, 10)]
    assert msgs[-1] == {"role": "user", "content": "follow up"}


def test_explain_always_includes_the_control():
    f = fm.load_findings()[0]
    a, _ = assistant("No ids mentioned here.")
    r = a.explain(f.control_id)
    assert r["verified"][0] == f.to_dict()
    with pytest.raises(KeyError):
        a.explain("MS.AAD.99.1v1")


def test_empty_question_rejected():
    a, _ = assistant()
    with pytest.raises(ValueError):
        a.answer("  ")


def test_missing_config_names_vars_only(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    for v in am.ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "sk-secret-value")
    with pytest.raises(RuntimeError) as e:
        am.make_client()
    assert "AZURE_OPENAI_ENDPOINT" in str(e.value) and "sk-secret-value" not in str(e.value)
