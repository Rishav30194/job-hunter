"""Shared detection of Anthropic API credit exhaustion.

When the account balance hits zero, every request fails with an
invalid_request_error whose message mentions the credit balance. Callers must
stop making requests immediately — retrying is guaranteed to fail and, in the
scorer, would previously have persisted hundreds of jobs as unscored (then
lost to dedup forever). Verified against the real error on 2026-07-16.
"""

import anthropic

_CREDIT_MARKER = "credit balance is too low"


class CreditExhaustedError(Exception):
    """The Anthropic account has no API credits — abort all further calls."""


def is_credit_error(error: object) -> bool:
    """Return True when an exception or batch error payload is the out-of-credits error."""
    return _CREDIT_MARKER in str(error).lower()


def retryable_api_error(exc: BaseException) -> bool:
    """Retry transient API errors (rate limits, 5xx), never the out-of-credits 400."""
    return (
        isinstance(exc, (anthropic.RateLimitError, anthropic.APIStatusError))
        and not is_credit_error(exc)
    )
