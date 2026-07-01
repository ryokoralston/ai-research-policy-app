"""Secret masking for settings endpoints.

MASK is the sentinel returned in place of real secrets. Frontends may echo it
back unchanged when a field is left untouched, so PUT handlers must never
persist it as a value.
"""

MASK = "***"


def mask_secret(value: str | None) -> str:
    """Return the mask sentinel for a set secret, empty string otherwise."""
    return MASK if value else ""
