from __future__ import annotations

from dataclasses import dataclass


PRODUCT_NAME = "SEMANTIQ"
__version__ = "0.1.0"


@dataclass(frozen=True)
class ProductIdentity:
    name: str
    version: str


def get_product_identity() -> ProductIdentity:
    return ProductIdentity(name=PRODUCT_NAME, version=__version__)
