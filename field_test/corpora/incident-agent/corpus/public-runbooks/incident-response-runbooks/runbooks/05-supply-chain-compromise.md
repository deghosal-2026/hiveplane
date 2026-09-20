# Supply Chain Compromise (Dependency or Build Pipeline)

**Trigger:** Vendor advisory (e.g., SolarWinds-style notification), suspicious behavior from third-party package, integrity check fails on a dependency, build artifact contains unexpected code.

## Immediate (first 15 minutes)

1. Identify the affected component and version range. Pull the vendor advisory or CVE.
2. Inventory all places the component runs: production, staging, dev, CI runners, developer laptops.
3. For build pipeline compromises (e.g., malicious GitHub Action, npm postinstall script): pause all builds until the pipeline is verified clean.
4. Block egress from build runners to non-essential destinations.

## Investigation

- For a compromised package: when was it introduced, by whom, what version was first used? Check `package-lock.json` / `poetry.lock` / `go.sum` history.
- Check if the malicious version executed in your environment. Many supply-chain payloads only fire on install (postinstall) or first import.
- Look for the IOCs from the vendor advisory: outbound connections, file writes, scheduled tasks, modified env vars.
- For build pipeline: inspect runner logs for the period the malicious component was active. What secrets were available to those runs? `GITHUB_TOKEN`, deploy keys, cloud credentials, signing keys.

## Containment

- Pin to a known-good version or remove the dependency.
- Treat any secrets exposed to compromised runners as leaked. Rotate them.
- If signing keys were exposed, revoke and reissue. Re-sign downstream artifacts.
- For software you publish: notify your downstream consumers if you republished compromised builds.

## Eradication

- Audit all artifacts built during the compromise window. Republish clean versions.
- Remove the malicious package from internal mirrors / artifact repos.
- Hunt for second-stage payloads on hosts that ran the compromised code.

## Recovery

- Resume builds from a clean state with the new dependency baseline.
- Verify deployment artifacts match expected hashes.

## Post-incident

- Implement SBOM generation for every build (Syft, CycloneDX).
- Pin dependencies by hash, not just version. Use `--require-hashes` (pip), integrity field (npm), or sumdb (Go).
- Restrict CI runner egress to a known allowlist.
- Use short-lived OIDC credentials in CI instead of long-lived secrets where possible.
- Sign artifacts (Sigstore, GPG) and verify signatures at deploy time.
- Subscribe to vulnerability feeds for your dependency stack.

## Reference data to capture

- Compromised component name and versions
- Dwell time (when malicious version was published vs detected)
- Internal exposure: which projects, which builds, which runtime hosts
- Secrets potentially exposed
- Downstream consumers notified
