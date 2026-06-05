# Job Hunter — Project Overview

## What This Is

An autonomous, 24/7 job search and application system tailored for a Senior Java/Backend Engineer
targeting Tier-1 and Tier-2 tech and finance companies in the US job market.

The system runs continuously on a VPS, fetches fresh job postings every 6 hours from multiple
platforms (LinkedIn, Indeed, Glassdoor, ZipRecruiter), scores each posting using Claude AI against
a detailed candidate profile, routes high-match jobs to a human review queue, and auto-applies
to mid-match jobs — all without manual intervention.

## Goals

| Goal | Mechanism |
|------|-----------|
| Zero stale listings | Hard 48-hour cutoff on all fetched jobs |
| Minimize human time | Auto-apply for score 60–84; human only sees score ≥ 85 |
| Maximum coverage | 5 search terms × 4 job boards per run |
| Smart filtering | Claude Haiku scores each job against actual resume |
| Visa safety | Explicit "no sponsorship" text → auto-discard |
| Recruiter reach | LinkedIn Premium tracing for every high-match job |
| Full visibility | Streamlit dashboard with funnel metrics and one-click actions |

## Target Profile

- **Candidate:** Rishavsingh Kshatriya
- **Experience:** 6+ years, Senior Java/Backend Engineer
- **Core Stack:** Java 17/21, Spring Boot, Kafka, AWS/Azure, Microservices
- **Domains:** Financial services, Healthcare, Enterprise SaaS
- **Roles:** Senior Software Engineer, Backend Engineer, Sr Java Developer
- **Salary Floor:** $100,000/year base
- **Location:** Remote / Hybrid / Onsite — anywhere in US
- **Seniority Target:** Roles requiring 3–7 years of experience

## Target Companies

- **Tier-1 Tech:** Google, Meta, Amazon, Apple, Microsoft, Netflix, Salesforce, Adobe
- **Tier-1 Finance:** JPMorgan, Goldman Sachs, Morgan Stanley, Citi, Bank of America, Wells Fargo
- **Tier-2 Tech:** Stripe, Uber, Snowflake, Databricks, Palantir, Cloudflare, CrowdStrike, NVIDIA
- **Tier-2 Finance:** Capital One, Mastercard, Visa, PayPal, Fidelity, BlackRock, Citadel, Two Sigma
- **Fintechs:** Brex, Plaid, Affirm, SoFi, Robinhood, Chime

## What Gets Automated

| Task | Automated? |
|------|-----------|
| Fetching jobs every 6h | ✅ Fully |
| Deduplication | ✅ Fully |
| Visa sponsorship check | ✅ Fully |
| AI scoring (0–100) | ✅ Fully |
| Telegram alerts (high match) | ✅ Fully |
| Auto-apply (score 60–84) | ✅ Fully (Playwright) |
| Recruiter tracing | ✅ Fully (LinkedIn MCP) |
| Outreach message drafting | ✅ Claude-generated |
| Final approval for score ≥ 85 | 🙋 Human (one click in dashboard) |
| Outreach send confirmation | 🙋 Human (one click in dashboard) |

## Non-Goals

- Does not scrape companies not on the target list
- Does not apply to roles requiring security clearance
- Does not bypass CAPTCHA or violate platform ToS for mass scraping
- Does not store or log any secrets or credentials in code
