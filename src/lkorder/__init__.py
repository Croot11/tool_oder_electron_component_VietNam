"""lkorder — tìm linh kiện ở shop VN và gom về ít đơn nhất có thể."""

from .models import Allocation, BomLine, Offer, PriceBreak, Shop, Solution
from .optimizer import Options, compare_scenarios, solve

__version__ = "0.1.0"

__all__ = [
    "Allocation", "BomLine", "Offer", "PriceBreak", "Shop", "Solution",
    "Options", "solve", "compare_scenarios",
]
