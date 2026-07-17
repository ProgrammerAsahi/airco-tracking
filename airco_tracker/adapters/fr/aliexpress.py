"""French AliExpress affiliate catalogue inspection adapter.

This class is intentionally not registered while the approved SKU API lacks
documented stock evidence.
"""

from __future__ import annotations

from ..shared.aliexpress import AliExpressAffiliateAdapter


class AliExpressFranceAdapter(AliExpressAffiliateAdapter):
    site = "AliExpress"
    country = "fr"
    destination_country = "FR"
    target_language = "FR"
    discovery_keywords = (
        "Midea PortaSplit",
        "climatiseur mobile",
        "climatiseur portable",
        "portable air conditioner",
    )
