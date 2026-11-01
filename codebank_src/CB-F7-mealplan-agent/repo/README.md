# mealplan

Weekly meal planner: recipes, a week plan, the shopping list and printable
recipe cards.

* `recipes.load_recipes(path)` reads `recipes.json`; recipes are cached
  (`recipes.get(name)`), and the cached recipe is the book's copy: nothing
  may change it.
* `scaling.scaled(recipe, servings)` returns a NEW recipe for that many
  servings (quantities multiplied by servings / recipe servings). Items with
  unit "to taste" are copied unchanged.
* `plan.Week` holds (day, recipe name, servings) entries; `plan.shopping(week)`
  totals the scaled ingredients of the whole week: same ingredient (name
  compared case-insensitively, trimmed) and same unit family are added up
  (mass in g, volume in ml; `units.to_base`), counts ("pcs") separately,
  "to taste" items listed once with no quantity. Output sorted by name:
  `[(name, quantity or None, unit)]` with the name trimmed and lowercased,
  unit "g", "ml", "pcs" or "to taste"; quantities rounded to 1 decimal.
  The result depends only on the week: planning it again gives the same list.
* `cards.card(recipe)` prints a recipe card; quantities are shown with
  `fmt.amount(x)`: amounts below 1/8 are "a pinch"; otherwise the value
  closest to x among all numbers w + n/d with whole w >= 0, d in
  (1, 2, 3, 4, 8) and 0 <= n < d (ties: the smaller d), written "3",
  "1 1/2", "2/3" or "3/8" (whole part omitted when 0).
