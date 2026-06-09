# Job Hunter — Project Overview

## What This Is

An autonomous, 24/7 job search and application tracking system tailored for a Senior Java/Backend
Engineer targeting US tech and finance companies.

The system runs continuously on a Hetzner VPS, fetches fresh job postings every 6 hours from
Indeed and Google for Jobs, scores each posting using Claude AI against the candidate's actual
resume and experience, routes high-match jobs to an apply queue, monitors Gmail for recruiter
replies, and provides a full dashboard for managing applications — all without manual intervention
beyond the apply button itself.

## Goals

| Goal | Mechanism |
|------|-----------|
| Zero stale listings | Hard 48-hour cutoff on all fetched jobs |
| Minimal human time | Apply Queue shows only scored ≥75 jobs; one click to open and apply |
| Maximum coverage | 8 search terms × Indeed + JSearch (Google for Jobs) per run |
| Accurate scoring | Claude Haiku scores fit (not desirability) — tier and salary are neutral |
| Visa safety | "No sponsorship" / US-citizenship / security-clearance phrases → auto-discard, persisted to DB |
| No repeat rejections | 30-day cooldown after 4+ rejections from a company (tier-1/2 exempt) |
| Inbox awareness | Gmail monitor classifies replies, updates funnel, alerts on action items |
| Full visibility | Streamlit dashboard with funnel metrics, notes, status updates |

## Target Profile

- **Candidate:** Rishavsingh Kshatriya
- **Experience:** ~8 years, Senior Java/Backend Engineer
- **Core Stack:** Java 17/21, Spring Boot, Kafka, AWS/Azure, Microservices
- **Domains:** Financial services, Healthcare, Enterprise SaaS
- **Roles:** Senior Software Engineer, Staff Engineer, Backend Engineer, Senior Java Developer
- **Salary Floor:** $100,000/year base
- **Location:** Remote / Hybrid / Onsite — anywhere in US
- **Seniority Target:** Roles requiring 5–10 years of experience

## Target Companies

> Source: MyVisaJobs FY2025 H1B LCA data (same DOL source as H1BGrader).
> Tiers are passed to the scorer as context but do NOT affect the score.
> Only Infosys is hard-excluded.

**Tier-1 — Highest H1B volume + top brand**
- **Tech:** Amazon, Amazon Web Services, Microsoft, Google, Apple, Meta, IBM, Tesla, Intel, Qualcomm, Salesforce, Nvidia, Oracle, Cisco, Adobe, ServiceNow, LinkedIn, eBay, Micron, Hewlett Packard Enterprise, Expedia, Bloomberg, ADP, AT&T, Comcast, T-Mobile, Charter Communications
- **Finance:** JPMorgan Chase, Goldman Sachs, Citibank, Bank of America, American Express, Morgan Stanley, Wells Fargo, Barclays, Visa, Mastercard, Charles Schwab, Fidelity Investments, BlackRock, PayPal, Capital One, Intuit, FIS, Optum, US Bank

**Tier-2 — Active H1B sponsors, strong product companies**
- **Big Consulting (tech engineering roles):** Accenture, Deloitte, Ernst & Young (EY), PricewaterhouseCoopers (PwC), Boston Consulting Group, McKinsey & Company
- **Tech:** ByteDance, TikTok, Uber, DoorDash, AMD, Palo Alto Networks, Stripe, Snowflake, Databricks, Palantir, Cloudflare, CrowdStrike, Workday, Twilio, MongoDB, Elastic, Datadog, Splunk, Broadcom, DocuSign, Airbnb, Lyft, Cognizant Technology Solutions
- **Finance/Fintech:** Robinhood, Coinbase, SoFi, Citadel, Two Sigma, D.E. Shaw, Point72, Millennium Management, Jane Street, State Street, Vanguard, T. Rowe Price, Northern Trust, Elevance Health (Anthem)

**Tier-3 — All other eligible H1B sponsors**
- **IT Services/Consulting:** Tata Consultancy Services (TCS), HCL America, Capgemini, LTIMindtree, Wipro, Tech Mahindra, Mphasis, UST Global, L&T Technology Services, Hexaware, CGI Technologies, Virtusa, Synechron, Persistent Systems, Infinite Computer Solutions, Randstad Digital, Compunnel Software Group, Kforce, Skilltune Technologies, Grandison Management
- **Fintech/Finance:** Affirm, Brex, Plaid, Chime, Marqeta, Toast, Nerdwallet, Discover Financial, Synchrony Financial, Green Dot, LendingClub, Upstart, Truist Financial, PNC Financial, Fifth Third Bank, KeyCorp, Raymond James, LPL Financial, Regions Financial
- **Insurance/Health Tech:** Oscar Health, Clover Health, Humana, Cigna, Aetna (CVS Health), USAA, Progressive, Allstate, Northwestern Mutual, Mayo Clinic, Elevance Health
- **Other Industries with Java engineering roles:** Walmart Global Tech, FedEx Technology, Ford Motor, General Motors, Cummins, Rivian, Lucid Motors, Eli Lilly, Target Tech, Wayfair, Chewy, Epic Systems, Veeva Systems, Tyler Technologies

**Hard Excluded**
- Infosys / Infosys Limited — do not apply

## What Gets Automated

| Task | Automated? |
|------|-----------|
| Fetching jobs every 6h | ✅ Fully |
| Deduplication | ✅ Fully |
| Visa sponsorship check | ✅ Fully |
| Rejection cooldown (30 days / 4+ rejections / tier-1-2 exempt) | ✅ Fully |
| AI scoring (0–100) | ✅ Fully |
| Telegram alerts (high match + run summary + daily digest) | ✅ Fully |
| Gmail inbox monitoring (classify replies, update status) | ✅ Fully |
| 30-day queue expiry (stale jobs archived automatically) | ✅ Fully |
| Applying to jobs | 🙋 Human (one click per job in dashboard) |
| Status updates (phone screen → interview → offer/rejected) | 🙋 Human (dashboard buttons) or auto via Gmail |

## Non-Goals

- Does not auto-apply — all applications are manual via the dashboard Apply Queue
- Does not attempt to automate Workday, Greenhouse, Lever, Oracle HCM, or any ATS behind Cloudflare
- Does not bypass CAPTCHA or violate platform ToS
- Does not restrict scraping to specific companies — all listings are fetched and scored
- Does not apply to roles requiring security clearance
- Does not store or log any secrets or credentials in code
