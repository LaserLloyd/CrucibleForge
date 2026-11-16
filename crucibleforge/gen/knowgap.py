"""knowgap -- KNOWGAP: knows what it does not know (4.5 Chat element H6, segment owner H6).

A seed + level build a small fictional world (an organisation and its sites, each with typed attributes)
and render it as a pack of documents: prose memos, emails, a register table, a corrections notice. Some
facts are stated, some are derivable (sum, x12, unit conversion, date + N days, "the same as"), some are
deliberately absent with a near-miss beside them (a sibling site's value, a look-alike attribute, a
look-alike site name, a figure only proposed or "similar"), and some are contradicted (two documents of
equal standing, no correction) or corrected (an explicit correction notice). The model answers numbered
questions in a closed form: a value, ``NOT STATED`` or ``CONFLICT``.

**Contrast pairs.** Every question belongs to a pair built from one template: one side has an answer
(a value), the other side's correct answer is NOT STATED or CONFLICT, and that side carries a *lure* (the
near-miss value a careless reader copies). The graded element is the PAIR: it passes only when both
questions are right. So:

* always-decline (NOT STATED everywhere) fails every pair's value side   -> 0
* always-answer / copy-nearest (the lure wherever the truth is not a value) fails every decline side -> 0
* always-CONFLICT fails every value side                                -> 0
* a lookup-only reader (direct stated values, NOT STATED for everything else) passes only the plain
  lookup/look-alike pairs (that is L1 and part of L2: the small-model cell)

Pairs are shuffled apart in the question list. Every check is required by a sentence of the prompt
(``RULES`` below; ``RULE_FOR_KIND`` names the rule each pair kind rests on).

LEVELS (pairs -> 2 questions each):

  level docs sites pairs  kinds added
    1    1    4     5    lookup (sibling's value), look-alike attribute
    2    2    5     6    + x-multiplied money, sum of two sites, unit conversion
    3    4    6     8    + conflict vs explicit correction, same quantity in two units vs two different
                           quantities; a register table
    4    6    8    10    + agreed vs only proposed, "the same as" vs "similar to", date + N days (rule in
                           another document); look-alike site names (Ashby / Ashby Vale)
    5    9   12    14    + chains: "the same as" a site whose figure was corrected vs "the same as" a
                           site whose figure is absent; more of everything

Answer shape: one line per question ``N. ANSWER: <value | NOT STATED | CONFLICT>``; the parser
tolerates a leading quote/dash/bullet/``>``/markdown and reads only the head of the field.
"""
from __future__ import annotations

import datetime as _dt
import re

from ._stack_common import rng
from .. import gen as _gen

FAMILY = "knowgap"
VARIANTS = ()
NS, CF = "NOT STATED", "CONFLICT"

LEVELS = {
    1: {"docs": 1, "sites": 4, "twins": 0, "table": False, "filler": 0,
        "pairs": ["lookup", "lookup", "lookup", "decoy", "decoy"]},
    2: {"docs": 2, "sites": 5, "twins": 0, "table": False, "filler": 2,
        "pairs": ["lookup", "decoy", "multiply", "sum", "convert", "lookup"]},
    3: {"docs": 4, "sites": 6, "twins": 0, "table": True, "filler": 3,
        "pairs": ["lookup", "decoy", "multiply", "sum", "convert", "conflict", "conflict", "uniteq"]},
    4: {"docs": 6, "sites": 8, "twins": 2, "table": True, "filler": 4,
        "pairs": ["lookup", "decoy", "multiply", "sum", "conflict", "uniteq", "undecided", "ref",
                  "dateoff", "lookup"]},
    5: {"docs": 9, "sites": 12, "twins": 3, "table": True, "filler": 6,
        "pairs": ["lookup", "decoy", "multiply", "sum", "convert", "conflict", "conflict", "uniteq",
                  "undecided", "ref", "dateoff", "chain", "chain", "lookup"]},
    # cycle 2: composite questions over k sites; every input must be checked (stated / corrected / same-as /
    # tonnes-or-kg on the value side; one input absent, look-alike-only, proposed, similar or same-as-an-absent
    # on the decline side)
    6: {"docs": 10, "sites": 22, "twins": 4, "table": True, "filler": 6, "k": (3, 3),
        "pairs": ["chain", "multiply", "conflict", "lookup"] + ["composite"] * 8},
    7: {"docs": 12, "sites": 29, "twins": 5, "table": True, "filler": 8, "k": (4, 4),
        "pairs": ["chain", "conflict"] + ["composite"] * 10},
}

#: per-prompt ceiling (thinking included): ~2x the largest hosted run, floored for local thinking models that
#: think 2-25k tokens per generation (largest hosted runs: L1 0.8k, L3 2.8k, L5 7.3k, L6 8.7k, L7 13.1k;
#: MiniMax ran out at its 24,576 cap once on L3 and once on L7). A runaway guard, never a limit on work.
CEILING = {1: 16384, 2: 16384, 3: 24576, 4: 24576, 5: 32768, 6: 49152, 7: 49152}

#: combined-total questions per theme and slot ({L} = "A, B and C"); {M} = the money multiplier
SUMQ = {
    "depots": {"c1": "How many loading docks do the {L} depots have in total?",
               "c2": "How many permanent staff do the {L} depots have in total?",
               "money": "What is the combined monthly rent of the {L} depots, in euros?",
               "moneyM": "What is the combined rent of the {L} depots over {M} months, in euros?",
               "area": "What is the combined floor area of the {L} depots, in square metres?",
               "weight": "What is the combined crane capacity of the {L} depots, in kilograms?"},
    "stations": {"c1": "How many bunks do {L} stations have in total?",
                 "c2": "How many permanent crew do {L} stations have in total?",
                 "money": "What is the combined monthly supply budget of {L} stations, in euros?",
                 "moneyM": "What is the combined supply budget of {L} stations over {M} months, in euros?",
                 "area": "What is the combined hut floor area of {L} stations, in square metres?",
                 "weight": "What is the combined winch rating of {L} stations, in kilograms?"},
    "venues": {"c1": "How many seats do the {L} venues have in total?",
               "c2": "How many stewards do the {L} venues have in total?",
               "money": "What is the combined daily hire fee of the {L} venues, in euros?",
               "moneyM": "What is the combined hire fee of the {L} venues over {M} days, in euros?",
               "area": "What is the combined stage area of the {L} venues, in square metres?",
               "weight": "What is the combined stage load limit of the {L} venues, in kilograms?"},
}
GOOD_MODES = ("stated", "stated", "corrected", "ref_same", "ref_corr", "uniteq")
BAD_MODES = ("absent_decoy", "absent_twin", "undecided", "ref_sim", "ref_absent")

RULES = [
    "Use only the documents below. Do not use outside knowledge, typical values or guesses.",
    "If the answer is stated, or can be worked out exactly from what is stated (for example by adding, "
    "multiplying, converting units, counting days, or following a statement that one figure is the same "
    "as another), give it.",
    "If the answer is not stated and cannot be worked out exactly, answer NOT STATED. A figure that is only "
    "proposed, still under discussion, or described as similar or close to another figure is not a stated "
    "value. A figure given for a different site, or for a different thing at the same site, does not count.",
    "All documents are equally reliable, whatever order they appear in. If a document explicitly corrects "
    "another, use the corrected value. If documents give different values for the same thing and none of "
    "them is an explicit correction, answer CONFLICT. The same quantity written in different units is not "
    "a conflict.",
    "Write numbers as plain digits in the unit the question asks for, without thousands separators or "
    "units; dates as YYYY-MM-DD; names in full, exactly as written in the documents.",
]
#: the rule sentence (1-based index into RULES) each pair kind's decline/conflict side rests on
RULE_FOR_KIND = {"lookup": 3, "decoy": 3, "multiply": 3, "sum": 3, "convert": 3, "conflict": 4, "uniteq": 4,
                 "undecided": 3, "ref": 3, "dateoff": 3, "chain": 3}

SYSTEM = ("You answer questions about a set of documents. Follow the rules given with the documents exactly. "
          "Reply with exactly one line per question, in order, in the form\n"
          "N. ANSWER: <answer>\n"
          "where <answer> is a value, NOT STATED or CONFLICT. No other text.")

# ------------------------------------------------------------------ themes
# slot types: int (count), money (euros), area (m2), weight (kg; written in t or kg), person, date
SLOTS = ("c1", "c2", "money", "area", "weight", "person", "date")
NUMERIC = ("c1", "c2", "money", "area", "weight")

THEMES = {
    "depots": {
        "org": "Harrow Freight", "kind": "depot", "kinds": "depots", "topic": "the regional depot roll-out",
        "names": ["Ashby", "Pell", "Corran", "Haldon", "Mirefield", "Tolworth", "Brack", "Easton", "Kilby",
                  "Wyre", "Stannard", "Orrin", "Fenwick", "Lisle", "Garth", "Hollins", "Marlow", "Dunsby", "Kettering",
                  "Rowan", "Selby", "Thorne", "Ulverston", "Bexley", "Carnforth", "Denholm", "Elstow", "Frisby"],
        "slots": {
            "c1": {"what": "the number of loading docks at the {E} depot", "q": "How many loading docks does the {E} depot have?",
                   "say": ["The {E} depot has {v} loading docks.", "{E} operates {v} loading docks."], "range": (6, 40), "col": "Loading docks",
                   "decoy": {"what": "the number of parking bays at the {E} depot", "say": ["The {E} depot has {v} parking bays."], "range": (41, 90)}},
            "c2": {"what": "the number of permanent staff at the {E} depot", "q": "How many permanent staff does the {E} depot have?",
                   "say": ["{E} employs {v} permanent staff.", "The {E} depot has {v} permanent staff."], "range": (12, 60), "col": "Permanent staff",
                   "decoy": {"what": "the number of agency staff at the {E} depot", "say": ["{E} also uses {v} agency staff."], "range": (61, 99)}},
            "money": {"what": "the monthly rent of the {E} depot", "q": "What is the monthly rent of the {E} depot, in euros?",
                      "mq": "How much rent does the {E} depot pay over {M} months, in euros?", "mult": 12,
                      "say": ["The monthly rent at {E} is {v}.", "{E} costs {v} a month in rent."], "range": (18000, 60000), "col": "Monthly rent",
                      "decoy": {"what": "the monthly service charge of the {E} depot", "say": ["The monthly service charge at {E} is {v}."], "range": (2000, 9000)}},
            "area": {"what": "the floor area of the {E} depot", "q": "What is the floor area of the {E} depot, in square metres?",
                     "say": ["The {E} depot has a floor area of {v}.", "{E}'s floor area is {v}."], "range": (3000, 15000), "col": "Floor area",
                     "decoy": {"what": "the yard area of the {E} depot", "say": ["The yard at {E} covers {v}."], "range": (16000, 30000)}},
            "weight": {"what": "the crane capacity at the {E} depot", "q": "What is the crane capacity at the {E} depot, in kilograms?",
                       "say": ["The crane at {E} is rated for {v}.", "{E}'s crane capacity is {v}."], "range": (30, 90), "col": "Crane capacity",
                       "decoy": {"what": "the forklift capacity at the {E} depot", "say": ["Forklifts at {E} are rated for {v}."], "range": (10, 29)}},
            "person": {"what": "the site manager of the {E} depot", "q": "Who is the site manager of the {E} depot? Give the full name.",
                       "say": ["{v} is the site manager at {E}.", "The {E} depot is managed by {v}."], "col": "Site manager",
                       "decoy": {"what": "the deputy site manager of the {E} depot", "say": ["{v} is the deputy site manager at {E}."]}},
            "date": {"what": "the opening date of the {E} depot", "q": "On what date does the {E} depot open?",
                     "say": ["The {E} depot opens on {v}.", "{E} opens its doors on {v}."], "col": "Opening date",
                     "dq": "On what date is the first safety audit of the {E} depot due?",
                     "drule": "Every depot's first safety audit is due exactly {N} days after the depot opens (the opening date plus {N} days).",
                     "decoy": {"what": "the handover date of the {E} depot", "say": ["The {E} building is handed over to us on {v}."]}},
        },
        "docs": ["Roll-out overview", "Property team memo", "Email from the regional director", "Planning call notes",
                 "Finance summary", "Site visit report", "Email from the operations lead", "Board pack extract",
                 "Corrections and updates"],
        "table": "Depot register",
        "filler": ["The regional director wants weekly progress reports until every site is open.",
                   "Signage for all sites will follow the new brand guidelines.",
                   "IT will install the warehouse system in the week before each opening.",
                   "Recruitment adverts went out on the usual job boards.",
                   "Insurance for the new sites is being handled centrally.",
                   "The next planning call is on the usual Tuesday slot.",
                   "Health and safety training will be run by the central team."],
    },
    "stations": {
        "org": "Northline Survey", "kind": "station", "kinds": "stations", "topic": "the coming field season",
        "names": ["Kestrel", "Brannock", "Tarn", "Eskholm", "Rimefell", "Corrie", "Skerra", "Hvitby", "Lomond",
                  "Quarrel", "Fjellmark", "Ostrey", "Gannet", "Siller", "Moraine", "Harbrek", "Vardo", "Nunatak", "Ptarmigan",
                  "Scree", "Bergsund", "Cairnloch", "Drumlin", "Isfjord", "Kittiwake", "Lyngen", "Petrel", "Sastrugi"],
        "slots": {
            "c1": {"what": "the number of bunks at {E} station", "q": "How many bunks does {E} station have?",
                   "say": ["{E} station sleeps {v} in bunks.", "There are {v} bunks at {E}."], "range": (6, 30), "col": "Bunks",
                   "decoy": {"what": "the number of tent pitches at {E} station", "say": ["{E} has {v} tent pitches for overflow."], "range": (31, 60)}},
            "c2": {"what": "the permanent crew at {E} station", "q": "How many permanent crew does {E} station have?",
                   "say": ["{E} station has a permanent crew of {v}.", "The permanent crew at {E} is {v}."], "range": (3, 20), "col": "Permanent crew",
                   "decoy": {"what": "the seasonal crew at {E} station", "say": ["{E} adds {v} seasonal crew in summer."], "range": (21, 45)}},
            "money": {"what": "the monthly supply budget of {E} station", "q": "What is the monthly supply budget of {E} station, in euros?",
                      "mq": "What is {E} station's supply budget over {M} months, in euros?", "mult": 6,
                      "say": ["{E}'s monthly supply budget is {v}.", "Supplies for {E} are budgeted at {v} a month."], "range": (8000, 40000), "col": "Monthly supply budget",
                      "decoy": {"what": "the monthly fuel budget of {E} station", "say": ["Fuel for {E} is budgeted at {v} a month."], "range": (1000, 7000)}},
            "area": {"what": "the hut floor area at {E} station", "q": "What is the hut floor area at {E} station, in square metres?",
                     "say": ["The hut at {E} has a floor area of {v}.", "{E}'s hut floor area is {v}."], "range": (120, 900), "col": "Hut floor area",
                     "decoy": {"what": "the helipad area at {E} station", "say": ["The helipad at {E} covers {v}."], "range": (901, 2000)}},
            "weight": {"what": "the winch rating at {E} station", "q": "What is the winch rating at {E} station, in kilograms?",
                       "say": ["The winch at {E} is rated for {v}.", "{E}'s winch rating is {v}."], "range": (20, 60), "col": "Winch rating",
                       "decoy": {"what": "the sledge payload rating at {E} station", "say": ["Sledges at {E} carry up to {v}."], "range": (5, 19)}},
            "person": {"what": "the station lead at {E}", "q": "Who is the station lead at {E}? Give the full name.",
                       "say": ["{v} leads {E} station this season.", "The station lead at {E} is {v}."], "col": "Station lead",
                       "decoy": {"what": "the deputy lead at {E}", "say": ["{v} is deputy lead at {E}."]}},
            "date": {"what": "the season start date at {E} station", "q": "On what date does the season start at {E} station?",
                     "say": ["The season at {E} starts on {v}.", "The season at {E} begins on {v}."], "col": "Season start",
                     "dq": "On what date is the first supply drop at {E} station?",
                     "drule": "Each station's first supply drop comes exactly {N} days after its season starts (the start date plus {N} days).",
                     "decoy": {"what": "the last resupply date at {E} station", "say": ["The last resupply at {E} is on {v}."]}},
        },
        "docs": ["Season overview", "Logistics memo", "Email from the chief scientist", "Planning meeting notes",
                 "Budget summary", "Field report", "Email from the logistics lead", "Steering group extract",
                 "Corrections and updates"],
        "table": "Station register",
        "filler": ["All crews must complete cold-weather training before deployment.",
                   "Satellite phones will be checked at the depot before shipping.",
                   "The chief scientist asks for daily weather logs from every station.",
                   "Waste must be flown out at the end of the season.",
                   "Medical kits follow the standard list.",
                   "Radio schedules are unchanged from last season.",
                   "Research permits are being renewed centrally."],
    },
    "venues": {
        "org": "Brightwater Festival", "kind": "venue", "kinds": "venues", "topic": "this year's festival",
        "names": ["Lantern", "Quayside", "Old Mill", "Orchard", "Granary", "Chapel", "Foundry", "Boathouse",
                  "Exchange", "Corn Hall", "Ropewalk", "Cellar", "Assembly", "Pavilion", "Tannery", "Arcade", "Malthouse",
                  "Library", "Courthouse", "Bandstand", "Gallery", "Wharf", "Observatory", "Playhouse", "Customs House",
                  "Drill Hall", "Kiln", "Signal Box"],
        "slots": {
            "c1": {"what": "the number of seats at the {E} venue", "q": "How many seats does the {E} venue have?",
                   "say": ["The {E} venue seats {v}.", "{E} has {v} seats."], "range": (80, 900), "col": "Seats",
                   "decoy": {"what": "the number of standing places at the {E} venue", "say": ["{E} also has {v} standing places."], "range": (901, 1500)}},
            "c2": {"what": "the number of stewards at the {E} venue", "q": "How many stewards does the {E} venue have?",
                   "say": ["{E} is staffed by {v} stewards.", "The {E} venue has {v} stewards."], "range": (4, 30), "col": "Stewards",
                   "decoy": {"what": "the number of volunteers at the {E} venue", "say": ["{v} volunteers help out at {E}."], "range": (31, 70)}},
            "money": {"what": "the daily hire fee of the {E} venue", "q": "What is the daily hire fee of the {E} venue, in euros?",
                      "mq": "What is the {E} venue's hire fee over {M} days, in euros?", "mult": 9,
                      "say": ["The daily hire fee for {E} is {v}.", "{E} costs {v} a day to hire."], "range": (1500, 9000), "col": "Daily hire fee",
                      "decoy": {"what": "the daily cleaning fee of the {E} venue", "say": ["Cleaning at {E} is charged at {v} a day."], "range": (200, 1400)}},
            "area": {"what": "the stage area of the {E} venue", "q": "What is the stage area of the {E} venue, in square metres?",
                     "say": ["The stage at {E} measures {v}.", "{E}'s stage area is {v}."], "range": (40, 400), "col": "Stage area",
                     "decoy": {"what": "the foyer area of the {E} venue", "say": ["The foyer at {E} is {v}."], "range": (401, 900)}},
            "weight": {"what": "the stage load limit at the {E} venue", "q": "What is the stage load limit at the {E} venue, in kilograms?",
                       "say": ["The stage at {E} takes a load of up to {v}.", "{E}'s stage load limit is {v}."], "range": (20, 80), "col": "Stage load limit",
                       "decoy": {"what": "the rigging point limit at the {E} venue", "say": ["Each rigging point at {E} takes {v}."], "range": (5, 19)}},
            "person": {"what": "the venue manager of {E}", "q": "Who is the venue manager of {E}? Give the full name.",
                       "say": ["{v} manages the {E} venue.", "The venue manager at {E} is {v}."], "col": "Venue manager",
                       "decoy": {"what": "the deputy venue manager of {E}", "say": ["{v} is deputy venue manager at {E}."]}},
            "date": {"what": "the get-in date of the {E} venue", "q": "On what date is the get-in at the {E} venue?",
                     "say": ["The get-in at {E} is on {v}.", "Crews get into {E} on {v}."], "col": "Get-in date",
                     "dq": "On what date is the safety walk-through at the {E} venue?",
                     "drule": "Every venue's safety walk-through takes place exactly {N} days after its get-in (the get-in date plus {N} days).",
                     "decoy": {"what": "the get-out date of the {E} venue", "say": ["The get-out at {E} is on {v}."]}},
        },
        "docs": ["Festival overview", "Production memo", "Email from the festival director", "Production meeting notes",
                 "Budget summary", "Venue visit report", "Email from the technical manager", "Trustees' pack extract",
                 "Corrections and updates"],
        "table": "Venue register",
        "filler": ["The festival director wants a weekly update from each venue team.",
                   "Accessibility audits are booked for every venue.",
                   "Box office opens online first.",
                   "Programme copy is due with the designers soon.",
                   "Volunteer briefings take place at the festival hub.",
                   "Noise limits follow the council licence.",
                   "Merchandise will be sold at the hub only."],
    },
}

TWIN_SUFFIX = ["Vale", "North", "Cross", "Park"]
FIRST = ["Dana", "Priya", "Tomas", "Ines", "Kwame", "Mei", "Oskar", "Leila", "Rhys", "Anouk", "Jonah", "Sofia",
         "Bram", "Nadia", "Elif", "Callum", "Yara", "Mateo", "Hana", "Felix", "Greta", "Idris", "Maren", "Luca"]
LAST = ["Okafor", "Lindqvist", "Marchetti", "Ferreira", "Novak", "Achterberg", "Mensah", "Kowalczyk", "Haddad",
        "Brennan", "Sorensen", "Delacroix", "Iwu", "Takeda", "Varga", "Pellegrini", "Ruiz", "Abara", "Holm",
        "Castellano", "Dunmore", "Yilmaz", "Ostrowski", "Quint"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


# ------------------------------------------------------------------ value formatting
def _fmt_int(v: int) -> str:
    return f"{v:,}"


def fmt_value(slot: str, v, unit: str | None = None) -> str:
    """How a value is written in a document."""
    if slot in ("c1", "c2"):
        return str(v)
    if slot == "money":
        return f"€{v:,}"
    if slot == "area":
        return f"{v:,} m²"
    if slot == "weight":                       # v in kg
        return f"{v // 1000}.{(v % 1000) // 100} t" if unit == "t" else f"{v:,} kg"
    if slot == "date":
        d = _dt.date.fromisoformat(v)
        return f"{d.day} {MONTHS[d.month - 1]} {d.year}"
    return str(v)


def key_value(slot: str, v) -> str:
    """How the value is written in the answer key (the reference)."""
    return str(v)


# ------------------------------------------------------------------ world building
class _World:
    def __init__(self, theme_key: str, level: int, r):
        self.tk, self.th, self.level, self.r = theme_key, THEMES[theme_key], level, r
        spec = LEVELS[level]
        base = list(self.th["names"])
        r.shuffle(base)
        nt = spec["twins"]
        sites = base[: spec["sites"] - nt]
        twins = []
        for i in range(nt):
            t = f"{sites[i]} {TWIN_SUFFIX[i % len(TWIN_SUFFIX)]}"
            twins.append((sites[i], t))
            sites.append(t)
        r.shuffle(sites)
        self.sites, self.twin_of = sites, {a: b for a, b in twins}
        self.twin_of.update({b: a for a, b in twins})
        self.cells: dict = {}          # (site, slot) -> cell dict
        self.dcells: dict = {}         # (site, slot) -> decoy value (stated)
        self.used_vals: dict = {s: set() for s in SLOTS}
        self.used_dvals: dict = {s: set() for s in SLOTS}
        ppl = [f"{f} {l}" for f, l in zip(r.sample(FIRST, len(FIRST)), r.sample(LAST, len(LAST)))]
        self.people = ppl              # distinct first AND last names within an instance
        self.offset_n = r.choice([7, 10, 14, 21, 28, 30])
        self.mult = self.th["slots"]["money"]["mult"]

    # values -----------------------------------------------------------
    def _draw(self, slot: str, decoy: bool = False):
        used = self.used_dvals[slot] if decoy else self.used_vals[slot]
        sd = self.th["slots"][slot]
        for _ in range(500):
            if slot in ("c1", "c2"):
                lo, hi = (sd["decoy"]["range"] if decoy else sd["range"])
                v = self.r.randint(lo, hi)
            elif slot == "money":
                lo, hi = (sd["decoy"]["range"] if decoy else sd["range"])
                v = self.r.randrange(lo, hi + 1, 50 if hi < 10000 else 250)
            elif slot == "area":
                lo, hi = (sd["decoy"]["range"] if decoy else sd["range"])
                v = self.r.randrange(lo, hi + 1, 5 if hi < 2000 else 50)
            elif slot == "weight":
                lo, hi = (sd["decoy"]["range"] if decoy else sd["range"])
                v = self.r.randint(lo, hi) * 100          # kg, a whole tenth of a tonne
            elif slot == "person":
                if not self.people:
                    raise RuntimeError("people exhausted")
                v = self.people.pop()
            else:                                         # date in 2027
                v = (_dt.date(2027, 2, 1) + _dt.timedelta(days=self.r.randint(0, 300))).isoformat()
            if (v not in self.used_vals[slot] and v not in self.used_dvals[slot]) or (_ > 300 and slot != "person"):
                used.add(v)
                return v
        raise RuntimeError("value space exhausted")

    def free(self, slot: str, exclude=()) -> list:
        return [s for s in self.sites if (s, slot) not in self.cells and s not in exclude]

    def take(self, slot: str, n: int, exclude=(), prefer=None) -> list:
        cand = self.free(slot, exclude)
        self.r.shuffle(cand)
        if prefer:
            cand.sort(key=lambda s: 0 if prefer(s) else 1)
        if len(cand) < n:
            raise RuntimeError("no free site")
        return cand[:n]

    def set(self, site, slot, **cell):
        self.cells[(site, slot)] = cell
        return cell

    def decoy(self, site, slot):
        if (site, slot) not in self.dcells:
            self.dcells[(site, slot)] = self._draw(slot, decoy=True)
        return self.dcells[(site, slot)]

    def stated(self, site, slot, role="filler"):
        v = self._draw(slot)
        self.set(site, slot, st="stated", v=v, role=role)
        return v


# ------------------------------------------------------------------ questions
def _q(w: _World, site: str, slot: str, derived: str | None = None, other: str | None = None) -> str:
    sd = w.th["slots"][slot]
    if derived == "multiply":
        return sd["mq"].format(E=site, M=w.mult)
    if derived == "sum":
        lab = {"c1": None, "c2": None}
        q = sd["q"]
        # "How many loading docks does the X depot have?" -> "... do the X and Y depots have in total?"
        what = sd["what"].replace("{E}", "{A} and {B}")
        return _sum_q(w, slot, site, other)
    if derived == "dateoff":
        return sd["dq"].format(E=site)
    return sd["q"].format(E=site)


def _sum_q(w: _World, slot: str, a: str, b: str) -> str:
    th, sd = w.th, w.th["slots"][slot]
    noun = {"c1": sd["col"].lower(), "c2": sd["col"].lower()}[slot]
    if th["kind"] == "station":
        return f"How many {noun} do {a} and {b} stations have in total?"
    return f"How many {noun} do the {a} and {b} {th['kinds']} have in total?"


def _pair(kind, value_q, decline_q):
    return {"kind": kind, "qs": [value_q, decline_q]}


def _qrec(text, slot, truth, lure, side, note):
    return {"text": text, "slot": slot, "truth": truth, "lure": lure, "side": side, "note": note}


def _kg_slot(slot):
    return "weight" if slot == "weight" else slot


def build_pair(w: _World, kind: str):
    r = w.r
    if kind == "lookup":
        slot = r.choice(SLOTS)
        x, y = w.take(slot, 2, prefer=lambda s: s in w.twin_of)
        v = w.stated(x, slot, role="asked")
        w.set(y, slot, st="absent", role="asked")
        # lure: the look-alike-named site when there is one, else another site, stated in the same slot
        z = w.twin_of.get(y)
        if z is None or (z, slot) in w.cells:
            z = w.take(slot, 1)[0]
        lure = w.stated(z, slot, role="lure")
        w.cells[(z, slot)]["near"] = y
        return _pair(kind, _qrec(_q(w, x, slot), slot, v, None, "value", "stated"),
                     _qrec(_q(w, y, slot), slot, NS, lure, "decline", f"absent; {z} has it"))
    if kind == "decoy":
        slot = r.choice(SLOTS)
        x, y = w.take(slot, 2)
        v = w.stated(x, slot, role="asked")
        w.decoy(x, slot)
        w.set(y, slot, st="absent", role="asked")
        lure = w.decoy(y, slot)
        return _pair(kind, _qrec(_q(w, x, slot), slot, v, None, "value", "stated beside its look-alike"),
                     _qrec(_q(w, y, slot), slot, NS, lure, "decline", "absent; look-alike attribute stated"))
    if kind == "multiply":
        slot = "money"
        x, y = w.take(slot, 2)
        v = w.stated(x, slot, role="asked")
        w.set(y, slot, st="absent", role="asked")
        lure = w.decoy(y, slot) * w.mult
        return _pair(kind, _qrec(_q(w, x, slot, "multiply"), slot, v * w.mult, None, "value", f"x{w.mult}"),
                     _qrec(_q(w, y, slot, "multiply"), slot, NS, lure, "decline", "absent; look-alike charge stated"))
    if kind == "sum":
        slot = r.choice(("c1", "c2"))
        a, b, c, d = w.take(slot, 4)
        va, vb = w.stated(a, slot, role="asked"), w.stated(b, slot, role="asked")
        vc = w.stated(c, slot, role="asked")
        w.set(d, slot, st="absent", role="asked")
        lure = vc + w.decoy(d, slot)
        return _pair(kind, _qrec(_sum_q(w, slot, a, b), slot, va + vb, None, "value", "sum of two stated"),
                     _qrec(_sum_q(w, slot, c, d), slot, NS, lure, "decline", f"{d} absent"))
    if kind == "convert":
        slot = "weight"
        x, y = w.take(slot, 2)
        v = w.stated(x, slot, role="asked")
        w.cells[(x, slot)]["unit"] = "t"
        w.set(y, slot, st="absent", role="asked")
        lure = w.decoy(y, slot)
        return _pair(kind, _qrec(_q(w, x, slot), slot, v, None, "value", "tonnes to kg"),
                     _qrec(_q(w, y, slot), slot, NS, lure, "decline", "absent; look-alike rating stated"))
    if kind == "conflict":
        slot = r.choice(("c1", "c2", "money", "area", "person", "date"))
        x, y = w.take(slot, 2)
        v1, v2 = w._draw(slot), w._draw(slot)
        w.set(x, slot, st="conflict", v1=v1, v2=v2, role="asked")
        old, new = w._draw(slot), w._draw(slot)
        w.set(y, slot, st="corrected", old=old, new=new, role="asked")
        return _pair(kind, _qrec(_q(w, y, slot), slot, new, old, "value", "corrected"),
                     _qrec(_q(w, x, slot), slot, CF, v1, "decline", "two values, no correction"))
    if kind == "uniteq":
        slot = "weight"
        x, y = w.take(slot, 2)
        v = w._draw(slot)
        w.set(x, slot, st="uniteq", v=v, role="asked")
        k1, k2 = w._draw(slot), w._draw(slot)
        w.set(y, slot, st="unitconf", v1=k1, v2=k2, role="asked")
        return _pair(kind, _qrec(_q(w, x, slot), slot, v, None, "value", "same value in t and kg"),
                     _qrec(_q(w, y, slot), slot, CF, k1, "decline", "t and kg values differ"))
    if kind == "undecided":
        slot = r.choice(("c1", "c2", "money", "area", "person"))
        x, y = w.take(slot, 2)
        v = w._draw(slot)
        w.set(x, slot, st="agreed", v=v, role="asked")
        o1, o2 = w._draw(slot), w._draw(slot)
        w.set(y, slot, st="undecided", v1=o1, v2=o2, role="asked")
        return _pair(kind, _qrec(_q(w, x, slot), slot, v, None, "value", "agreed"),
                     _qrec(_q(w, y, slot), slot, NS, o1, "decline", "only proposed"))
    if kind == "ref":
        slot = r.choice(("c1", "c2", "money", "area"))
        x, y, z, u = w.take(slot, 4)
        vz, vu = w.stated(z, slot, role="ref"), w.stated(u, slot, role="ref")
        w.set(x, slot, st="ref_same", z=z, role="asked")
        w.set(y, slot, st="ref_sim", z=u, role="asked")
        return _pair(kind, _qrec(_q(w, x, slot), slot, vz, None, "value", f"same as {z}"),
                     _qrec(_q(w, y, slot), slot, NS, vu, "decline", f"only similar to {u}"))
    if kind == "dateoff":
        slot = "date"
        x, y = w.take(slot, 2)
        v = w.stated(x, slot, role="asked")
        w.set(y, slot, st="absent", role="asked")
        dv = w.decoy(y, slot)
        add = lambda d: (_dt.date.fromisoformat(d) + _dt.timedelta(days=w.offset_n)).isoformat()  # noqa: E731
        w.uses_offset = True
        return _pair(kind, _qrec(_q(w, x, slot, "dateoff"), slot, add(v), v, "value", f"+{w.offset_n} days"),
                     _qrec(_q(w, y, slot, "dateoff"), slot, NS, add(dv), "decline", "start date absent; look-alike date stated"))
    if kind == "chain":
        slot = r.choice(("c1", "c2", "money", "area"))
        x, y, z, u = w.take(slot, 4)
        old, new = w._draw(slot), w._draw(slot)
        w.set(z, slot, st="corrected", old=old, new=new, role="ref")
        w.set(u, slot, st="absent", role="ref")
        lure = w.decoy(u, slot)
        w.set(x, slot, st="ref_same", z=z, role="asked")
        w.set(y, slot, st="ref_same", z=u, role="asked")
        return _pair(kind, _qrec(_q(w, x, slot), slot, new, old, "value", f"same as {z}, which was corrected"),
                     _qrec(_q(w, y, slot), slot, NS, lure, "decline", f"same as {u}, whose figure is absent"))
    if kind == "composite":
        return _composite(w)
    raise ValueError(kind)


def _listing(names):
    return ", ".join(names[:-1]) + " and " + names[-1]


def _input(w: _World, slot: str, mode: str):
    """One input of a composite: returns (truth, lure); truth None = not determinable."""
    if mode == "absent_twin":
        cand = [s for s in w.free(slot) if s in w.twin_of and (w.twin_of[s], slot) not in w.cells]
        if not cand:
            mode = "absent_decoy"
        else:
            x = w.r.choice(cand)
            w.set(x, slot, st="absent", role="asked")
            return x, None, w.stated(w.twin_of[x], slot, role="lure")
    if mode == "uniteq" and slot != "weight":
        mode = "stated"
    x = w.take(slot, 1)[0]
    if mode == "stated":
        v = w.stated(x, slot, role="asked")
        return x, v, v
    if mode == "corrected":
        old, new = w._draw(slot), w._draw(slot)
        w.set(x, slot, st="corrected", old=old, new=new, role="asked")
        return x, new, old
    if mode == "uniteq":
        v = w._draw(slot)
        w.set(x, slot, st="uniteq", v=v, role="asked")
        return x, v, v
    if mode in ("ref_same", "ref_corr", "ref_sim", "ref_absent"):
        w.set(x, slot, st="pending", role="asked")
        z = w.take(slot, 1)[0]
        if mode in ("ref_same", "ref_sim"):
            vz = w.stated(z, slot, role="ref")
            w.set(x, slot, st="ref_same" if mode == "ref_same" else "ref_sim", z=z, role="asked")
            return x, (vz if mode == "ref_same" else None), vz
        if mode == "ref_corr":
            old, new = w._draw(slot), w._draw(slot)
            w.set(z, slot, st="corrected", old=old, new=new, role="ref")
            w.set(x, slot, st="ref_same", z=z, role="asked")
            return x, new, old
        w.set(z, slot, st="absent", role="ref")
        w.set(x, slot, st="ref_same", z=z, role="asked")
        return x, None, w.decoy(z, slot)
    if mode == "absent_decoy":
        w.set(x, slot, st="absent", role="asked")
        return x, None, w.decoy(x, slot)
    if mode == "undecided":
        o1, o2 = w._draw(slot), w._draw(slot)
        w.set(x, slot, st="undecided", v1=o1, v2=o2, role="asked")
        return x, None, o1
    raise ValueError(mode)


def _composite(w: _World):
    r = w.r
    lo, hi = LEVELS[w.level].get("k", (3, 3))
    # the slot with the most free sites (ties at random), so big levels do not run one slot dry
    free = {sl: len(w.free(sl)) for sl in NUMERIC}
    top = max(free.values())
    slot = r.choice([sl for sl in NUMERIC if free[sl] >= top - 2])
    mult = 1
    key = slot
    if slot == "money" and r.random() < 0.5:
        mult, key = w.mult, "moneyM"
    sides = []
    for side in ("value", "decline"):
        k = r.randint(lo, hi)
        modes = [r.choice(GOOD_MODES) for _ in range(k)]
        if side == "decline":
            modes[r.randrange(k)] = r.choice(BAD_MODES)
        got = [_input(w, slot, m) for m in modes]
        names = [g[0] for g in got]
        r.shuffle(names)
        text = SUMQ[w.tk][key].format(L=_listing(names), M=w.mult)
        lure = sum(g[2] for g in got) * mult
        if side == "value":
            truth = sum(g[1] for g in got) * mult
            sides.append(_qrec(text, slot, truth, lure if lure != truth else None, "value",
                               "composite: " + ", ".join(modes)))
        else:
            sides.append(_qrec(text, slot, NS, lure, "decline", "composite: " + ", ".join(modes)))
    return _pair("composite", sides[0], sides[1])


# ------------------------------------------------------------------ documents
def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _render(w: _World):
    """Assign every stated fact to documents and render them. Returns (docs, doc_titles)."""
    r, th, spec = w.r, w.th, LEVELS[w.level]
    nd = spec["docs"]
    pool = list(th["docs"][:-1]) + ["Weekly update", "Follow-up email", "Second site visit report",
                                     "Risk log extract", "Handover notes"]
    titles = pool[: nd - 1] + [th["docs"][-1]] if nd >= 4 else pool[:nd]
    has_corr = nd >= 4
    corr_doc = nd - 1 if has_corr else None
    prose = [i for i in range(nd) if i != corr_doc]
    table_doc = 1 if spec["table"] and nd >= 3 else None
    text_docs = [i for i in prose if i != table_doc] or prose
    lines = {i: [] for i in range(nd)}           # sentences per doc
    table = {}                                   # (site, slot) -> written value

    def place(sent, docs=None):
        i = r.choice(docs or text_docs)
        lines[i].append(sent)
        return i

    def say(site, slot, v, unit=None, decoy=False):
        sd = th["slots"][slot]["decoy"] if decoy else th["slots"][slot]
        return r.choice(sd["say"]).format(E=site, v=fmt_value(slot, v, unit))

    def what(site, slot):
        return th["slots"][slot]["what"].format(E=site)

    def put_value(site, slot, v, unit=None, docs=None, allow_table=True):
        if allow_table and table_doc is not None and (docs is None or table_doc in docs) and r.random() < 0.45 \
                and slot != "weight":
            table[(site, slot)] = fmt_value(slot, v, unit)
            return table_doc
        return place(say(site, slot, v, unit), docs)

    for (site, slot), c in sorted(w.cells.items(), key=lambda kv: (kv[0][0], SLOTS.index(kv[0][1]))):
        st = c["st"]
        if st == "stated":
            unit = c.get("unit") or ("t" if slot == "weight" and r.random() < 0.5 else ("kg" if slot == "weight" else None))
            put_value(site, slot, c["v"], unit)
        elif st == "absent":
            pass
        elif st == "conflict":
            d1 = put_value(site, slot, c["v1"])
            others = [i for i in prose if i != d1]
            place(say(site, slot, c["v2"]), others)
        elif st == "corrected":
            d1 = put_value(site, slot, c["old"])
            lines[corr_doc].append(f"Correction to “{titles[d1]}”: {what(site, slot)} is {fmt_value(slot, c['new'])}, "
                                   f"not {fmt_value(slot, c['old'])}.")
        elif st == "uniteq":
            d1 = place(say(site, slot, c["v"], "t"))
            place(say(site, slot, c["v"], "kg"), [i for i in prose if i != d1])
        elif st == "unitconf":
            d1 = place(say(site, slot, c["v1"], "t"))
            place(say(site, slot, c["v2"], "kg"), [i for i in prose if i != d1])
        elif st == "agreed":
            place(f"{_cap(what(site, slot))} has been agreed at {fmt_value(slot, c['v'])}.")
        elif st == "undecided":
            a, b = fmt_value(slot, c["v1"]), fmt_value(slot, c["v2"])
            if slot == "person":
                place(f"{_cap(what(site, slot))} has not been appointed yet; {a} and {b} are both on the shortlist.")
            else:
                place(f"{_cap(what(site, slot))} has not been settled yet; {a} and {b} are both under discussion.")
        elif st == "ref_same":
            place(f"{_cap(what(site, slot))} is exactly the same as at the {c['z']} {th['kind']}." if th["kind"] != "station"
                  else f"{_cap(what(site, slot))} is exactly the same as at {c['z']} station.")
        elif st == "ref_sim":
            place(f"{_cap(what(site, slot))} is expected to be similar to the figure for the {c['z']} {th['kind']}."
                  if th["kind"] != "station" else
                  f"{_cap(what(site, slot))} is expected to be similar to the figure for {c['z']} station.")
    for (site, slot), v in sorted(w.dcells.items()):
        place(say(site, slot, v, "kg" if slot == "weight" else None, decoy=True))
    # global rule for the date + N question (in a different document from most dates when possible)
    rule = th["slots"]["date"]["drule"].format(N=w.offset_n)
    intro_i = 0
    intro = [f"This pack collects what we know so far about {th['topic']} at {th['org']}. "
             f"It covers {len(w.sites)} {th['kinds']}: " + ", ".join(sorted(w.sites)) + "."]
    if getattr(w, "uses_offset", False) or r.random() < 0.5:
        intro.append(rule)
    fill = list(th["filler"])
    r.shuffle(fill)
    for k in range(spec["filler"]):
        lines[r.choice(prose)].append(fill[k % len(fill)])
    docs = []
    for i in range(nd):
        body = list(lines[i])
        r.shuffle(body)
        if i == intro_i:
            body = intro + body
        if i == table_doc:
            cols = [s for s in SLOTS if s != "weight" and any(k[1] == s for k in table)]
            rows = [s for s in sorted(w.sites) if any((s, c) in table for c in cols)]
            tl = ["| " + th["kind"].capitalize() + " | " + " | ".join(th["slots"][c]["col"] for c in cols) + " |",
                  "|---" * (len(cols) + 1) + "|"]
            for s in rows:
                tl.append(f"| {s} | " + " | ".join(table.get((s, c), "—") for c in cols) + " |")
            note = "A dash means the register has no figure for that entry."
            text = "\n".join(tl) + "\n\n" + note + ("\n\n" + " ".join(body) if body else "")
        else:
            text = _paras(body, r)
        docs.append((titles[i], text))
    return docs


def _paras(sents: list, r) -> str:
    out, cur = [], []
    for s in sents:
        cur.append(s)
        if len(cur) >= r.randint(3, 5):
            out.append(" ".join(cur))
            cur = []
    if cur:
        out.append(" ".join(cur))
    return "\n\n".join(out) if out else "(no further notes)"


# ------------------------------------------------------------------ plan
def _fill(w: _World):
    """Stated filler so every site appears and documents carry volume; never touches asked cells."""
    r = w.r
    for s in w.sites:
        slots = [sl for sl in SLOTS if (s, sl) not in w.cells and (sl != "person" or len(w.people) > 6)]
        r.shuffle(slots)
        for sl in slots[: 2 + (w.level >= 3)]:
            w.stated(s, sl)


def _plan(gen: dict, release_seed: str, case_id: str) -> dict:
    level = int(gen.get("level", 1))
    if level not in LEVELS:
        raise _gen.GeneratorError(f"knowgap: unknown level {level}")
    seed = _gen.instance_seed(FAMILY, release_seed, case_id, level, gen.get("key"))
    for attempt in range(50):
        r = rng(seed, f"world{attempt}")
        tk = gen.get("theme") or r.choice(sorted(THEMES))
        w = _World(tk, level, r)
        try:
            pairs = [build_pair(w, k) for k in LEVELS[level]["pairs"]]
            _fill(w)
        except RuntimeError:
            continue
        docs = _render(w)
        break
    else:
        raise _gen.GeneratorError("knowgap: could not build a world")
    qs = []
    for pi, p in enumerate(pairs):
        for q in p["qs"]:
            qs.append({**q, "pair": pi, "kind": p["kind"]})
    order = list(range(len(qs)))
    # shuffle so the two sides of a pair are never adjacent
    for _ in range(200):
        r.shuffle(order)
        if all(qs[order[i]]["pair"] != qs[order[i + 1]]["pair"] for i in range(len(order) - 1)):
            break
    items = []
    for n, oi in enumerate(order, 1):
        q = qs[oi]
        items.append({"n": n, "text": q["text"], "slot": q["slot"], "truth": q["truth"], "lure": q["lure"],
                      "side": q["side"], "pair": q["pair"], "kind": q["kind"], "note": q["note"]})
    surnames = sorted({p.split()[-1] for p in w.people} | {v.split()[-1] for v in _all_people(w)})
    return {"level": level, "theme": tk, "docs": docs, "items": items, "pairs": len(pairs),
            "kinds": [p["kind"] for p in pairs], "sites": w.sites, "offset_n": w.offset_n}


def _all_people(w):
    out = []
    for c in w.cells.values():
        for k in ("v", "v1", "v2", "old", "new"):
            if isinstance(c.get(k), str) and " " in c[k] and not c[k][:4].isdigit():
                out.append(c[k])
    for (s, sl), v in w.dcells.items():
        if sl == "person":
            out.append(v)
    return out


def _prompt(plan: dict) -> str:
    parts = ["Rules:"] + [f"{i}. {t}" for i, t in enumerate(RULES, 1)]
    parts.append("")
    parts.append("Documents:")
    for i, (t, body) in enumerate(plan["docs"], 1):
        parts.append(f"\n=== Document {i}: {t} ===\n{body}")
    parts.append("\nQuestions:")
    for it in plan["items"]:
        parts.append(f"{it['n']}. {it['text']}")
    parts.append("\nAnswer every question in order, one line each: the question number, a full stop, the word "
                 "ANSWER, a colon, then the value, or the words NOT STATED, or the word CONFLICT.")
    return "\n".join(parts)


# ------------------------------------------------------------------ grading
_ANS_LINE = re.compile(r"^[ \t>*_#\-\"'“‘•]*(?:Q\s*)?(\d{1,2})\s*[.):]?[ \t*_]*answer[ \t*_]*[:\-–—][ \t*_]*(.*)$",
                       re.I | re.M)
#: fallback when a reply has no "ANSWER" lines at all: "N. <answer>" / "N: <answer>"
_BARE_LINE = re.compile(r"^[ \t>*_#\-\"'“‘•]*(?:Q\s*)?(\d{1,2})\s*[.):][ \t*_]+(.*)$", re.M)
_NS_RE = re.compile(r"^(?:not\s+(?:stated|given|specified|provided|mentioned|known|determinable)|unknown|"
                    r"cannot\s+be\s+(?:determined|worked\s+out)|can'?t\s+be\s+determined|undetermined|"
                    r"insufficient|no\s+(?:data|information|figure|value)|n/?a\b|none\s+stated|not_stated)", re.I)
_CF_RE = re.compile(r"^(?:conflict|conflicting|(?:the\s+)?(?:sources|documents)\s+(?:conflict|disagree)|"
                    r"contradict|inconsistent)", re.I)
_NUM = re.compile(r"-?\d{1,3}(?:[,   ]\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")
_ISO = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_DMY = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(MONTHS) + r")\s*,?\s+(\d{4})", re.I)
_MDY = re.compile(r"(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?\s*,?\s+(\d{4})", re.I)


def _clean(field: str) -> str:
    f = field.strip()
    f = re.sub(r"^[\s*_`\"'“‘\[\(]+", "", f)
    return f.strip()


def _lines(text: str) -> dict:
    # per question: its "N. ANSWER: x" line when there is one, else its bare "N. x" line (a reply may mix
    # the two shapes line by line: R1-8B wrote "1. ANSWER: 790" then "2. CONFLICT", rig screen 2026-11-14)
    out = {}
    for rx in (_ANS_LINE, _BARE_LINE):
        for m in rx.finditer(text):
            n = int(m.group(1))
            if n not in out:
                out[n] = _clean(m.group(2))
    return out


def classify(field: str) -> str:
    f = _clean(field)
    if _NS_RE.match(f):
        return "NS"
    if _CF_RE.match(f):
        return "CF"
    return "V" if f else "EMPTY"


def _parse_num(f: str):
    m = _NUM.search(f.replace("€", " "))
    if not m:
        return None
    s = re.sub(r"[,   ]", "", m.group(0))
    try:
        val = float(s)
    except ValueError:
        return None
    tail = f[m.end(): m.end() + 12].lower()
    return val, tail


_DMY_NUM = re.compile(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})(?!\d)")
_DMY_COMPACT = re.compile(r"(?<!\d)(\d{2})(\d{2})(20\d{2})(?!\d)")


def _parse_date(f: str):
    """ISO, written dates, and day-first numeric dates (DD-MM-YYYY, DD/MM/YYYY, DD.MM.YYYY, DDMMYYYY): the
    documents write dates day first, so a day-first numeric answer is read day first (never month first)."""
    m = _ISO.search(f)
    try:
        if m:
            return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        m = _DMY_NUM.search(f) or _DMY_COMPACT.search(f)
        if m:
            return _dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        m = _DMY.search(f)
        if m:
            return _dt.date(int(m.group(3)), MONTHS.index(m.group(2).capitalize()) + 1, int(m.group(1))).isoformat()
        m = _MDY.search(f)
        if m:
            return _dt.date(int(m.group(3)), MONTHS.index(m.group(1).capitalize()) + 1, int(m.group(2))).isoformat()
    except ValueError:
        return None
    return None


def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z ]", "", s.lower()).strip()


def value_ok(field: str, it: dict) -> bool:
    truth, slot = it["truth"], it["slot"]
    f = _clean(field)
    if slot == "date":
        return _parse_date(f) == truth
    if slot == "person":
        nf = _norm_name(f)
        full = _norm_name(truth)
        if full in nf:
            return True
        # the surname alone is accepted (surnames are unique within an instance)
        head = nf.split()
        return bool(head) and head[0] == full.split()[-1] and len(head) <= 2
    p = _parse_num(f)
    if p is None:
        return False
    val, tail = p
    if slot == "weight" and re.match(r"\s*(t\b|tonne|ton\b|tons\b)", tail):
        val *= 1000                                    # a value written in tonnes is accepted when converted right
    return abs(val - float(truth)) < 0.5


def question_ok(field: str | None, it: dict) -> tuple[bool, str]:
    if field is None:
        return False, "missing"
    cls = classify(field)
    if it["truth"] == NS:
        return cls == "NS", cls
    if it["truth"] == CF:
        return cls == "CF", cls
    return (cls == "V" and value_ok(field, it)), cls


def score_answer(answer: str, cfg: dict) -> dict:
    from ..api import strip_thinking_tags
    text = strip_thinking_tags(answer or "")
    got = _lines(text)
    per_q = {}
    for it in cfg["items"]:
        f = got.get(it["n"])
        ok, cls = question_ok(f, it)
        per_q[it["n"]] = (ok, cls, (f or "")[:40])
    results = []
    for p in range(cfg["pairs"]):
        side = {it["side"]: it for it in cfg["items"] if it["pair"] == p}
        v, d = side["value"], side["decline"]
        okv, okd = per_q[v["n"]][0], per_q[d["n"]][0]
        det = (f"value q{v['n']} {'ok' if okv else 'got ' + repr(per_q[v['n']][2])}; "
               f"{'conflict' if d['truth'] == CF else 'decline'} q{d['n']} {'ok' if okd else 'got ' + repr(per_q[d['n']][2])}")
        results.append({"id": f"p{p + 1}-{v['kind']}", "pass": okv and okd, "detail": det,
                        "value_ok": okv, "decline_ok": okd})
    k = len(results)
    sc = sum(r["pass"] for r in results) / k if k else 0.0
    nq = len(cfg["items"])
    qacc = sum(1 for x in per_q.values() if x[0]) / nq if nq else 0.0
    return {"score": round(sc, 6), "results": results, "n_elements": k, "question_accuracy": round(qacc, 6),
            "per_question": {n: {"ok": x[0], "class": x[1]} for n, x in per_q.items()}}


def grade_answer(answer: str, cfg: dict) -> dict:
    r = score_answer(answer, cfg)
    return {"grade": "pass" if r["score"] >= 1.0 - 1e-9 else "fail",
            "detail": f"{sum(x['pass'] for x in r['results'])}/{r['n_elements']} pairs; questions {r['question_accuracy']:.2f}",
            "score": r["score"], "rate": r["score"], "results": [
                {"id": x["id"], "pass": x["pass"], "detail": x["detail"]} for x in r["results"]]}


def elements(answer: str, materialized: dict) -> list[dict]:
    return grade_answer(answer, materialized["grader_config"])["results"]


# ------------------------------------------------------------------ reference, strategies, mutants
def _fmt(pairs) -> str:
    return "\n".join(f"{n}. ANSWER: {a}" for n, a in pairs)


def _key(it: dict) -> str:
    return str(it["truth"])


def strategy(plan_or_cfg: dict, name: str) -> str:
    """Named bad strategies (the floors)."""
    items = plan_or_cfg["items"]
    if name == "decline":
        return _fmt((it["n"], NS) for it in items)
    if name == "conflict":
        return _fmt((it["n"], CF) for it in items)
    if name == "copy":           # copy-nearest / always-answer: the right value where there is one, else the lure
        return _fmt((it["n"], _key(it) if it["side"] == "value" and it["lure"] is None else str(it["lure"]))
                    for it in items)
    if name == "answer_all":     # oracle values, but never declines (lure on the decline side)
        return _fmt((it["n"], _key(it) if it["side"] == "value" else str(it["lure"])) for it in items)
    if name == "lookup_only":    # direct stated lookups right, NOT STATED for everything that needs more
        simple = {"lookup", "decoy"}
        return _fmt((it["n"], _key(it) if it["kind"] in simple and it["side"] == "value" else NS) for it in items)
    if name == "empty":
        return ""
    raise ValueError(name)


STRATEGIES = ("decline", "conflict", "copy", "answer_all", "lookup_only", "empty")


def _mutants(plan: dict) -> list[str]:
    return [strategy(plan, s) for s in ("decline", "conflict", "copy", "answer_all", "empty")]


def _full(gen: dict, release_seed: str, case_id: str) -> dict:
    plan = _plan(gen, release_seed, case_id)
    plan["prompt"] = _prompt(plan)
    plan["config"] = {"items": plan["items"], "pairs": plan["pairs"], "theme": plan["theme"]}
    return plan


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    plan = _full(gen, release_seed, case_id)
    nq = len(plan["items"])
    max_tokens = CEILING[plan["level"]]
    return {
        "id": case_id, "category": "reasoning", "system": SYSTEM, "prompt": plan["prompt"],
        "temperature": 0.0, "max_tokens": max_tokens,
        "grader": "knowgap", "grader_config": plan["config"], "generator": dict(gen),
        "meta": {"family": FAMILY, "level": plan["level"], "theme": plan["theme"], "pairs": plan["pairs"],
                 "questions": nq, "elements": plan["pairs"], "kinds": plan["kinds"],
                 "docs": len(plan["docs"]),
                 "budget": {"max_tokens": max_tokens},
                 "prompt_tokens_est": round((len(plan["prompt"]) + len(SYSTEM)) / 4),
                 "authorship": {"structure": "claude", "bank": "generated (no bank)"}},
    }


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    plan = _full(gen, release_seed, case_id)
    return _fmt((it["n"], _key(it)) for it in plan["items"])


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    return _mutants(_full(gen, release_seed, case_id))


def grade(answer: str, materialized: dict) -> float:
    return float(score_answer(answer, materialized["grader_config"])["score"])
