from __future__ import annotations

import pytest

from pitchbot.security.credentials import (
    MIN_SECRET_LENGTH,
    ApiCredential,
    CredentialStore,
    parse_api_keys,
)

SECRET = "a-sufficiently-long-secret"
OTHER = "another-sufficiently-long-secret"


def test_parses_named_entries() -> None:
    assert parse_api_keys(f"web:{SECRET},ops:{OTHER}") == (
        ("web", SECRET),
        ("ops", OTHER),
    )


def test_empty_value_yields_no_credentials() -> None:
    assert parse_api_keys("") == ()
    assert parse_api_keys("  ,  ") == ()


def test_entry_without_a_name_is_refused() -> None:
    with pytest.raises(ValueError, match="must be '<name>:<secret>'"):
        parse_api_keys(SECRET)


@pytest.mark.parametrize("length", [0, 1, MIN_SECRET_LENGTH - 1])
def test_short_secret_is_refused_rather_than_warned_about(length: int) -> None:
    """A short key is the appearance of authentication, which is worse than none."""

    with pytest.raises(ValueError, match="appearance of authentication|must be"):
        parse_api_keys(f"web:{'x' * length}")


def test_duplicate_name_is_refused() -> None:
    with pytest.raises(ValueError, match="defined twice"):
        parse_api_keys(f"web:{SECRET},web:{OTHER}")


def test_duplicate_secret_is_refused() -> None:
    """Two names sharing a secret could never be told apart afterwards."""

    with pytest.raises(ValueError, match="reuses another key's secret"):
        parse_api_keys(f"web:{SECRET},ops:{SECRET}")


def test_store_without_entries_is_not_enforcing() -> None:
    store = CredentialStore(())
    assert store.enforcing is False
    assert store.identify(SECRET) is None
    assert store.identify(None) is None


def test_store_identifies_a_configured_secret() -> None:
    store = CredentialStore(parse_api_keys(f"web:{SECRET},ops:{OTHER}"))
    assert store.enforcing is True
    assert store.identify(SECRET) == ApiCredential(name="web")
    assert store.identify(OTHER) == ApiCredential(name="ops")


def test_store_rejects_an_unknown_or_absent_secret() -> None:
    store = CredentialStore(parse_api_keys(f"web:{SECRET}"))
    assert store.identify("wrong-but-long-enough-value") is None
    assert store.identify(None) is None
    assert store.identify("") is None


def test_store_does_not_match_a_prefix_of_a_real_secret() -> None:
    store = CredentialStore(parse_api_keys(f"web:{SECRET}"))
    assert store.identify(SECRET[:-1]) is None
    assert store.identify(SECRET + "x") is None


def test_names_are_exposed_but_secrets_are_not() -> None:
    """The credential carries a name so it can be logged; the secret must never be."""

    store = CredentialStore(parse_api_keys(f"web:{SECRET}"))
    assert store.names == ("web",)
    credential = store.identify(SECRET)
    assert credential is not None
    assert SECRET not in repr(credential)
