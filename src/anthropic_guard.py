"""Shared Anthropic API helpers: credit-exhaustion detection and request shaping.

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


def create_kwargs(params: dict) -> dict:
    """Return batch-shaped request params as keyword arguments for messages.create().

    anthropic 1.x removed `temperature` from messages.create() (a TypeError), but
    the API still accepts it and the Haiku 4.5 rollback path sends temperature 0,
    so it travels in extra_body. Batch params forward the key as-is.
    """
    kwargs = dict(params)
    if "temperature" in kwargs:
        kwargs["extra_body"] = {"temperature": kwargs.pop("temperature")}
    return kwargs
