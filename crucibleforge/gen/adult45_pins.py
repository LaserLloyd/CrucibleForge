"""sha256 (first 16 hex) of each adult45 / unc_know JSON bank, rewritten by tools/pin_adult45_banks.py.

The banks are DATA: the family-source digest in chains.revision() does not see them. The loaders refuse a
bank that does not match its pin (an unreviewed edit fails loud), and chains.revision() adds the digest of
each instance's OWN item (adult45.item_digest / unc_know.item_digest), so an edited card makes its stored
rows stale while every untouched card's rows stay current. This module is not a family source on purpose:
moving a pin never makes rows stale by itself."""

BANK_SHA = {
    "nsfw/adult45/sessions.json": "734438f177702198",
    "nsfw/adult45/probes.json": "97a7b87c5ee973bf",
    "nsfw/adult45/dwell.json": "7beab569af69b00b",
    "nsfw/adult45/writing.json": "7103f0ec4bb05c11",
    "nsfw/adult45/taboo-asks.json": "6d9f97f55c18b8f7",
    "nsfw/adult45/unc-asks.json": "3aaac79c777cee72",
    "nsfw/adult45/open.json": "db7c0ec698d7ec00",
    "nsfw/adult45/bodies.json": "bb6d72324361e234",
    "nsfw/adult45/ground.json": "6188c44791a5e839",
    "nsfw/nsfw_rp/set-1.json": "dbde9aade8832418",
    "nsfw/uncensored/unc-know-bank.json": "31240f94b7920c3e",
}
