# Using CrucibleForge with OpenClaw (or any agent framework)

CrucibleForge is a plain CLI, so any agent that can run shell commands can drive it.

1. **Import your gateway's providers/models** (OpenAI-compatible providers,
   `${ENV}` key references and per-model prices map 1:1):

   ```bash
   crucibleforge import-openclaw --openclaw-json ~/.openclaw/openclaw.json --write
   crucibleforge status
   ```
   Local/loopback providers are skipped unless `--include-local`; literal API
   keys are never copied — export the same env var for CrucibleForge.

2. **Give agents a skill.** A minimal `SKILL.md`:

   ```markdown
   ---
   name: crucibleforge
   description: Benchmark LLMs (hard math/reasoning/coding/tools/long-context + RP/safety/speed) to pick a model for a role.
   user-invocable: true
   ---
   Run from the crucibleforge checkout:
   - `uv run crucibleforge status`
   - `uv run crucibleforge run --models <a>,<b> --difficulty hard --yes`
   - `uv run crucibleforge all --smoke --models <a> --yes`
   - `uv run crucibleforge report` → read results/report.md (Hard % column)
   Rules: remote runs cost money (say so); a run may unload what your local server is serving (LM Studio / StudioForge).
   ```

3. **Judge on a remote model** when no local uncensored judge is loaded:
   `crucibleforge judge --judge deepseek:deepseek-v4-flash` (creative rows may be
   refused by hosted judges — the calibration canary aborts if so; the
   `reference` rubric always works).

4. **GUI over a tailnet:** `crucibleforge gui --host 0.0.0.0 --port 8777` prints a
   tokenised URL; state-changing calls are same-origin only.
