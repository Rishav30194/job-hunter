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

> Source: MyVisaJobs FY2025 H1B LCA data (same DOL source as H1BGrader). Body shops and staffing
> firms excluded even if they appear high on the H1B volume list.

**Tier-1 — Highest H1B volume + top brand** *(score bonus applied in LLM rubric)*
- **Tech:** Amazon, Microsoft, Google, Apple, Meta, IBM, Tesla, Intel, Qualcomm, Salesforce, Nvidia, Oracle, Cisco, Adobe, ServiceNow, Uber, LinkedIn, eBay, Micron, HPE, Expedia, Bloomberg, ADP
- **Finance:** JPMorgan Chase, Goldman Sachs, Citibank, Bank of America, American Express, Morgan Stanley, Wells Fargo, Barclays, Visa, Mastercard, Charles Schwab, Fidelity Investments, BlackRock, PayPal, Capital One, Intuit, FIS, Optum

**Tier-2 — Active H1B sponsors, strong product companies**
- **Tech:** ByteDance, TikTok, DoorDash, T-Mobile, AMD (Advanced Micro Devices), Stripe, Snowflake, Databricks, Palantir, Cloudflare, CrowdStrike, Workday, Twilio, MongoDB, Elastic, Datadog, Splunk, Broadcom, DocuSign, Airbnb, Lyft, Palo Alto Networks, AT&T, Comcast, Charter Communications
- **Finance/Fintech:** Robinhood, Coinbase, SoFi, Citadel, Two Sigma, D.E. Shaw, Point72, Millennium Management, Jane Street, State Street, Vanguard, T. Rowe Price, Northern Trust, US Bank, Elevance Health (Anthem)

**Tier-3 — Mid-size legitimate H1B sponsors in tech/finance**
- **Fintech:** Affirm, Brex, Plaid, Chime, Marqeta, Toast, Nerdwallet, Discover Financial, Synchrony Financial, Green Dot, LendingClub, Upstart
- **Insurance/Health Tech:** Oscar Health, Clover Health, Humana, Cigna, Aetna (CVS Health), USAA, Progressive, Allstate, Northwestern Mutual, Elevance Health
- **Regional Banking:** Truist Financial, PNC Financial, Fifth Third Bank, KeyCorp, Raymond James, LPL Financial, Regions Financial
- **Other Tech:** Walmart Global Tech, Target Tech, Wayfair, Chewy, Rivian, Epic Systems, Veeva Systems, Tyler Technologies

**Hard Excluded** *(body shops / staffing — filtered before scoring)*
Cognizant, Tata Consultancy Services (TCS), Infosys, HCL America, Capgemini, LTIMindtree,
Wipro, Tech Mahindra, Mphasis, Compunnel, Kforce, CGI, Virtusa, Randstad Digital, Hexaware,
Synechron, Persistent Systems, Infinite Computer Solutions, Skilltune Technologies, Grandison Management

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
