# Job Hunter — Project Overview

## What This Is

An autonomous, 24/7 job search and application system tailored for a Senior Java/Backend Engineer
targeting Tier-1 and Tier-2 tech and finance companies in the US job market.

The system runs continuously on a VPS, fetches fresh job postings every 6 hours from multiple
platforms (Indeed, Glassdoor, ZipRecruiter, Google Jobs), scores each posting using Claude AI against
a detailed candidate profile, routes high-match jobs to a human review queue, and auto-applies
to mid-match jobs — all without manual intervention.

## Goals

| Goal | Mechanism |
|------|-----------|
| Zero stale listings | Hard 48-hour cutoff on all fetched jobs |
| Minimize human time | Auto-apply for score 60–84; human only sees score ≥ 85 |
| Maximum coverage | 5 search terms × Indeed / Glassdoor / ZipRecruiter / Google Jobs per run |
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

> Source: MyVisaJobs FY2025 H1B LCA data (same DOL source as H1BGrader).
> Tiers affect scoring weight only — all tiers are eligible. Only Infosys is excluded.

**Tier-1 — Highest H1B volume + top brand**
- **Tech:** Amazon, Amazon Web Services, Microsoft, Google, Apple, Meta, IBM, Tesla, Intel, Qualcomm, Salesforce, Nvidia, Oracle, Cisco, Adobe, ServiceNow, LinkedIn, eBay, Micron, Hewlett Packard Enterprise, Expedia, Bloomberg, ADP, AT&T, Comcast, T-Mobile, Charter Communications
- **Finance:** JPMorgan Chase, Goldman Sachs, Citibank, Bank of America, American Express, Morgan Stanley, Wells Fargo, Barclays, Visa, Mastercard, Charles Schwab, Fidelity Investments, BlackRock, PayPal, Capital One, Intuit, FIS, Optum, US Bank

**Tier-2 — Active H1B sponsors, strong product companies**
- **Big Consulting (tech engineering roles):** Accenture, Deloitte, Ernst & Young (EY), PricewaterhouseCoopers (PwC), Boston Consulting Group, McKinsey & Company
- **Tech:** ByteDance, TikTok, Uber, DoorDash, AMD (Advanced Micro Devices), Palo Alto Networks, Stripe, Snowflake, Databricks, Palantir, Cloudflare, CrowdStrike, Workday, Twilio, MongoDB, Elastic, Datadog, Splunk, Broadcom, DocuSign, Airbnb, Lyft, Cognizant Technology Solutions
- **Finance/Fintech:** Robinhood, Coinbase, SoFi, Citadel, Two Sigma, D.E. Shaw, Point72, Millennium Management, Jane Street, State Street, Vanguard, T. Rowe Price, Northern Trust, Elevance Health (Anthem)

**Tier-3 — All other eligible H1B sponsors**
- **IT Services/Consulting:** Tata Consultancy Services (TCS), HCL America, Capgemini, LTIMindtree, Wipro, Tech Mahindra, Mphasis, UST Global, L&T Technology Services, Hexaware, CGI Technologies, Virtusa, Synechron, Persistent Systems, Infinite Computer Solutions, Randstad Digital, Compunnel Software Group, Kforce, Skilltune Technologies, Grandison Management
- **Fintech/Finance:** Affirm, Brex, Plaid, Chime, Marqeta, Toast, Nerdwallet, Discover Financial, Synchrony Financial, Green Dot, LendingClub, Upstart, Truist Financial, PNC Financial, Fifth Third Bank, KeyCorp, Raymond James, LPL Financial, Regions Financial
- **Insurance/Health Tech:** Oscar Health, Clover Health, Humana, Cigna, Aetna (CVS Health), USAA, Progressive, Allstate, Northwestern Mutual, Mayo Clinic, Elevance Health
- **Other Industries with Java engineering roles:** Walmart Global Tech, FedEx Technology, Ford Motor, General Motors, Cummins, Rivian, Lucid Motors, Eli Lilly, Target Tech, Wayfair, Chewy, Epic Systems, Veeva Systems, Tyler Technologies

**Hard Excluded**
- Infosys / Infosys Limited — current employer, do not apply

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

- Does not restrict scraping to specific companies — all listings are fetched and scored; tiers only affect score weighting
- Does not apply to roles requiring security clearance
- Does not bypass CAPTCHA or violate platform ToS for mass scraping
- Does not store or log any secrets or credentials in code
