# DDoS Attack

**Trigger:** Service degradation, traffic spike orders of magnitude above baseline, ISP/CDN alert, ransom DDoS note received.

## Immediate (first 15 minutes)

1. Confirm it's a DDoS (not a viral traffic spike or a misconfigured client retry storm). Look at source IP diversity, geographic spread, request patterns.
2. Identify the layer: L3/L4 (volumetric, bandwidth/PPS exhaustion) vs L7 (application, request floods, slowloris, expensive endpoint hammering).
3. Engage your DDoS protection provider (AWS Shield Advanced, Cloudflare, Akamai). If you don't have one: contact your ISP/CDN to enable emergency mitigation.
4. Switch DNS to bring CDN/scrubbing inline if not already.

## Investigation

- For L3/L4: capture flow logs, identify attack vectors (UDP amplification, SYN flood, ICMP).
- For L7: identify which endpoints are being targeted. Often it's a known-expensive one (search, login, password reset, /api/v1/*).
- Check for collateral: are legitimate users blocked by your mitigation? What's the false positive rate of any rate limits?
- Determine if this is a smokescreen, major DDoS sometimes covers other attacks (data theft, account takeover). Increase monitoring on sensitive systems.

## Containment

- L3/L4: rely on upstream scrubbing. Most enterprises cannot mitigate volumetric attacks at the edge themselves.
- L7: tighten WAF rules, enable bot detection (challenge pages, JS challenges, CAPTCHAs as last resort), rate-limit per IP/ASN/fingerprint, geo-block if attack is geo-concentrated and your users aren't there.
- For specific abused endpoints: add aggressive rate limits or temporarily disable the feature.
- Scale horizontally for legitimate traffic that's getting through.

## Eradication

- DDoS doesn't have "eradication" in the malware sense, you wait it out while mitigating. Most attacks last hours to days.
- If a specific botnet is identified, coordinate with law enforcement / CERT for takedown.

## Recovery

- Gradually relax mitigations as attack subsides.
- Monitor for "second wave", attackers often pause and resume to test your defenses.
- Restore any features disabled during the attack.

## Post-incident

- Calculate cost: bandwidth overage, scrubbing fees, lost revenue, engineering time.
- Review architecture: anycast DNS, CDN coverage, autoscaling triggers, expensive endpoints behind auth/rate-limits.
- Practice the runbook quarterly with a tabletop or controlled test.
- Ransom DDoS: do not pay. Payment marks you as a target. Report to law enforcement.

## Reference data to capture

- Attack start/end times, peak traffic (Gbps, PPS, RPS)
- Vector(s), source IP/ASN distribution
- Mitigations applied and effectiveness
- Service availability metrics during the attack
- Cost impact
