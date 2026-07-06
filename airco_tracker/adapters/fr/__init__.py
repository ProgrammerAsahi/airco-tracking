from __future__ import annotations

from .action import ActionFranceAdapter
from .auchan import AuchanAdapter
from .boulanger import BoulangerAdapter
from .bricodepot import BricoDepotFranceAdapter
from .castorama import CastoramaAdapter
from .costway import CostwayFranceAdapter
from .create_store import CreateFranceAdapter
from .delonghi import DelonghiFranceAdapter
from .electrodepot import ElectroDepotFranceAdapter
from .evolarshop import EvolarshopFranceAdapter
from .klarstein import KlarsteinFranceAdapter
from .lidl import LidlFranceAdapter
from .maison_energy import MaisonEnergyAdapter
from .rueducommerce import RueDuCommerceAdapter
from .trotec import TrotecFranceAdapter


ADAPTERS = [
    CastoramaAdapter,
    AuchanAdapter,
    RueDuCommerceAdapter,
    ElectroDepotFranceAdapter,
    CostwayFranceAdapter,
    MaisonEnergyAdapter,
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
    BricoDepotFranceAdapter,
]
