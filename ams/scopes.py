"""Scope strings: 'org', 'project:<id>', 'tenant:<id>', 'run:<id>'."""

from __future__ import annotations

import re
from dataclasses import dataclass

KINDS = ("org", "project", "tenant", "run")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_PATH = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$")


@dataclass(frozen=True)
class Scope:
    kind: str
    id: str | None = None

    @classmethod
    def parse(cls, value: str) -> "Scope":
        from .errors import ScopeError

        if value == "org":
            return cls("org")
        kind, sep, ident = value.partition(":")
        if not sep or kind not in KINDS or kind == "org":
            raise ScopeError(f"invalid scope {value!r}; use org, project:<id>, tenant:<id> or run:<id>")
        if not _ID.fullmatch(ident):
            raise ScopeError(f"invalid scope id {ident!r}")
        return cls(kind, ident)

    def __str__(self) -> str:
        return self.kind if self.kind == "org" else f"{self.kind}:{self.id}"

    @property
    def file_stem(self) -> str:
        """File name used by file-per-scope backends ('org', 'tenant-acme')."""
        return self.kind if self.kind == "org" else f"{self.kind}-{self.id}"


def check_path(path: str) -> str:
    """Memory paths are relative, slash-separated, no '..' and no leading slash."""
    from .errors import ScopeError

    if not _PATH.fullmatch(path) or any(part in (".", "..") for part in path.split("/")):
        raise ScopeError(f"invalid memory path {path!r}")
    return path
