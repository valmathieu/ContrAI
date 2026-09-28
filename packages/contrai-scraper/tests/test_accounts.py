"""Pins the accounts document: labels, indirection, strictness."""

import pathlib

import pytest

import contrai_scraper
from contrai_scraper import LabelledAccount, ProfileError, load_accounts

#: The committed schema, read the way an operator's copy would be.
EXAMPLE = pathlib.Path(contrai_scraper.__path__[0]).parents[1] / "accounts.example.toml"

TWO = """
[bot01]
email = "one@example.invalid"
verification_code = "1111"

[bot02]
email = "two@example.invalid"
verification_code = "2222"
"""


def _write(tmp_path, text):
    """An accounts document on disk. Never named ``accounts.toml`` in the repo."""

    path = tmp_path / "fixture-accounts.toml"
    path.write_text(text, encoding="utf-8")
    return path


class TestLoading:
    def test_accounts_come_back_in_document_order(self, tmp_path):
        accounts = load_accounts(_write(tmp_path, TWO))
        assert [(item.label, item.account.email) for item in accounts] == [
            ("bot01", "one@example.invalid"),
            ("bot02", "two@example.invalid"),
        ]

    def test_an_account_is_the_profiles_own_account_type(self, tmp_path):
        # A worker's profile is the site's profile with this account swapped
        # in, so the two must be the same type.
        first = load_accounts(_write(tmp_path, TWO))[0]
        assert (isinstance(first, LabelledAccount),
                type(first.account).__name__) == (True, "AccountSection")

    def test_values_may_read_the_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FLEET_TEST_EMAIL", "env@example.invalid")
        monkeypatch.setenv("FLEET_TEST_CODE", "9999")
        text = TWO.replace('"one@example.invalid"', '"env:FLEET_TEST_EMAIL"')
        text = text.replace('"1111"', '"env:FLEET_TEST_CODE"')
        account = load_accounts(_write(tmp_path, text))[0].account
        assert (account.email, account.verification_code) == (
            "env@example.invalid", "9999")

    def test_the_committed_example_loads(self, monkeypatch):
        for name in ("CONTRAI_BOT01_EMAIL", "CONTRAI_BOT02_EMAIL"):
            monkeypatch.setenv(name, f"{name.lower()}@example.invalid")
        for name in ("CONTRAI_BOT01_CODE", "CONTRAI_BOT02_CODE"):
            monkeypatch.setenv(name, "0000")
        assert [item.label for item in load_accounts(EXAMPLE)] == ["bot01", "bot02"]


class TestRefusals:
    def test_a_missing_file_is_refused(self, tmp_path):
        with pytest.raises(ProfileError, match="cannot read the accounts"):
            load_accounts(tmp_path / "absent.toml")

    def test_a_document_that_is_not_toml_is_refused(self, tmp_path):
        with pytest.raises(ProfileError, match="not valid TOML"):
            load_accounts(_write(tmp_path, "[bot01\n"))

    def test_an_empty_document_is_refused(self, tmp_path):
        with pytest.raises(ProfileError, match="names no account"):
            load_accounts(_write(tmp_path, "# nothing yet\n"))

    def test_an_address_as_a_label_is_refused_without_repeating_it(self, tmp_path):
        # The likeliest bad label is the address itself, and the message is
        # printed — so it names the account by position instead.
        text = TWO.replace("[bot02]", '["two@example.invalid"]')
        with pytest.raises(ProfileError) as refusal:
            load_accounts(_write(tmp_path, text))
        assert ("account 2's label" in str(refusal.value),
                "@" in str(refusal.value)) == (True, False)

    def test_a_value_that_is_not_a_table_is_refused(self, tmp_path):
        with pytest.raises(ProfileError, match=r"\[bot00\] must be a table"):
            load_accounts(_write(tmp_path, 'bot00 = "one@example.invalid"\n' + TWO))

    def test_a_missing_key_is_refused(self, tmp_path):
        text = TWO.replace('verification_code = "2222"\n', "")
        with pytest.raises(ProfileError, match=r"\[bot02\] is missing .*verification_code"):
            load_accounts(_write(tmp_path, text))

    def test_an_unknown_key_is_refused(self, tmp_path):
        text = TWO + 'proxy = "somewhere"\n'
        with pytest.raises(ProfileError, match=r"\[bot02\] has unknown keys: proxy"):
            load_accounts(_write(tmp_path, text))

    def test_an_unset_variable_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FLEET_TEST_UNSET", raising=False)
        text = TWO.replace('"2222"', '"env:FLEET_TEST_UNSET"')
        with pytest.raises(ProfileError, match=r"\[bot02\]\.verification_code reads"):
            load_accounts(_write(tmp_path, text))

    def test_two_labels_on_one_address_are_refused(self, tmp_path):
        # Case-folded: the site does not tell the two spellings apart.
        text = TWO.replace("two@example.invalid", "ONE@example.invalid")
        with pytest.raises(ProfileError, match=r"\[bot01\] and \[bot02\]"):
            load_accounts(_write(tmp_path, text))
