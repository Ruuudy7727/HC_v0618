import json
from pathlib import Path
from typing import Any, Dict, List, Optional

_CONFIG_DIR = Path(__file__).resolve().parent
_PRODUCTS_PATH = _CONFIG_DIR / "products.json"

_products_cache: Optional[Dict[str, Any]] = None


def load_products_config() -> Dict[str, Any]:
    global _products_cache
    if _products_cache is None:
        with _PRODUCTS_PATH.open("r", encoding="utf-8") as f:
            _products_cache = json.load(f)
    return _products_cache


def list_all_products() -> List[Dict[str, Any]]:
    products: List[Dict[str, Any]] = []
    for series in load_products_config().get("series", []):
        for product in series.get("products", []):
            products.append({
                **product,
                "series_id": series.get("id"),
                "series_name": series.get("name"),
            })
    return products


def get_product_by_id(product_id: str) -> Optional[Dict[str, Any]]:
    for product in list_all_products():
        if product.get("id") == product_id:
            return product
    return None


def get_product_by_md_stem(md_stem: str) -> Optional[Dict[str, Any]]:
    stem = (md_stem or "").strip()
    for product in list_all_products():
        if product.get("md_stem") == stem:
            return product
    return None


def get_product_by_pdf(filename: str) -> Optional[Dict[str, Any]]:
    name = Path(filename).name
    for product in list_all_products():
        if product.get("manual_pdf") == name:
            return product
    return None


def match_products_in_query(query: str) -> List[str]:
    text = (query or "").lower()
    matched: List[str] = []
    for product in list_all_products():
        candidates = [product.get("display_name", ""), product.get("id", "")]
        candidates.extend(product.get("aliases", []))
        for candidate in candidates:
            c = (candidate or "").strip().lower()
            if c and c in text:
                matched.append(product["id"])
                break
    return matched
