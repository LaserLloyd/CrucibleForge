# First run in Docker

The README's first run, with nothing installed but Docker (or Podman: replace `docker` with `podman`). The
container needs no GPU: the model and the judge run on OpenRouter here. Everything you keep (`models.yaml`, the
results) lives on one volume, `crucibleforge-data`. The longer guide is [DOCKER.md](DOCKER.md).

**1. Build the image and put the key in.** The key stays in your shell; `-e OPENROUTER_API_KEY` with no value hands
it to the container without writing it on the command line.

```bash
git clone https://github.com/LaserLloyd/CrucibleForge.git && cd CrucibleForge   # scrub-ok: public repo URL
docker build -t crucibleforge .
export OPENROUTER_API_KEY=sk-or-...
alias cf='docker run --rm -v crucibleforge-data:/data -e OPENROUTER_API_KEY -e CRUCIBLEFORGE_ALLOW_UNSANDBOXED=1 crucibleforge'
cf status          # first start: writes /data/models.yaml from the example; the openrouter provider shows "key ... (set)"
```

The container is the sandbox for the coding answers, which is why `CRUCIBLEFORGE_ALLOW_UNSANDBOXED=1` is set.

**2. Add one model.** Prices are USD per million tokens from the model's OpenRouter page. The judge needs no
entry: `models.yaml` already holds the OpenRouter Qwen judge with its price and settings.

```bash
cf models add gpt-6-luna openrouter openai/gpt-6-luna --context-length 1050000 --price-in 0.1 --price-out 0.5
```

**3. Plan, then run.** `--plan` calls nothing and prints the rows, the minutes and the price figures.

```bash
cf all --models gpt-6-luna --judge openrouter:qwen/qwen3.8-27b --plan
cf all --models gpt-6-luna --judge openrouter:qwen/qwen3.8-27b --spend-cap-usd 6
```

Any judge works, local or hosted. The scores published on MakoBench are judged by the same model run locally at
8-bit (Q8). A hosted judge is billed by its provider, is not part of the model's Run cost and is not limited by the
run's spend cap. A judge on your own machine: add a provider whose `base_url` is
`http://host.docker.internal:<port>/v1` and pass `--judge <provider>:<model-id>`. A hosted judge receives every reply it judges, NSFW+ included; pass `--no-nsfw-plus` to leave NSFW+ out. NSFW+ never enters Overall.

**4. Read the board.** The reports are on the volume; copy them out:

```bash
cf cat /data/results/report.md | head -40
cf cat /data/results/report.html > report.html     # open it in a browser
```

Overall /20 is Code plus Chat and sets the rank; Code /10 is code that hidden tests run; Chat /10 is role-play,
memory, co-writing and everyday instructions, checked by code and the judge; `±` is the 90% range of one run.
