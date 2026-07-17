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
from .ecoflow import EcoFlowFranceAdapter
from .eleclerc import ELeclercFranceAdapter
from .evolarshop import EvolarshopFranceAdapter
from .h2r import H2REquipementsAdapter
from .klarstein import KlarsteinFranceAdapter
from .lidl import LidlFranceAdapter
from .maison_energy import MaisonEnergyAdapter
from .mon_camping_car import MonCampingCarAdapter
from .narbonne import NarbonneAccessoiresAdapter
from .obelink import ObelinkFranceAdapter
from .rueducommerce import RueDuCommerceAdapter
from .trotec import TrotecFranceAdapter


ADAPTERS = [
    CastoramaAdapter,
    AuchanAdapter,
    RueDuCommerceAdapter,
    ElectroDepotFranceAdapter,
    EcoFlowFranceAdapter,
    ELeclercFranceAdapter,
    MaisonEnergyAdapter,
    CreateFranceAdapter,
    EvolarshopFranceAdapter,
    KlarsteinFranceAdapter,
    TrotecFranceAdapter,
    DelonghiFranceAdapter,
    LidlFranceAdapter,
    ActionFranceAdapter,
    H2REquipementsAdapter,
    ObelinkFranceAdapter,
    NarbonneAccessoiresAdapter,
    MonCampingCarAdapter,
]

DEFERRED_ADAPTERS = [
    BoulangerAdapter,
    BricoDepotFranceAdapter,
    CostwayFranceAdapter,
]
