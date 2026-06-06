"""System and user prompt builders for Claude Haiku job scoring."""

# ---------------------------------------------------------------------------
# Candidate resume — baked into the system prompt so every scoring call has
# full context without re-sending it on each message.
# ---------------------------------------------------------------------------
_RESUME = """
CANDIDATE: Rishavsingh Kshatriya
Senior Java Engineer | 6+ years

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
- Roles: Senior Software Engineer, Senior Java Developer, Backend Engineer
- Seniority: roles requiring 3–7 years experience
- Location: Remote / Hybrid / Onsite anywhere in US
- Salary floor: $100,000/year base
- Domains of interest (ranked): Financial services > Healthcare > Enterprise SaaS > Other tech
"""

# ---------------------------------------------------------------------------
# Scoring rubric — embedded in system prompt for consistent per-call behaviour.
# ---------------------------------------------------------------------------
_RUBRIC = """
SCORING RUBRIC (0–100)

Score by weighing these factors:

1. CORE TECH MATCH (most important)
   - Java 17/21 + Spring Boot + Microservices: strong base
   - Add: Kafka / event-driven, cloud (AWS/Azure), PostgreSQL — each raises score
   - Java but heavy frontend/mobile/ML: penalise significantly
   - No Java as primary language: score ≤ 30

2. SENIORITY FIT
   - "Senior" or "3–7 years" required: ideal
   - "Staff" or "7–10 years" required: slight penalty (stretch but possible)
   - Junior (<2 years) or "10+ years" required: penalise heavily

3. DOMAIN MATCH
   - Finance / Banking / Fintech / Insurance: strong bonus
   - Healthcare / Enterprise SaaS: moderate bonus
   - Pure consumer tech or unrelated: neutral

4. COMPANY TIER (use Tier-1/2/3 info if provided, else treat as unknown)
   - Tier-1: bonus; Tier-2: small bonus; Tier-3/Unknown: neutral

5. COMPENSATION (if stated)
   - ≥$150K: bonus; $100–149K: neutral; <$100K: penalise (rare after pre-filter)
   - Not stated: neutral — do NOT penalise for missing salary

6. VISA DISQUALIFICATION
   - If the description explicitly says "will not sponsor", "no sponsorship",
     "must be authorized without sponsorship", or similar → set visa_disqualified=true
     and score=0 regardless of other factors.
   - If no explicit rejection (including if visa is simply not mentioned): visa_disqualified=false

SCORE BANDS
90–100 : Near-perfect — Java 21/Spring Boot, senior, finance/healthcare, Tier-1, strong comp
75–89  : Strong match — core Java skills, right seniority, decent domain or company tier
60–74  : Decent — Java present but partial stack, wrong seniority, or weak domain
40–59  : Weak — Java secondary, primary focus is frontend/infra/ML, or seniority mismatch
0–39   : Poor — not primarily Java backend, wrong domain, or visa disqualified
"""

# ---------------------------------------------------------------------------
# Calibration examples — embedded in the system prompt as text, not as
# conversation turns. This avoids tool_use message format conflicts while
# still anchoring the scoring scale across all four score bands.
# ---------------------------------------------------------------------------
_CALIBRATION = """
CALIBRATION EXAMPLES — use these to anchor your scoring scale:

A) score=93, visa_disqualified=false
   Java 21 + Spring Boot + Kafka + AWS, Senior 5+ yrs, Tier-1 Finance company,
   $160–210K, visa sponsorship explicitly available.
   Why 93: near-perfect stack + seniority + domain + tier + comp alignment.

B) score=80, visa_disqualified=false
   Java/Spring Boot microservices, Senior, Tier-2 tech company (e.g. Stripe),
   $140–160K, no specific finance/healthcare domain mentioned.
   Why 80: strong core match, right seniority, good comp — docked for no domain
   bonus and Tier-2 vs Tier-1.

C) score=65, visa_disqualified=false
   Java/Spring Boot, Senior, Tier-3 IT services company, $110–130K,
   description thin on advanced skills (no Kafka/cloud specifics mentioned).
   Why 65: Java/Spring Boot present, acceptable seniority and comp — docked for
   Tier-3, weak domain, and lack of advanced stack signals.

D) score=52, visa_disqualified=false
   Python (Django) primary backend with some legacy Java services, Senior,
   Unknown company, $120–140K, no domain bonus.
   Why 52: Java secondary — candidate would be hired as a Python engineer.
   Seniority and comp are fine but stack fit is poor.

E) score=0, visa_disqualified=true
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
    if len(description) > 3000:
        description = description[:3000] + "... [truncated]"

    salary = job.get("salary_text") or "Not stated"
    tier = job.get("_tier_label") or "Unknown"

    return (
        f"Title: {job.get('title', '')}\n"
        f"Company: {job.get('company', '')} (Tier: {tier})\n"
        f"Location: {job.get('location', 'Not stated')} | Work type: {job.get('work_type', 'Not stated')}\n"
        f"Salary: {salary}\n\n"
        f"Job Description:\n{description}"
    )
