from __future__ import annotations

from bs4 import Tag


def stock_quantity_from_qty_class(scope: Tag, selector: str = ".product-item-photo") -> int | None:
    """Return Magento/Hyvä ``qty-N`` stock quantity from a product card."""

    node = scope.select_one(selector)
    if node is None:
        return None
    classes = node.get("class", [])
    if isinstance(classes, str):
        classes = classes.split()
    for cls in classes:
        if not str(cls).startswith("qty-"):
            continue
        try:
            return int(str(cls)[4:])
        except ValueError:
            return None
    return None

