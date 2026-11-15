"""Summarize local CodeQL SARIF reports without requiring code scanning uploads."""

import html
import json
import os
from collections import Counter
from pathlib import Path


def summarize(paths):
    """Return Markdown for all result counts and the first 20 findings, plus error count."""
    counts = Counter()
    findings = []
    for path in sorted(paths):
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        for run in report.get("runs", []):
            for result in run.get("results", []):
                level = result.get("level", "warning")
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
