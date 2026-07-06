from __future__ import annotations

from .action import ActionFranceAdapter
from .auchan import AuchanAdapter
from .boulanger import BoulangerAdapter
from .castorama import CastoramaAdapter
from .create_store import CreateFranceAdapter
from .delonghi import DelonghiFranceAdapter
from .evolarshop import EvolarshopFranceAdapter
from .klarstein import KlarsteinFranceAdapter
from .lidl import LidlFranceAdapter
from .rueducommerce import RueDuCommerceAdapter
from .trotec import TrotecFranceAdapter


ADAPTERS = [
    CastoramaAdapter,
    AuchanAdapter,
    RueDuCommerceAdapter,
    CreateFranceAdapter,
    EvolarshopFranceAdapter,
    KlarsteinFranceAdapter,
    TrotecFranceAdapter,
    DelonghiFranceAdapter,
    LidlFranceAdapter,
    ActionFranceAdapter,
]

DEFERRED_ADAPTERS = [
    BoulangerAdapter,
]
