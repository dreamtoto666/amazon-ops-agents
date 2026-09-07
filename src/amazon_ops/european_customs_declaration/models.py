from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from amazon_ops.customs_declaration.models import CustomsIssue


class EuropeanCategoryPreview(BaseModel):
    channel: str
    prefix: str
    country: str
    trade_mode: str
    shipment_count: int = 0
    item_count: int = 0
    box_count: int = 0
    net_weight: Decimal = Decimal("0")
    gross_weight: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")
    generates_file: bool = False


class EuropeanCustomsPreview(BaseModel):
    declaration_date: date
    can_generate: bool
    categories: list[EuropeanCategoryPreview]
    warnings: list[CustomsIssue] = Field(default_factory=list)
    errors: list[CustomsIssue] = Field(default_factory=list)
