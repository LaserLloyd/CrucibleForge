"""Crop families and seed viability.

Seed loses germination with age; roughly how many years each family stays
good when kept cool and dry (garden lore, rounded down):
"""
from . import util

FAMILY_YEARS = {
    "allium": 1,        # onion, leek: buy fresh every year
    "apiaceae": 2,      # carrot, parsnip, parsley
    "asteraceae": 3,    # lettuce, sunflower
    "brassica": 4,      # cabbage, kale, radish
    "cucurbit": 5,      # squash, cucumber, melon
    "legume": 3,        # beans, peas
    "solanum": 4,       # tomato, pepper, aubergine
    "amaranth": 4,      # beet, chard, spinach
    "": 2,              # unknown family: be careful
}

#: crop word -> family (matched on whole words of the folded seed name)
CROPS = {
    "onion": "allium", "leek": "allium", "garlic": "allium", "chive": "allium", "chives": "allium",
    "carrot": "apiaceae", "parsnip": "apiaceae", "parsley": "apiaceae", "dill": "apiaceae",
    "coriander": "apiaceae", "fennel": "apiaceae",
    "lettuce": "asteraceae", "sunflower": "asteraceae", "calendula": "asteraceae",
    "cabbage": "brassica", "kale": "brassica", "radish": "brassica", "broccoli": "brassica",
    "rocket": "brassica", "turnip": "brassica", "mustard": "brassica",
    "squash": "cucurbit", "pumpkin": "cucurbit", "cucumber": "cucurbit", "courgette": "cucurbit",
    "melon": "cucurbit", "zucchini": "cucurbit",
    "bean": "legume", "beans": "legume", "pea": "legume", "peas": "legume",
    "tomato": "solanum", "pepper": "solanum", "chilli": "solanum", "aubergine": "solanum",
    "beet": "amaranth", "beetroot": "amaranth", "chard": "amaranth", "spinach": "amaranth",
}


def family_of(name):
    """Family for a seed name ('Purple Sprouting Broccoli' -> 'brassica'), '' if unknown."""
    words = util.fold(name).replace("-", " ").split()
    for w in reversed(words):           # the crop is usually the last word
        if w in CROPS:
            return CROPS[w]
    return ""


def good_until(family, harvested):
    """Last year the seed is expected to germinate well; None when the harvest year is unknown."""
    if harvested is None:
        return None
    return harvested + FAMILY_YEARS.get(family, FAMILY_YEARS[""])


def status(family, harvested, year):
    """'fresh', 'use soon' (last good year) or 'old' for the given season; 'unknown' without a year."""
    last = good_until(family, harvested)
    if last is None:
        return "unknown"
    if year < last:
        return "fresh"
    if year == last:
        return "use soon"
    return "old"
