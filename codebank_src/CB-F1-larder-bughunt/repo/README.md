# larder

Pantry inventory + shopping list for the household. SQLite-backed.

* `larder.units`: units and conversion. Mass: g, kg; volume: ml, l, tsp
  (5 ml), tbsp (15 ml), cup (240 ml); count: pcs. Unit names are
  case-insensitive. Converting between families (mass <-> volume, anything
  <-> pcs) raises ValueError. Conversions are exact (Fractions inside),
  results are returned as float.
* `larder.store.Pantry`: what is in the cupboard. `add(item, qty, unit)` adds
  to what is there (converting to the unit the item is already stored in);
  `stock(item)` returns `(qty, unit)` or None.
* `larder.recipes`: recipes and scaling.
* `larder.shopping.missing(pantry, recipe, servings)`: what to buy, in the
  recipe's units, smallest positive amounts only, sorted by item name.
