from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pitchbot.domain import (
    Classification,
    ContactPolicy,
    LanguageCode,
    Lead,
    LeadTemperature,
)


def test_lead_defaults_deny_outreach() -> None:
    lead = Lead(display_name="Synthetic buyer")

    assert lead.language_preference is LanguageCode.UNKNOWN
    assert lead.contact_policy == ContactPolicy()
    assert lead.contact_policy.outreach_allowed is False
    assert lead.created_at.tzinfo is not None


def test_domain_models_are_immutable_and_forbid_extra_fields() -> None:
    lead = Lead(display_name="Synthetic buyer")

    with pytest.raises(ValidationError):
        lead.display_name = "Changed"

    with pytest.raises(ValidationError):
        Lead(display_name="Synthetic buyer", unexpected=True)  # type: ignore[call-arg]


def test_classification_requires_bounded_scores() -> None:
    with pytest.raises(ValidationError):
        Classification(
            lead_id=Lead(display_name="Synthetic buyer").lead_id,
            temperature=LeadTemperature.HOT,
            score=1.1,
            confidence=0.8,
            rule_version="v1",
            classified_at=datetime.now(UTC),
        )
