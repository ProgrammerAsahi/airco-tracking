"""Dutch retailer adapters for portable air-conditioner stock tracking."""

from .action import ActionAdapter
from .aircovoorinhuis import AircoVoorInHuisAdapter
from .aircowebwinkel import AircoWebwinkelAdapter
from .alternate import AlternateAdapter
from .bostools import BostoolsAdapter
from .coolblue import CoolblueAdapter
from .costway import CostwayAdapter
from .create_store import CreateStoreAdapter
from .delonghi import DelonghiAdapter
from .diy import GammaAdapter, KarweiAdapter
from .electroworld import ElectroWorldAdapter
from .ep import EpAdapter
from .evolarshop import EvolarshopAdapter
from .expert import ExpertAdapter
from .flinq import FlinqAdapter
from .hubo import HuboAdapter
from .kampeerwereld import KampeerwereldAdapter
from .klarstein import KlarsteinAdapter
from .klimaatshop import KlimaatshopAdapter
from .lidl import LidlAdapter
from .mediamarkt import MediaMarktAdapter
from .obelink import ObelinkAdapter
from .praxis import PraxisAdapter
from .solago import SolagoAdapter
from .trotec import TrotecAdapter
from .vrijbuiter import VrijbuiterAdapter
from .wehkamp import WehkampAdapter

__all__ = [
    "ActionAdapter",
    "AircoVoorInHuisAdapter",
    "AircoWebwinkelAdapter",
    "AlternateAdapter",
    "BostoolsAdapter",
    "CoolblueAdapter",
    "CostwayAdapter",
    "CreateStoreAdapter",
    "DelonghiAdapter",
    "ElectroWorldAdapter",
    "EpAdapter",
    "EvolarshopAdapter",
    "ExpertAdapter",
    "FlinqAdapter",
    "GammaAdapter",
    "HuboAdapter",
    "KarweiAdapter",
    "KampeerwereldAdapter",
    "KlarsteinAdapter",
    "KlimaatshopAdapter",
    "LidlAdapter",
    "MediaMarktAdapter",
    "ObelinkAdapter",
    "PraxisAdapter",
    "SolagoAdapter",
    "TrotecAdapter",
    "VrijbuiterAdapter",
    "WehkampAdapter",
]

# Runtime registration order. This is the order adapters are instantiated and
# checked in cli.check(); it is preserved for deterministic logging and to keep
# test_cli expectations stable. Add new Dutch retailers at the end.
ADAPTERS = [
    CoolblueAdapter,
    MediaMarktAdapter,
    EpAdapter,
    ElectroWorldAdapter,
    WehkampAdapter,
    LidlAdapter,
    GammaAdapter,
    KarweiAdapter,
    PraxisAdapter,
    AlternateAdapter,
    TrotecAdapter,
    KlarsteinAdapter,
    FlinqAdapter,
    ActionAdapter,
    ExpertAdapter,
    DelonghiAdapter,
    ObelinkAdapter,
    KampeerwereldAdapter,
    CreateStoreAdapter,
    CostwayAdapter,
    EvolarshopAdapter,
    AircoVoorInHuisAdapter,
    SolagoAdapter,
    HuboAdapter,
    VrijbuiterAdapter,
    KlimaatshopAdapter,
    AircoWebwinkelAdapter,
    BostoolsAdapter,
]
