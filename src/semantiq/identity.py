from __future__ import annotations

import re
from dataclasses import dataclass


PRODUCT_NAME = "SEMANTIQ"
__version__ = "0.1.0"

_SEMANTIC_VERSION_PATTERN = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
)


@dataclass(frozen=True, order=True)
class SemanticVersion:
    major: int
    minor: int
    patch: int

    def __post_init__(self) -> None:
        for name, value in (
            ("major", self.major),
            ("minor", self.minor),
            ("patch", self.patch),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    @classmethod
    def parse(cls, value: str) -> SemanticVersion:
        if not isinstance(value, str):
            raise TypeError("semantic version must be a string")

        match = _SEMANTIC_VERSION_PATTERN.fullmatch(value)
        if match is None:
            raise ValueError("semantic version must use canonical X.Y.Z format")

        return cls(*(int(component) for component in match.groups()))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True)
class ProductIdentity:
    name: str
    version: str


def get_product_identity() -> ProductIdentity:
    return ProductIdentity(name=PRODUCT_NAME, version=__version__)
