# kitchenpane

Renders household notifications (chores, appliance alerts, calendar nudges)
on the kitchen's 40-column status display. Notes arrive from the family chat
bridge and may contain ANSI colour codes and Japanese or emoji text.

- `kitchenpane/wrap.py`: line wrapping (spec: `docs/wrap.md`)
- `kitchenpane/pane.py`: bullets and indentation
- tests: `python -m pytest tests`
