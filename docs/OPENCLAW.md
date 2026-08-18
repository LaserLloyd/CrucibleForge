# Using Gauntlet with OpenClaw (or any agent framework)

Gauntlet is a plain CLI, so any agent that can run shell commands can drive it.

1. **Import your gateway's providers/models** (OpenAI-compatible providers,
   `${ENV}` key references and per-model prices map 1:1):

   ```bash
   gauntlet import-openclaw --openclaw-json ~/.openclaw/openclaw.json --write
   gauntlet status
   ```
   Local/loopback providers are skipped unless `--include-local`; literal API
   keys are never copied — export the same env var for Gauntlet.

2. **Give agents a skill.** A minimal `SKILL.md`:

   ```markdown
   ---
   name: gauntlet
   description: Benchmark LLMs (hard math/reasoning/coding/tools/long-context + RP/safety/speed) to pick a model for a role.
   user-invocable: true
   ---
   Run from the gauntlet checkout:
   - `uv run gauntlet status`
   - `uv run gauntlet run --models <a>,<b> --difficulty hard --yes`
   - `uv run gauntlet all --smoke --models <a> --yes`
   - `uv run gauntlet report` → read results/report.md (Hard % column)
   Rules: remote runs cost money (say so); a run unloads what LM Studio is serving.
   ```

3. **Judge on a remote model** when no local uncensored judge is loaded:
   `gauntlet judge --judge deepseek:deepseek-v4-flash` (creative rows may be
   refused by hosted judges — the calibration canary aborts if so; the
   `reference` rubric always works).

4. **GUI over a tailnet:** `gauntlet gui --host 0.0.0.0 --port 8777` prints a
   tokenised URL; state-changing calls are same-origin only.
