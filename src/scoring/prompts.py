"""System and user prompt builders for Claude Haiku job scoring."""

# ---------------------------------------------------------------------------
# Candidate resume — baked into the system prompt so every scoring call has
# full context without re-sending it on each message.
# ---------------------------------------------------------------------------
_RESUME = """
CANDIDATE: Rishavsingh Kshatriya
Senior Java Engineer | ~8 years total experience

SUMMARY
Distributed systems engineer. 50,000+ daily transactions, 200K+ users across
financial services, healthcare, and enterprise tech. Expert in Spring Boot,
Spring Cloud, event-driven architecture (Kafka, Azure Event Hubs). Shipped
production RAG document assistant (Spring AI, pgvector, Anthropic/OpenAI) and
automated trading platform (Alpaca API, Hetzner VPS, Telegram gateway).

CORE SKILLS
- Languages: Java 17/21 (primary), Python, JavaScript, SQL
- Frameworks: Spring Boot, Spring Cloud (Gateway/Config/Feign), Spring Security,
  Spring MVC, Hibernate/JPA, Spring AI, LangChain4j
- Cloud: AWS (EC2/Lambda/S3/SQS/RDS/ECS/EKS), Azure (Functions/API Mgmt/
  Key Vault/Event Hubs), Terraform, Docker, Kubernetes
- Messaging: Apache Kafka, Azure Event Hubs, JMS
- Databases: PostgreSQL, Oracle, MySQL, MS SQL Server, MongoDB, Redis
- APIs: REST, GraphQL, gRPC, OData, SOAP, OAuth2, JWT, Apigee
- CI/CD: GitLab CI/CD, Jenkins, Azure DevOps, GitHub Actions
- Architecture: Microservices, Event-Driven, DDD, CQRS, Circuit Breaker
  (Resilience4j), TDD, Secure SDLC
- Observability: Prometheus, Grafana, Splunk, ELK Stack, JUnit 5, Mockito

EXPERIENCE
Senior Software Engineer | Ally Financial | Nov 2025–Present | Hartford, CT
- Java 21/Spring Boot document management platform; replaced legacy Oracle
  reporting for 4 business units, cut processing 35%, saved 15 hrs/week.
- PDF rendering pipeline: Oracle stored procs → XML → Apigee API gateway.
- PostgreSQL + Liquibase request-tracking; reduced schema migration errors 95%.
- Terraform + GitLab CI/CD IaC; cut environment setup from 5 days to 2 hours.
- High-throughput SFTP polling microservice; 50,000+ daily documents.

Senior Java Developer | Starbucks | Feb 2022–Oct 2025 | Hartford, CT
- Leave of Absence microservices: Spring Boot + Azure Functions + OData APIs;
  reduced latency 40% across 200K employee records in 2 countries.
- Taleo + Employee Central integration: 45K monthly hiring transactions;
  eliminated redundant ETL, cut data sync errors 92%.
- Event-driven onboarding: Azure Event Hubs + Kafka; Lyft/iOffice provisioning.
- Terraform Azure IaC; deployment time 3 days → under 3 hours.
- TDD: unit test coverage 65% → 90%; mentored 3 engineers.

Java Developer | Vitech System Group | Apr 2021–Jan 2022
- Spring Boot + GraphQL health insurance claims; API response -30%, 450ms→180ms.
- PostgreSQL composite indexing; p95 query latency 1200ms → 250ms at 5K users.

Backend Java Developer | Kash Hospitality | Jun 2020–Apr 2021
- REST API platform replacing manual workflows; -50% processing time, 8 properties.
- Automated 1,200+ daily booking/billing operations; reconciliation errors -90%.

Software Engineer Intern | Forbes Media | Aug 2019–Mar 2020
- Java/Spring Boot trading workflow services; +20% data feed throughput.
- Python/pandas/scikit-learn backtesting pipeline; -65% preprocessing time.

EDUCATION
M.S. Computer Science — Sacred Heart University, CT | GPA 3.7 | May 2021
B.S. Computer Applications — Gujarat University, India | GPA 3.2 | May 2017

CERTIFICATIONS
Microsoft Certified: Azure Fundamentals (AZ-900) — April 2023

TARGET
- Roles: Senior Software Engineer, Senior Java Developer, Staff Engineer, Backend Engineer
- Seniority: roles requiring 5–10 years experience (sweet spot: 5–8 years)
- Location: Remote / Hybrid / Onsite anywhere in US
- Salary floor: $100,000/year base
- Domain experience: Financial services (Ally Financial), Healthcare (Vitech),
  Enterprise SaaS (Starbucks) — strongest fit in these domains
"""

# ---------------------------------------------------------------------------
# Scoring rubric — embedded in system prompt for consistent per-call behaviour.
# ---------------------------------------------------------------------------
_RUBRIC = """
SCORING RUBRIC (0–100)

The score answers one question: how likely is it that this candidate gets hired for this role?
Score fit and realism — not how desirable the job is.

1. CORE TECH MATCH (most important — drives ~50% of score)
   - Java 17/21 + Spring Boot + Microservices: strong base
   - Add: Kafka / event-driven, cloud (AWS/Azure), PostgreSQL — each raises score
   - Java but heavy frontend/mobile/ML required: penalise significantly
   - No Java as primary language: score ≤ 30

2. SENIORITY FIT (critical — drives ~30% of score)
   The candidate has ~8 years of total experience. Score against the job's stated requirement:
   - 5–8 years required: ideal fit — no penalty
   - 3–5 years required: slight penalty (overqualified risk)
   - 8–10 years required: slight penalty (small stretch)
   - 10+ years required ("Lead", "Principal", "Architect"): heavy penalty — candidate is
     likely 2–3 years short; these roles rarely hire below the stated bar
   - <3 years required (junior): heavy penalty (overqualified)

3. DOMAIN EXPERIENCE MATCH (moderate — ~20% of score)
   Only give a bonus if the candidate's ACTUAL WORK EXPERIENCE is relevant to the domain.
   The candidate has real experience in: financial services, healthcare, enterprise SaaS.
   - Role is in finance/banking/fintech AND prior finance experience applies: moderate bonus
   - Role is in healthcare AND prior healthcare experience applies: small bonus
   - Role is in enterprise SaaS / HR tech: small bonus
   - Unrelated domain (consumer tech, gaming, social media): slight penalty
   Do NOT give a domain bonus just because the company is well-known or prestigious.

4. COMPANY TIER — NEUTRAL (do not adjust score)
   Company tier reflects prestige and selectivity, not candidate fit.
   Tier-1 companies have higher hiring bars — they are harder to get into, not easier.
   Do not add or subtract points based on company tier.

5. COMPENSATION — NEUTRAL (do not adjust score)
   All jobs reaching the scorer have already passed a $100K salary floor filter.
   Salary above the floor does not mean better fit. Do not adjust score for compensation.

6. VISA DISQUALIFICATION
   - If the description explicitly says "will not sponsor", "no sponsorship",
     "must be authorized without sponsorship", or similar → set visa_disqualified=true
     and score=0 regardless of other factors.
   - If no explicit rejection (including if visa is simply not mentioned): visa_disqualified=false

SCORE BANDS
90–100 : Near-perfect fit — Java 21/Spring Boot/Kafka, exactly 5–8 yrs required, domain matches
75–89  : Strong fit — core Java stack present, seniority close, role is realistic to land
60–74  : Partial fit — Java present but seniority off by 2+ years, or stack partially matches
40–59  : Weak fit — Java secondary, seniority heavily mismatched, or wrong role type
0–39   : Poor fit — not a Java backend role, major mismatch, or visa disqualified
"""

# ---------------------------------------------------------------------------
# Calibration examples — embedded in the system prompt as text, not as
# conversation turns. This avoids tool_use message format conflicts while
# still anchoring the scoring scale across all four score bands.
# ---------------------------------------------------------------------------
_CALIBRATION = """
CALIBRATION EXAMPLES — use these to anchor your scoring scale:

A) score=92, visa_disqualified=false
   Java 21 + Spring Boot + Kafka + AWS, "Senior Software Engineer, 5–8 yrs",
   fintech company, candidate's finance background directly applies.
   Why 92: near-perfect stack + seniority sweet spot + domain experience matches.
   Company tier and salary did NOT influence this score.

B) score=80, visa_disqualified=false
   Java/Spring Boot microservices, "Senior Engineer, 4–7 yrs required",
   enterprise SaaS company, no specific domain overlap but tech is strong.
   Why 80: strong core stack, seniority fits, slight dock for no domain match.

C) score=72, visa_disqualified=false
   Java/Spring Boot, "Lead Software Engineer, 10+ years required", Tier-1
   finance company (e.g. Visa, JPMorgan), $170–270K.
   Why 72: strong Java/finance stack match, but "Lead / 10+ yrs" requirement is
   a significant seniority stretch (candidate has ~8 yrs). High salary and Tier-1
   company did NOT raise this score — those reflect selectivity, not fit.

D) score=63, visa_disqualified=false
   Java/Spring Boot, Senior, description thin on advanced stack (no Kafka/cloud),
   unrelated domain (e-commerce / consumer tech), 5–8 yrs required.
   Why 63: Java present and seniority fits, but no domain experience and weak
   advanced stack signals.

E) score=50, visa_disqualified=false
   Python (Django) primary backend with some legacy Java services, Senior,
   5–7 yrs required, no domain overlap.
   Why 50: Java secondary — candidate would be hired as a Python engineer.
   Seniority fits but stack fit is poor.

F) score=0, visa_disqualified=true
   Node.js/React primary, "must be authorized to work in the US without
   sponsorship now or in the future."
   Why 0: explicit visa rejection overrides everything. Stack also wrong.
"""

# ---------------------------------------------------------------------------
# Tool definition — enforces output schema at the API level.
# scorer.py passes this with tool_choice=forced so the API rejects any
# response that does not call score_job with conforming arguments.
# ---------------------------------------------------------------------------
SCORING_TOOL: dict = {
    "name": "score_job",
    "description": (
        "Submit the match score and analysis for a job listing against the candidate profile. "
        "Always call this tool — never respond with plain text."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "Match score 0–100 per the rubric.",
            },
            "reasoning": {
                "type": "string",
                "description": (
                    "2–3 sentences: what matched, what did not, "
                    "the single factor that drove the score."
                ),
            },
            "visa_disqualified": {
                "type": "boolean",
                "description": (
                    "True only if the job description explicitly rejects visa sponsorship. "
                    "False if sponsorship is not mentioned at all."
                ),
            },
        },
        "required": ["score", "reasoning", "visa_disqualified"],
    },
}


def build_system_prompt() -> str:
    """Return the system prompt with resume, rubric, and calibration examples baked in.

    Sent once per API call. scorer.py wraps this in a cache_control content block
    so Anthropic caches it across the batch — the ~1,400 token prompt is only
    billed once per cache window rather than once per job.
    """
    return (
        "You are a precise job-match scorer for a specific candidate. "
        "Score each job 0–100 by calling the score_job tool.\n\n"
        f"CANDIDATE PROFILE:\n{_RESUME}\n\n"
        f"{_RUBRIC}\n\n"
        f"{_CALIBRATION}"
    )


def build_user_prompt(job: dict) -> str:
    """Return the user prompt for a single job.

    Truncates description to 3,000 characters. This is the single authoritative
    truncation point — scorer.py does not pre-truncate.
    """
    description = job.get("description") or ""
    _HEAD, _TAIL = 2000, 1000
    if len(description) > _HEAD + _TAIL:
        description = description[:_HEAD] + "\n...\n" + description[-_TAIL:]

    salary = job.get("salary_text") or "Not stated"
    tier = job.get("_tier_label") or "Unknown"

    return (
        f"Title: {job.get('title', '')}\n"
        f"Company: {job.get('company', '')} (Tier: {tier})\n"
        f"Location: {job.get('location', 'Not stated')} | Work type: {job.get('work_type', 'Not stated')}\n"
        f"Salary: {salary}\n\n"
        f"Job Description:\n{description}"
    )
