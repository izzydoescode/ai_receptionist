"""Summarize local CodeQL SARIF reports without requiring code scanning uploads."""

import html
import json
import os
from collections import Counter
from pathlib import Path


def rule_components(tool, reference):
    """Select a referenced tool component, or search the driver and extensions."""
    extensions = tool.get("extensions", [])
    if not reference:
        return [tool.get("driver", {}), *extensions]
    index = reference.get("index")
    if isinstance(index, int) and 0 <= index < len(extensions):
        return [extensions[index]]
    return [
        component
        for component in [tool.get("driver", {}), *extensions]
        if any(reference.get(key) and reference[key] == component.get(key) for key in ("name", "guid"))
    ]


def result_level(run, result):
    """Resolve explicit severity, then the referenced rule, then SARIF's default."""
    if "level" in result:
        return result["level"]
    reference = result.get("rule", {})
    components = rule_components(run.get("tool", {}), reference.get("toolComponent"))
    index = result.get("ruleIndex", reference.get("index"))
    rule_id = result.get("ruleId", reference.get("id"))
    for component in components:
        rules = component.get("rules", [])
        if index is not None:
            if isinstance(index, int) and 0 <= index < len(rules):
                return rules[index].get("defaultConfiguration", {}).get("level", "warning")
        elif rule_id is not None:
            for rule in rules:
                if rule.get("id") == rule_id:
                    return rule.get("defaultConfiguration", {}).get("level", "warning")
    return "warning"


def summarize(paths):
    """Return Markdown for all result counts and the first 20 findings, plus error count."""
    counts = Counter()
    findings = []
    for path in sorted(paths):
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        for run in report.get("runs", []):
            for result in run.get("results", []):
                if result.get("kind", "fail") != "fail":
                    continue
                level = result_level(run, result)
                counts[level] += 1
                if len(findings) >= 20:
                    continue
                locations = result.get("locations", [])
                physical = locations[0].get("physicalLocation", {}) if locations else {}
                file = physical.get("artifactLocation", {}).get("uri", "unknown")
                line = physical.get("region", {}).get("startLine", "?")
                message = result.get("message", {}).get("text", "").splitlines()
                text = message[0] if message else ""
                # Escape report text so it cannot introduce Markdown markup or HTML.
                finding = f"{result.get('ruleId', 'unknown')} — {file}:{line} — {text}"
                findings.append(f"- {html.escape(finding)}")
    lines = ["## CodeQL SARIF", ""]
    lines.extend(f"- {level}: {count}" for level, count in sorted(counts.items()))
    if not counts:
        lines.append("No results found.")
    lines.extend(["", "### First 20 findings", "", *findings])
    return "\n".join(lines) + "\n", counts["error"]


def main():
    markdown, error_count = summarize(Path("sarif-results").glob("*.sarif"))
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
        summary.write(markdown)
    return 1 if error_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
