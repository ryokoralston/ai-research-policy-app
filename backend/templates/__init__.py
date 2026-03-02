from .congressional_brief import CONGRESSIONAL_BRIEF_SYSTEM, CONGRESSIONAL_BRIEF_SECTIONS
from .policy_memo import POLICY_MEMO_SYSTEM, POLICY_MEMO_SECTIONS
from .risk_assessment import RISK_ASSESSMENT_SYSTEM, RISK_ASSESSMENT_SECTIONS

TEMPLATES = {
    "congressional_brief": {
        "system": CONGRESSIONAL_BRIEF_SYSTEM,
        "sections": CONGRESSIONAL_BRIEF_SECTIONS,
    },
    "policy_memo": {
        "system": POLICY_MEMO_SYSTEM,
        "sections": POLICY_MEMO_SECTIONS,
    },
    "risk_assessment": {
        "system": RISK_ASSESSMENT_SYSTEM,
        "sections": RISK_ASSESSMENT_SECTIONS,
    },
}

__all__ = ["TEMPLATES"]
