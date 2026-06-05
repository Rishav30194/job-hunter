"""
Company tier lists sourced from MyVisaJobs FY2025 H1B LCA data (same DOL source as H1BGrader).

Tiers affect scoring weight only — all tiers are eligible for application.
Only HARD_EXCLUDED companies are skipped entirely.
"""

# Highest H1B volume + top brand
TIER_1: frozenset[str] = frozenset({
    # Tech
    "Amazon", "Amazon Web Services", "Microsoft", "Google", "Apple", "Meta",
    "IBM", "Tesla", "Intel", "Qualcomm", "Salesforce", "Nvidia", "Oracle",
    "Cisco", "Adobe", "ServiceNow", "LinkedIn", "eBay", "Micron",
    "Hewlett Packard Enterprise", "Expedia", "Bloomberg", "ADP", "AT&T",
    "Comcast", "T-Mobile", "Charter Communications",
    # Finance
    "JPMorgan Chase", "Goldman Sachs", "Citibank", "Bank of America",
    "American Express", "Morgan Stanley", "Wells Fargo", "Barclays",
    "Visa", "Mastercard", "Charles Schwab", "Fidelity Investments",
    "BlackRock", "PayPal", "Capital One", "Intuit", "FIS", "Optum", "US Bank",
})

# Active H1B sponsors, strong product companies
TIER_2: frozenset[str] = frozenset({
    # Big consulting (tech engineering roles)
    "Accenture", "Deloitte", "Ernst & Young", "EY", "PricewaterhouseCoopers",
    "PwC", "Boston Consulting Group", "McKinsey & Company",
    # Tech
    "ByteDance", "TikTok", "Uber", "DoorDash", "AMD", "Advanced Micro Devices",
    "Palo Alto Networks", "Stripe", "Snowflake", "Databricks", "Palantir",
    "Cloudflare", "CrowdStrike", "Workday", "Twilio", "MongoDB", "Elastic",
    "Datadog", "Splunk", "Broadcom", "DocuSign", "Airbnb", "Lyft",
    "Cognizant Technology Solutions", "Cognizant",
    # Finance / Fintech
    "Robinhood", "Coinbase", "SoFi", "Citadel", "Two Sigma", "D.E. Shaw",
    "Point72", "Millennium Management", "Jane Street", "State Street",
    "Vanguard", "T. Rowe Price", "Northern Trust", "Elevance Health",
})

# All other eligible H1B sponsors
TIER_3: frozenset[str] = frozenset({
    # IT Services / Consulting
    "Tata Consultancy Services", "TCS", "HCL America", "HCL Technologies",
    "Capgemini", "LTIMindtree", "Wipro", "Tech Mahindra", "Mphasis",
    "UST Global", "L&T Technology Services", "Hexaware", "CGI Technologies",
    "Virtusa", "Synechron", "Persistent Systems", "Infinite Computer Solutions",
    "Randstad Digital", "Compunnel Software Group", "Kforce",
    "Skilltune Technologies", "Grandison Management",
    # Fintech / Finance
    "Affirm", "Brex", "Plaid", "Chime", "Marqeta", "Toast", "NerdWallet",
    "Discover Financial", "Synchrony Financial", "Green Dot", "LendingClub",
    "Upstart", "Truist Financial", "PNC Financial", "Fifth Third Bank",
    "KeyCorp", "Raymond James", "LPL Financial", "Regions Financial",
    # Insurance / Health Tech
    "Oscar Health", "Clover Health", "Humana", "Cigna", "Aetna", "CVS Health",
    "USAA", "Progressive", "Allstate", "Northwestern Mutual", "Mayo Clinic",
    # Other industries with active Java engineering roles
    "Walmart Global Tech", "FedEx Technology", "Ford Motor", "General Motors",
    "Cummins", "Rivian", "Lucid Motors", "Eli Lilly", "Target Tech",
    "Wayfair", "Chewy", "Epic Systems", "Veeva Systems", "Tyler Technologies",
})

# Current employer — never apply
HARD_EXCLUDED: frozenset[str] = frozenset({
    "Infosys", "Infosys Limited",
})

ALL_TIERS: frozenset[str] = TIER_1 | TIER_2 | TIER_3


def get_tier(company: str) -> int | None:
    """Return the tier (1, 2, or 3) for a company name, or None if not in any tier.

    Matching is case-sensitive — normalise the company name before calling.
    """
    if company in TIER_1:
        return 1
    if company in TIER_2:
        return 2
    if company in TIER_3:
        return 3
    return None


def is_excluded(company: str) -> bool:
    """Return True if the company is hard-excluded and must never be applied to."""
    return company in HARD_EXCLUDED
