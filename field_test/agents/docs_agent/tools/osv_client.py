from __future__ import annotations
import json
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

CVE_PATTERN = re.compile(r"CVE-\d{4}-\d+")
OSV_QUERY_URL = "https://api.osv.dev/v1/query"


def extract_cve_references(message: str) -> list[str]:
    return list(set(CVE_PATTERN.findall(message)))


class OSVClient:
    def __init__(self, timeout: float = 15.0):
        self._client = httpx.Client(timeout=timeout)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self) -> None:
        self._client.close()

    def extract_cve_references(self, message: str) -> list[str]:
        return list(set(CVE_PATTERN.findall(message)))

    def query_cve(self, cve_id: str) -> dict[str, Any] | None:
        try:
            resp = self._client.post(
                OSV_QUERY_URL,
                json={"id": cve_id},
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code >= 400:
                logger.warning("OSV.dev returned %d for %s. Skipping.", resp.status_code, cve_id)
                return None
            data = resp.json()
            vulns = data.get("vulns", [])
            if not vulns:
                logger.warning("No vulnerability data for %s", cve_id)
                return None
            return _extract_vuln_summary(vulns[0])
        except httpx.RequestError as e:
            logger.warning("OSV.dev unreachable: %s. Skipping CVE %s.", e, cve_id)
            return None
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("OSV.dev returned malformed JSON for %s: %s", cve_id, e)
            return None

    def draft_advisory(self, cve_data: dict[str, Any], llm_client: Any) -> str:
        prompt = (
            f"CVE ID: {cve_data.get('id', 'Unknown')}\n"
            f"Summary: {cve_data.get('summary', 'N/A')}\n"
            f"Severity: {cve_data.get('severity', 'N/A')}\n"
            f"Affected: {cve_data.get('affected', 'N/A')}\n"
            f"References: {cve_data.get('references', 'N/A')}\n\n"
            "Write a brief security advisory for the release notes. "
            "Describe the vulnerability, affected versions, and mitigation steps."
        )
        try:
            return llm_client.complete(
                system_prompt="You are a security advisory writer. Write concise, clear advisories for release notes.",
                user_prompt=prompt,
                max_tokens=512,
            )
        except Exception as e:
            logger.warning("Advisory draft failed: %s", e)
            return f"Advisory unavailable. See {cve_data.get('id', 'CVE')} for details."


def _extract_vuln_summary(vuln: dict[str, Any]) -> dict[str, Any]:
    aliases = vuln.get("aliases", [])
    cve_id = next((a for a in aliases if a.startswith("CVE-")), aliases[0] if aliases else "Unknown")

    severity = "unknown"
    database_specific = vuln.get("database_specific", {})
    if database_specific:
        severity = database_specific.get("severity", "unknown")

    affected_packages = []
    for affected in vuln.get("affected", []):
        pkg = affected.get("package", {})
        affected_packages.append({
            "package": pkg.get("name", "unknown"),
            "ecosystem": pkg.get("ecosystem", "unknown"),
            "ranges": affected.get("ranges", []),
        })

    references = [r.get("url", "") for r in vuln.get("references", [])]

    return {
        "id": cve_id,
        "summary": vuln.get("summary", "No summary available"),
        "severity": severity,
        "affected": affected_packages,
        "references": references[:3],
    }
