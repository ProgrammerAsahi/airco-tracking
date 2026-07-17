"""Dutch AliExpress affiliate catalogue inspection adapter.

This class is intentionally not registered while the approved SKU API lacks
documented stock evidence.
"""

from __future__ import annotations

from ..shared.aliexpress import AliExpressAffiliateAdapter


class AliExpressNetherlandsAdapter(AliExpressAffiliateAdapter):
    site = "AliExpress"
    country = "nl"
    destination_country = "NL"
    target_language = "NL"
    discovery_keywords = (
        "Midea PortaSplit",
        "mobiele airco",
        "mobiele airconditioner",
        "portable air conditioner",
    )
