"""Prometheus metrics export for Guardian."""

from prometheus_client import Counter, Gauge, Histogram

PRS_ANALYZED = Counter("guardian_prs_total", "Total PRs analyzed")
FILES_SCANNED = Counter("guardian_files_scanned_total", "Total files scanned")
VIOLATIONS_FOUND = Counter("guardian_violations_total", "Total violations found", ["rule"])
FALSE_POSITIVES = Counter("guardian_false_positives_total", "Total false positives reported")
AI_CODE_PCT = Gauge("guardian_ai_code_percentage", "Percentage of AI-attributed code")
ANALYSIS_DURATION = Histogram("guardian_analysis_duration_seconds", "Analysis duration in seconds")
