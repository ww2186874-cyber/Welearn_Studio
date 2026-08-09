"""CSV and text account import with runtime-only credentials."""

from __future__ import annotations

import csv
import io
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from welearn_studio.domain.models import AccountIdentity

_USERNAME_HEADERS = {"username", "user", "account"}
_PASSWORD_HEADERS = {"password", "pass"}
_NICKNAME_HEADERS = {"nickname", "nick", "display_name"}


@dataclass(frozen=True, slots=True)
class AccountCredential:
    identity: AccountIdentity
    password: str = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.password:
            raise ValueError("password must not be empty")


@dataclass(frozen=True, slots=True)
class ImportIssue:
    line_number: int
    message: str


@dataclass(frozen=True, slots=True)
class AccountImportResult:
    accounts: tuple[AccountCredential, ...]
    issues: tuple[ImportIssue, ...]


def _decode_account_file(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeError("account file is not valid UTF-8, UTF-16, or GB18030 text")


def parse_account_file(path: str | Path) -> AccountImportResult:
    source = Path(path)
    return parse_accounts(_decode_account_file(source.read_bytes()), format_hint=source.suffix)


def parse_accounts(text: str, *, format_hint: str | None = None) -> AccountImportResult:
    hint = (format_hint or "").lower().lstrip(".")
    if hint == "txt":
        rows = _read_text_rows(text)
    else:
        rows = _read_delimited_rows(text)
    header = bool(rows and _is_header(rows[0][1]))
    return _build_result(rows, header=header)


def _read_delimited_rows(text: str) -> list[tuple[int, list[str]]]:
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff")), dialect)
    rows: list[tuple[int, list[str]]] = []
    for row in reader:
        if not row or all(not value.strip() for value in row):
            continue
        rows.append((reader.line_num, [value.strip() for value in row]))
    return rows


def _read_text_rows(text: str) -> list[tuple[int, list[str]]]:
    rows: list[tuple[int, list[str]]] = []
    for line_number, raw_line in enumerate(text.lstrip("\ufeff").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        delimiter = next((value for value in ("\t", ",", ";") if value in line), None)
        try:
            if delimiter is not None:
                values = next(csv.reader([line], delimiter=delimiter, skipinitialspace=True))
            else:
                values = shlex.split(line, comments=True, posix=True)
        except (csv.Error, ValueError):
            rows.append((line_number, []))
            continue
        cleaned = [value.strip() for value in values]
        if delimiter is None and len(cleaned) > 3:
            cleaned = [cleaned[0], cleaned[1], " ".join(cleaned[2:])]
        rows.append((line_number, cleaned))
    return rows


def _is_header(values: list[str]) -> bool:
    normalized = {value.strip().lower() for value in values}
    known_headers = _USERNAME_HEADERS | _PASSWORD_HEADERS | _NICKNAME_HEADERS
    explicit_headers = {
        "username",
        "account",
        "password",
        "nickname",
        "display_name",
    }
    return len(normalized & known_headers) >= 2 and bool(normalized & explicit_headers)


def _build_result(rows: list[tuple[int, list[str]]], *, header: bool) -> AccountImportResult:
    accounts: list[AccountCredential] = []
    issues: list[ImportIssue] = []
    seen: set[str] = set()
    indexes: tuple[int, int, int | None] | None = None

    if header and rows:
        header_line, values = rows.pop(0)
        normalized = [value.lower() for value in values]
        username_index = next(
            (i for i, value in enumerate(normalized) if value in _USERNAME_HEADERS), None
        )
        password_index = next(
            (i for i, value in enumerate(normalized) if value in _PASSWORD_HEADERS), None
        )
        nickname_index = next(
            (i for i, value in enumerate(normalized) if value in _NICKNAME_HEADERS), None
        )
        if username_index is None or password_index is None:
            return AccountImportResult(
                (), (ImportIssue(header_line, "header requires username and password"),)
            )
        indexes = username_index, password_index, nickname_index

    for line_number, values in rows:
        try:
            if indexes is None:
                if not 2 <= len(values) <= 3:
                    raise ValueError("expected username, password, and optional nickname")
                username, password = values[0], values[1]
                nickname = values[2] if len(values) == 3 else None
            else:
                username_index, password_index, nickname_index = indexes
                required_index = max(username_index, password_index)
                if len(values) <= required_index:
                    raise ValueError("row is missing a username or password column")
                username, password = values[username_index], values[password_index]
                nickname = (
                    values[nickname_index]
                    if nickname_index is not None and nickname_index < len(values)
                    else None
                )
            identity = AccountIdentity(username, nickname)
            credential = AccountCredential(identity, password)
            duplicate_key = identity.username.casefold()
            if duplicate_key in seen:
                raise ValueError("duplicate username")
            seen.add(duplicate_key)
            accounts.append(credential)
        except ValueError as exc:
            issues.append(ImportIssue(line_number, str(exc)))
    return AccountImportResult(tuple(accounts), tuple(issues))
