"""Metrics collector — computes dashboard stats from audit log."""

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from guardian.audit.logger import AuditLogger


class MetricsCollector:
    def __init__(self, audit_logger: AuditLogger):
        self.entries = audit_logger.read()

    @property
    def total_prs(self) -> int:
        return len({e.pr_number for e in self.entries if e.pr_number is not None})

    @property
    def ai_prs(self) -> int:
        ai_prs: set[int] = set()
        for e in self.entries:
            if e.pr_number is not None and e.detection_results:
                if any(d.confidence != "low" for d in e.detection_results):
                    ai_prs.add(e.pr_number)
        return len(ai_prs)

    @property
    def ai_code_pct(self) -> float:
        total_files = 0
        ai_files = 0
        for e in self.entries:
            if e.detection_results:
                for d in e.detection_results:
                    total_files += 1
                    if d.confidence != "low":
                        ai_files += 1
        if total_files == 0:
            return 0.0
        return round(ai_files / total_files * 100, 1)

    @property
    def violations_by_rule(self) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for e in self.entries:
            if e.violations:
                for v in e.violations:
                    counts[v.rule_name] += 1
        return dict(counts)

    @property
    def violation_trends(self) -> list[dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        daily: defaultdict[str, int] = defaultdict(int)
        for e in self.entries:
            if e.action != "analysis":
                continue
            ts = self._parse_ts(e.timestamp)
            if ts is None or ts < cutoff:
                continue
            day = ts.strftime("%Y-%m-%d")
            if e.violations:
                daily[day] += len(e.violations)
        return [{"date": d, "count": c} for d, c in sorted(daily.items())]

    @property
    def false_positive_rate(self) -> float:
        confirmed = 0
        false_positives = 0
        for e in self.entries:
            if e.feedback == "confirmed":
                confirmed += 1
            elif e.feedback == "disputed":
                false_positives += 1
        total = confirmed + false_positives
        if total == 0:
            return 0.0
        return round(false_positives / total, 3)

    @property
    def recent_prs(self) -> list[dict]:
        prs: dict[int, dict] = {}
        for e in sorted(self.entries, key=lambda x: x.timestamp, reverse=True):
            if e.pr_number is None:
                continue
            if e.pr_number not in prs:
                prs[e.pr_number] = {
                    "pr_number": e.pr_number,
                    "timestamp": e.timestamp,
                    "risk_level": e.risk_level,
                    "violation_count": 0,
                }
            if e.action == "analysis" and e.violations:
                prs[e.pr_number]["violation_count"] += len(e.violations)
        return list(prs.values())[:20]

    def to_dict(self) -> dict:
        return {
            "total_prs": self.total_prs,
            "ai_prs": self.ai_prs,
            "ai_code_pct": self.ai_code_pct,
            "violations_by_rule": self.violations_by_rule,
            "violation_trends": self.violation_trends,
            "false_positive_rate": self.false_positive_rate,
            "recent_prs": self.recent_prs,
        }

    @staticmethod
    def _parse_ts(ts: str) -> datetime | None:
        try:
            return datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            return None
