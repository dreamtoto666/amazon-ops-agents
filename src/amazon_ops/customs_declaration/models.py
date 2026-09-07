from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field


class ShippingSpeed(StrEnum):
    STANDARD = "普船统配"
    EXPRESS = "快船"
    MATSON = "美森极致达"
    STANDARD_ECONOMY = "普船特惠"


class TradeMode(StrEnum):
    GENERAL = "0110"
    CROSS_BORDER = "9810"


class CustomsIssue(BaseModel):
    code: str
    message: str
    file: str | None = None
    sheet: str | None = None
    row: int | None = None
    field: str | None = None


class PreviewItem(BaseModel):
    rule_id: str
    declaration_name: str
    model: str
    material: str
    hs_code: str
    quantity: Decimal
    unit_weight: Decimal
    net_weight: Decimal
    gross_weight: Decimal
    unit_price: Decimal
    amount: Decimal


class CategoryPreview(BaseModel):
    shipping_speed: ShippingSpeed
    trade_mode: TradeMode
    order_count: int = 0
    shipment_count: int = 0
    item_count: int = 0
    box_count: int = 0
    net_weight: Decimal = Decimal("0")
    gross_weight: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")
    generates_file: bool = False
    items: list[PreviewItem] = Field(default_factory=list)


class CustomsPreview(BaseModel):
    declaration_date: date
    can_generate: bool
    categories: list[CategoryPreview]
    warnings: list[CustomsIssue] = Field(default_factory=list)
    errors: list[CustomsIssue] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ProductRule:
    rule_id: str
    skus: frozenset[str]
    standard_name: str
    declaration_name: str
    model: str
    material: str
    usage: str
    unit_weight: Decimal
    unit_price: Decimal
    number_key: tuple[str, ...]

    @property
    def hs_code(self) -> str:
        return "4016939000" if self.material == "EPDM" else "3926909090"

    @property
    def declaration_elements(self) -> str:
        if self.material == "EPDM":
            return (
                f"0|0|{self.usage}|非海绵橡胶|EPDM|非机器仪器用|"
                f"已硫化|无品牌|{self.model}"
            )
        return f"0|0|{self.usage}|{self.material}|无品牌|{self.model}"


@dataclass(slots=True)
class AggregatedItem:
    rule: ProductRule
    quantity: Decimal
    net_weight: Decimal
    gross_weight: Decimal
    amount: Decimal


@dataclass(slots=True)
class CategoryResult:
    shipping_speed: ShippingSpeed
    trade_mode: TradeMode
    order_ids: list[str]
    shipment_count: int
    box_count: int
    gross_weight: Decimal
    net_weight: Decimal
    amount: Decimal
    items: list[AggregatedItem]
