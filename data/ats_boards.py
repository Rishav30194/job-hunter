"""Company → ATS job-board tokens for direct polling.

Every token below was verified live against the public API on 2026-07-15
(HTTP 200 with a non-empty jobs list). Companies come from the tier lists in
data/companies.py; big banks and most Tier-1 tech use Workday, which has no
simple public API, and are deliberately absent.

Finding a company's token: its careers page URL —
boards.greenhouse.io/{token}, jobs.lever.co/{token}, jobs.ashbyhq.com/{token}.
"""

# (ats, token, display company name) — display name must match the tier lists
# in data/companies.py where the company appears there.
ATS_BOARDS: list[tuple[str, str, str]] = [
    # Greenhouse
    ("greenhouse", "stripe", "Stripe"),
    ("greenhouse", "databricks", "Databricks"),
    ("greenhouse", "cloudflare", "Cloudflare"),
    ("greenhouse", "datadog", "Datadog"),
    ("greenhouse", "coinbase", "Coinbase"),
    ("greenhouse", "robinhood", "Robinhood"),
    ("greenhouse", "sofi", "SoFi"),
    ("greenhouse", "affirm", "Affirm"),
    ("greenhouse", "brex", "Brex"),
    ("greenhouse", "chime", "Chime"),
    ("greenhouse", "marqeta", "Marqeta"),
    ("greenhouse", "toast", "Toast"),
    ("greenhouse", "airbnb", "Airbnb"),
    ("greenhouse", "lyft", "Lyft"),
    ("greenhouse", "twilio", "Twilio"),
    ("greenhouse", "mongodb", "MongoDB"),
    ("greenhouse", "elastic", "Elastic"),
    ("greenhouse", "oscar", "Oscar Health"),
    ("greenhouse", "upstart", "Upstart"),
    ("greenhouse", "point72", "Point72"),
    # Lever
    ("lever", "palantir", "Palantir"),
    ("lever", "veeva", "Veeva Systems"),
    # Ashby
    ("ashby", "nerdwallet", "NerdWallet"),
    ("ashby", "plaid", "Plaid"),
    ("ashby", "snowflake", "Snowflake"),
]
