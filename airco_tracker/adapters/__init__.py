"""Retailer adapters, organized by country.

Country-specific adapters live in subpackages (``nl``, ``fr``, ...). Each
subpackage's ``__init__`` exposes an ``ADAPTERS`` list. Use
:func:`airco_tracker.adapters.registry.load_adapter_specs` when runtime code
needs country-bound site identities; ``load_adapter_classes`` remains for
callers that only need the raw classes.

Country-agnostic parsing helpers shared by all adapters remain in this top-level
package: ``base`` (the ``Adapter`` ABC plus price/BTU/presale parsing),
``schema`` (JSON-LD helpers), and ``sitemap`` (sitemap discovery).
"""

from .registry import load_adapter_classes, load_adapter_specs

__all__ = ["load_adapter_classes", "load_adapter_specs"]
