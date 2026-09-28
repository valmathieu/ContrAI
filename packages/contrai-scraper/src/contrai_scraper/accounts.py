"""The spectator accounts a fleet logs in with, one per worker.

A fleet runs one browser context per account, and every account is somebody's
login. So the list lives in a document of its own, ``accounts.toml``, beside
the profile and git-ignored like it, rather than in N copies of the profile:
the site is described once, and only the secrets multiply.

Each account is filed under a **label** — ``bot01`` and the like — and the
label is what a worker's health lines carry. It is opaque on purpose. A log
line is the thing most likely to leave the machine, and an address in it would
name the account behind a whole corpus.

The document is read as strictly as the profile. A value may read
``env:NAME``, an unknown key is refused, and a label that is not a plain token
is refused rather than cleaned up, because a label that *was* the address would
otherwise reach the logs all the same.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .exceptions import ProfileError
from .profile import AccountSection, _secret, _Table

#: What a label may look like: a bare TOML key, so never an address.
_LABEL: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


@dataclass(frozen=True, slots=True)
class LabelledAccount:
    """One spectator account, and the opaque name its worker goes by."""

    label: str
    """What the worker's health lines carry, in place of the address."""

    account: AccountSection


def load_accounts(path: Path | str) -> tuple[LabelledAccount, ...]:
    """Read and validate an accounts document.

    Each top-level table is one account, named by its label and holding the
    same two keys as the profile's ``[account]``. The order is the document's,
    which is the order workers take them in.

    Args:
        path: The ``accounts.toml`` to read.

    Returns:
        The accounts, in document order.

    Raises:
        ProfileError: The file is missing, unreadable or empty; a label is not
            a plain token; an account has a key missing, unknown or of the
            wrong type, or names an unset environment variable; or two labels
            log in with the same address — two sessions on one account would
            sign each other out.
    """

    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ProfileError(f"cannot read the accounts at {path}: {error}") from error
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ProfileError(f"{path} is not valid TOML: {error}") from error
    if not raw:
        raise ProfileError(f"{path} names no account")

    accounts: list[LabelledAccount] = []
    owner_of: dict[str, str] = {}
    for position, (label, block) in enumerate(raw.items(), start=1):
        if _LABEL.fullmatch(label) is None:
            # Named by position, never echoed: the likeliest bad label is the
            # address itself, and this message is printed.
            raise ProfileError(
                f"account {position}'s label must be a plain token such as "
                "bot01 — letters, digits, '_' or '-' — and never the address"
            )
        if type(block) is not dict:
            raise ProfileError(
                f"[{label}] must be a table holding one account's email and "
                "verification_code"
            )
        table = _Table(label, block)
        account = AccountSection(
            email=_secret(f"[{label}].email", table.string("email")),
            verification_code=_secret(
                f"[{label}].verification_code", table.string("verification_code")
            ),
        )
        table.done()
        address = account.email.casefold()
        if address in owner_of:
            raise ProfileError(
                f"[{owner_of[address]}] and [{label}] log in with the same "
                "address; each worker needs an account of its own"
            )
        owner_of[address] = label
        accounts.append(LabelledAccount(label=label, account=account))
    return tuple(accounts)
