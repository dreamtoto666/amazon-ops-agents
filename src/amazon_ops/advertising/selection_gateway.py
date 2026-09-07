"""Human-workbench selector boundary; never an Agent tool or evidence source."""
from __future__ import annotations

import hashlib
import hmac
import os
from collections import defaultdict
from time import monotonic
from typing import Any

from .models import DiagnosticScope
from .openapi_transport import LingxingOpenAPITransport


class InvalidSelectionScope(ValueError):
    """The browser sent stale, cross-directory, or cross-store references."""


class SelectionDirectoryGateway:
    """Maps fixed Open API results to a server-resolved minimal directory."""

    _TTL_SECONDS = 10 * 60

    def __init__(self, transport: LingxingOpenAPITransport | None = None) -> None:
        self.transport = transport or LingxingOpenAPITransport()
        self._snapshots: dict[str, tuple[float, dict[str, Any]]] = {}

    def directory(self, *, owner_id: str | None = None) -> dict[str, Any]:
        sellers = self.transport.get("/erp/sc/data/seller/lists")
        shops = [row for row in sellers.get("data", []) if isinstance(row, dict) and row.get("sid")]
        sids = [int(row["sid"]) for row in shops]
        listings = self.transport.post(
            "/erp/sc/data/mws/listing",
            payload={"sid": ",".join(map(str, sids)), "is_delete": 0, "offset": 0, "length": 1000},
        ) if sids else {"data": []}
        shop_labels = {int(row["sid"]): str(row.get("name") or "未命名店铺") for row in shops}
        grouped: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
        for row in listings.get("data", []):
            if not isinstance(row, dict) or not row.get("sid") or not row.get("parent_asin"):
                continue
            sid, parent = int(row["sid"]), str(row["parent_asin"])
            for person in row.get("principal_info", []) if isinstance(row.get("principal_info"), list) else []:
                if not isinstance(person, dict) or not person.get("principal_uid"):
                    continue
                uid = str(person["principal_uid"])
                item = grouped[sid].setdefault(uid, {"responsible_ref": self._ref("responsible", uid), "label": str(person.get("principal_name") or "未分配"), "products": {}})
                product = item["products"].setdefault(parent, {"product_ref": self._ref("product", f"{sid}:{uid}:{parent}"), "parent_asin": parent, "child_asins": set()})
                product["child_asins"].add(str(row.get("asin") or parent))
        version = self._ref("directory", f"{owner_id or 'anonymous'}:{monotonic()}")
        private: dict[str, Any] = {"owner_id": owner_id, "shops": {}}
        stores: list[dict[str, Any]] = []
        for sid in sorted(shop_labels):
            shop_ref = self._ref("shop", str(sid))
            products_by_ref: dict[str, dict[str, str]] = {}
            responsibles = []
            for person in grouped.get(sid, {}).values():
                private_products = sorted(person["products"].values(), key=lambda item: item["parent_asin"])
                products_by_ref.update({item["product_ref"]: {"parent_asin": item["parent_asin"], "child_asins": sorted(item["child_asins"])} for item in private_products})
                responsibles.append({"responsible_ref": person["responsible_ref"], "label": person["label"], "products": [{"product_ref": item["product_ref"], "parent_asin": item["parent_asin"]} for item in private_products]})
            private["shops"][shop_ref] = {"sid": str(sid), "label": shop_labels[sid], "products": products_by_ref}
            # `sid` is intentionally not returned to the browser.
            stores.append({"shop_ref": shop_ref, "label": shop_labels[sid], "responsibles": responsibles})
        self._snapshots[version] = (monotonic() + self._TTL_SECONDS, private)
        self._purge()
        return {"version": version, "stores": stores}

    def resolve(self, *, owner_id: str | None, version: str, shop_ref: str, product_refs: list[str]) -> DiagnosticScope:
        snapshot = self._snapshots.get(version)
        if not snapshot or snapshot[0] <= monotonic():
            raise InvalidSelectionScope("选择目录已过期，请刷新后重新选择。")
        private = snapshot[1]
        if private["owner_id"] != owner_id:
            raise PermissionError("无权使用该选择目录。")
        shop = private["shops"].get(shop_ref)
        if not shop or not product_refs:
            raise InvalidSelectionScope("店铺或产品选择无效。")
        products = [shop["products"].get(ref) for ref in product_refs]
        if any(product is None for product in products):
            raise InvalidSelectionScope("产品不属于所选店铺或负责人范围。")
        return DiagnosticScope(shop_ref=shop_ref, product_refs=product_refs, sid=shop["sid"], shop_label=shop["label"], parent_asins=sorted({item["parent_asin"] for item in products if item}), child_asins=sorted({asin for item in products if item for asin in item["child_asins"]}))

    @staticmethod
    def _ref(kind: str, value: str) -> str:
        secret = os.getenv("LINGXING_SELECTION_REF_SECRET") or os.getenv("LINGXING_OPEN_API_APP_SECRET", "")
        if not secret:
            raise RuntimeError("选择目录安全配置尚未完成。")
        digest = hmac.new(secret.encode(), f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()[:24]
        return f"{kind}_{digest}"

    def _purge(self) -> None:
        now = monotonic()
        for version, (expires_at, _) in list(self._snapshots.items()):
            if expires_at <= now:
                self._snapshots.pop(version, None)
