import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from sarif_summary import summarize

ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    return yaml.safe_load((ROOT / f".github/workflows/{name}.yml").read_text())


def step(job, action):
    return next(s for s in job["steps"] if s.get("uses", "").startswith(action + "@"))


def commands(job):
    return "\n".join(s.get("run", "") for s in job["steps"])


@pytest.mark.parametrize("name", ["tests", "quality", "security"])
def test_workflow_contract(name):
    config = workflow(name)
    assert config["name"] == name
    triggers = config.get("on", config.get(True))
    assert triggers["push"] == {"branches": ["master"]}
    assert triggers["pull_request"] in (None, {}, {"branches": ["**"]})
    assert config["permissions"] == {"contents": "read"}
    assert config["concurrency"] == {
        "group": "${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}",
        "cancel-in-progress": True,
    }
    if name == "security":
        assert triggers["schedule"] == [{"cron": "17 6 * * 1"}]
        assert "workflow_dispatch" in triggers
    raw = (ROOT / f".github/workflows/{name}.yml").read_text()
    for job_id, job in config["jobs"].items():
        assert job["runs-on"] == "ubuntu-latest"
        assert 0 < job["timeout-minutes"] <= 20
        if job_id != "codeql":
            assert job.get("permissions", {"contents": "read"}) == {"contents": "read"}
        assert step(job, "actions/checkout")["with"]["persist-credentials"] is False
        for entry in job["steps"]:
            if "uses" in entry:
                action, sha = entry["uses"].split("@")
                assert re.fullmatch(r"[0-9a-f]{40}", sha)
                assert re.search(rf"uses: {re.escape(action)}@{sha} # v\d+\.\d+\.\d+", raw)


def test_tests_workflow():
    job = workflow("tests")["jobs"]["pytest"]
    assert job["strategy"] == {"fail-fast": False, "matrix": {"python-version": ["3.11", "3.13"]}}
    assert step(job, "actions/setup-python")["with"] == {
        "python-version": "${{ matrix.python-version }}",
        "cache": "pip",
        "cache-dependency-path": "backend/requirements*.txt",
    }
    runs = commands(job)
    assert "python -m pip install -r backend/requirements-dev.txt" in runs
    assert "python -m pytest --cov --cov-report=term-missing --cov-report=xml --cov-fail-under=80" in runs
    assert "TOTAL" in runs and "GITHUB_STEP_SUMMARY" in runs
    assert "TOOL_SECRET" not in str(workflow("tests"))
    upload = step(job, "actions/upload-artifact")
    assert upload["if"] == "always()"
    assert upload["with"] == {
        "name": "coverage-py${{ matrix.python-version }}",
        "path": "coverage.xml",
        "retention-days": 14,
    }


def test_quality_workflow():
    job = workflow("quality")["jobs"]["ruff"]
    assert step(job, "actions/setup-python")["with"]["python-version"] == "3.13"
    runs = commands(job)
    assert "pip install -r backend/requirements-dev.txt" in runs
    assert "ruff check --output-format=github ." in runs
    assert "ruff format --check ." in runs


def test_security_workflow():
    jobs = workflow("security")["jobs"]
    assert set(jobs) == {"codeql", "pip-audit", "gitleaks"}
    codeql = jobs["codeql"]
    assert codeql["permissions"] == {"contents": "read", "actions": "read", "security-events": "write"}
    assert step(codeql, "github/codeql-action/init")["with"] == {
        "languages": "python",
        "build-mode": "none",
        "queries": "security-and-quality",
    }
    assert step(codeql, "github/codeql-action/analyze")["with"] == {
        "category": "/language:python",
        "upload": "${{ vars.CODE_SCANNING_UPLOAD == 'true' && 'always' || 'never' }}",
        "output": "sarif-results",
    }
    upload = step(codeql, "actions/upload-artifact")
    assert upload["if"] == "always()"
    assert upload["with"]["name"] == "codeql-sarif"
    assert upload["with"]["path"] == "sarif-results"
    assert "python .github/scripts/sarif_summary.py" in commands(codeql)
    audit = jobs["pip-audit"]
    assert step(audit, "actions/setup-python")["with"]["python-version"] == "3.13"
    assert "pip install -r backend/requirements-dev.txt" in commands(audit)
    assert "pip-audit --strict --desc -r backend/requirements-dev.txt" in commands(audit)
    leaks = jobs["gitleaks"]
    assert step(leaks, "actions/checkout")["with"]["fetch-depth"] == 0
    runs = commands(leaks)
    for text in (
        "https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/",
        "gitleaks_8.30.1_linux_x64.tar.gz",
        "gitleaks_8.30.1_checksums.txt",
        "sha256sum --check --ignore-missing",
        "./gitleaks git --redact --verbose --exit-code 1 --report-format sarif --report-path gitleaks.sarif .",
    ):
        assert text in runs
    upload = step(leaks, "actions/upload-artifact")
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == "gitleaks.sarif"


def test_dependabot():
    config = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())
    assert config["version"] == 2
    assert len(config["updates"]) == 2
    for entry, ecosystem, directory, prefix in zip(
        config["updates"], ["pip", "github-actions"], ["/backend", "/"], ["deps", "ci"], strict=True
    ):
        assert entry["package-ecosystem"] == ecosystem
        assert entry["directory"] == directory
        assert entry["schedule"] == {"interval": "weekly", "day": "monday"}
        assert entry["open-pull-requests-limit"] == 5
        assert entry["commit-message"]["prefix"] == prefix
        assert list(entry["groups"].values()) == [{"update-types": ["minor", "patch"]}]


def test_sarif_summary(tmp_path):
    path = tmp_path / "tiny.sarif"
    results = [
        {
            "ruleId": "danger",
            "level": "error",
            "message": {"text": "Bad thing\nMore"},
            "locations": [
                {"physicalLocation": {"artifactLocation": {"uri": "backend/main.py"}, "region": {"startLine": 12}}}
            ],
        },
        {"ruleId": "note", "level": "warning", "message": {"text": "Heads up"}},
    ]
    path.write_text(json.dumps({"runs": [{"results": results}]}))
    markdown, errors = summarize([path])
    assert errors == 1
    for text in ("error: 1", "warning: 1", "danger", "backend/main.py:12", "Bad thing", "Heads up"):
        assert text in markdown
    assert "More" not in markdown
    results.extend({"ruleId": f"extra{i}", "message": {"text": "Info"}} for i in range(25))
    path.write_text(json.dumps({"runs": [{"results": results}]}))
    markdown, errors = summarize([path])
    assert "extra17" in markdown and "extra18" not in markdown
    assert "warning: 26" in markdown
    assert summarize([])[1] == 0


@pytest.mark.parametrize("level, exit_code", [("error", 1), ("warning", 0)])
def test_sarif_main(tmp_path, level, exit_code):
    reports = tmp_path / "sarif-results"
    reports.mkdir()
    (reports / "report.sarif").write_text(
        json.dumps({"runs": [{"results": [{"level": level, "message": {"text": "Finding"}}]}]})
    )
    summary = tmp_path / "summary.md"
    summary.write_text("Existing summary\n")
    result = subprocess.run(
        [sys.executable, str(ROOT / ".github/scripts/sarif_summary.py")],
        cwd=tmp_path,
        env={"GITHUB_STEP_SUMMARY": str(summary)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == exit_code, result.stderr
    assert summary.read_text().startswith("Existing summary\n## CodeQL SARIF")
    assert f"{level}: 1" in summary.read_text()


@pytest.mark.parametrize("reference", [{"ruleIndex": 0}, {"ruleId": "danger"}])
def test_sarif_rule_default_level(tmp_path, reference):
    path = tmp_path / "rules.sarif"
    rule = {"id": "danger", "defaultConfiguration": {"level": "error"}}
    run = {"tool": {"driver": {"rules": [rule]}}, "results": [reference]}
    path.write_text(json.dumps({"runs": [run]}))
    markdown, errors = summarize([path])
    assert errors == 1
    assert "error: 1" in markdown


def test_sarif_main_rule_default_level(tmp_path, monkeypatch):
    from sarif_summary import main

    reports = tmp_path / "sarif-results"
    reports.mkdir()
    rule = {"id": "danger", "defaultConfiguration": {"level": "error"}}
    run = {"tool": {"driver": {"rules": [rule]}}, "results": [{"ruleIndex": 0}]}
    (reports / "report.sarif").write_text(json.dumps({"runs": [run]}))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.chdir(tmp_path)
    assert main() == 1
    assert "error: 1" in summary.read_text()


@pytest.mark.parametrize("kind", ["pass", "notApplicable"])
def test_sarif_non_fail_results_ignored(tmp_path, kind):
    path = tmp_path / "non-failure.sarif"
    path.write_text(json.dumps({"runs": [{"results": [{"kind": kind, "level": "error"}]}]}))
    markdown, errors = summarize([path])
    assert errors == 0
    assert "No results found." in markdown
    assert "- unknown" not in markdown


@pytest.mark.parametrize(
    "result, expected",
    [
        ({"ruleId": "extension-rule"}, "error"),
        ({"rule": {"index": 0, "toolComponent": {"index": 0}}}, "error"),
        ({"rule": {"index": 0, "toolComponent": {"name": "extension"}}}, "error"),
        ({"rule": {"index": 0, "toolComponent": {"guid": "extension-guid"}}}, "error"),
        ({"ruleIndex": 0, "level": "note"}, "note"),
        ({"ruleIndex": 0}, "warning"),
        ({"ruleId": "missing"}, "warning"),
        ({"ruleIndex": 99}, "warning"),
        ({"ruleIndex": -1}, "warning"),
        ({"kind": "fail"}, "warning"),
    ],
)
def test_sarif_severity_resolution(tmp_path, result, expected):
    path = tmp_path / "severity.sarif"
    tool = {
        "driver": {"rules": [{"id": "driver-rule"}]},
        "extensions": [
            {
                "name": "extension",
                "guid": "extension-guid",
                "rules": [{"id": "extension-rule", "defaultConfiguration": {"level": "error"}}],
            }
        ],
    }
    path.write_text(json.dumps({"runs": [{"tool": tool, "results": [result]}]}))
    markdown, errors = summarize([path])
    assert f"- {expected}: 1" in markdown
    assert errors == (1 if expected == "error" else 0)
