from __future__ import annotations

import re
from dataclasses import dataclass


PRODUCT_NAME = "SEMANTIQ"
__version__ = "0.1.0"

_SEMANTIC_VERSION_RE = re.compile(r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$")


@dataclass(frozen=True)
class ProductIdentity:
    name: str
    version: str


@dataclass(frozen=True, order=True)
class SemanticVersion:
    major: int
    minor: int
    patch: int


def parse_semantic_version(version: str) -> SemanticVersion:
    """Parse the product's strict MAJOR.MINOR.PATCH version representation."""
    match = _SEMANTIC_VERSION_RE.fullmatch(version)
    if match is None:
        raise ValueError(f"invalid semantic version: {version!r}")
    return SemanticVersion(
        major=int(match.group("major")),
        minor=int(match.group("minor")),
        patch=int(match.group("patch")),
    )


def get_product_identity() -> ProductIdentity:
    return ProductIdentity(name=PRODUCT_NAME, version=__version__)
