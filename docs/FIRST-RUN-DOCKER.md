# Test a model with one command

All you need is [Docker](https://docs.docker.com/get-docker/) (or Podman) and an
[OpenRouter key](https://openrouter.ai/keys) with a few dollars of credit. No GPU, no setup.

## The command

Make an empty folder for the results, then run the test. Put your key after `OPENROUTER_API_KEY=` and the
OpenRouter id of the model after `test` (the id is shown under the model's name on
[openrouter.ai/models](https://openrouter.ai/models)).

**Linux** (tested with Podman and with rootless Docker 29):

```bash
mkdir makobench
docker run --rm -it -e OPENROUTER_API_KEY=sk-or-... -v "$PWD/makobench:/data" ghcr.io/laserlloyd/crucibleforge:4.5.1 test openai/gpt-6-luna
```

On Fedora, RHEL or another SELinux system write `"$PWD/makobench:/data:Z"`. With Podman, write `podman` instead of
`docker`; nothing else changes.

**Windows, PowerShell** (not tested yet):

```powershell
mkdir makobench
docker run --rm -it -e OPENROUTER_API_KEY=sk-or-... -v "${PWD}\makobench:/data" ghcr.io/laserlloyd/crucibleforge:4.5.1 test openai/gpt-6-luna
```

**macOS, Terminal** (not tested yet): the Linux command, unchanged.

## What happens

1. Within a second it says what it is checking: the folder, the key, the model.
2. It looks the model up on OpenRouter, with its price, and plans the test.
3. It prints the plan: the model, the judge, whether NSFW+ (adult fiction) is included, the expected cost of the model and
   of the judge (about, and up to, for the standard test, and what the bonus packs add if they all open), and a rough
   time. It sets two spend limits at about twice the estimate.
4. It asks `Continue? [y/N]`. Nothing is spent before you answer `y`. (`--yes` skips the question.)
5. It runs, one line per answer, then prints the result. Each total is printed with its split: the standard score
   plus the bonus points of the harder packs the model opened (the rank is on the total; today's maximum is 17 for
   Code and Chat, 34 for Overall). For `openai/gpt-6-luna` (rootless Docker, 2026-11-25):

```text
Overall 21.6 of 34 = 16.9 standard + 4.7 bonus
Code    11.9 of 17 = 9.0 standard + 3.0 bonus
Chat    9.7 of 17 = 8.0 standard + 1.7 bonus
Adult (NSFW+, never part of Overall) 9.8 of 26 = 8.9 standard + 0.9 bonus
Judged by Qwen 3.8 27B through OpenRouter; MakoBench's published scores use the local 8-bit (Q8) judge.
Judge cost: $0.2149 for 892 call(s) to qwen/qwen3.8-27b on openrouter
Open results/report.html in the folder you mounted with -v (for example makobench) in your browser (report.md is the same board as text).
```

**Stopped, or the computer slept?** Run the same command again. Finished answers are kept; it continues where it
stopped and shows what is still owed and what it will cost before it spends anything. Ctrl-C stops cleanly.

**Options** go after `test`: `--no-adult` (or `--no-nsfw-plus`) leaves out NSFW+, the adult-fiction page that is
scored apart and never added to Overall; `--yes` does not ask.

## What it costs

Measured on 2026-11-25 with `openai/gpt-6-luna` (NSFW+ included): $0.82 in all, taken from the OpenRouter balance (the plan had said about $0.68: $0.47 for the model, $0.21 for the judge), in 34 minutes.
A frontier model costs much more than a flash-class one: the plan's estimate is printed before anything is spent,
and the run stops at about twice that.

## The judge

The judged parts of the test (role-play, co-writing, NSFW+) are graded by Qwen 3.8 27B through
OpenRouter, billed by OpenRouter apart from the model's cost. MakoBench's published board uses the same model run
locally at 8-bit, so your numbers can differ a little from the board's. A hosted judge sees every reply it grades,
NSFW+ included; `--no-adult` leaves NSFW+ out.

## A model on your own computer

Start your model server so that it listens on all addresses, then use `--local` with its address and the model's
name as the server lists it:

```bash
docker run --rm -it --add-host=host.docker.internal:host-gateway -e OPENROUTER_API_KEY=sk-or-... -v "$PWD/makobench:/data" ghcr.io/laserlloyd/crucibleforge:4.5.1 test --local http://localhost:11434 qwen3:8b
```

- **Ollama:** start it with `OLLAMA_HOST=0.0.0.0 ollama serve`; the address is `http://localhost:11434`.
- **llama-server:** add `--host 0.0.0.0`; the address is `http://localhost:8080`.
- **LM Studio:** Developer tab, turn on "Serve on local network"; the address is `http://localhost:1234`.

`localhost` is changed to your computer's address inside the container (Docker Desktop, Podman and Linux Docker
each name it differently; the test finds it). `--add-host=host.docker.internal:host-gateway` gives that address a
name on Linux Docker; Docker Desktop and Podman have it already, and the option does no harm there. **Rootless
Docker** cannot reach `localhost` on your computer at all: use your computer's network address instead
(`hostname -I` prints it), for example `--local http://<that address>:11434 qwen3:8b`. A local model is free but slow: a 27B model that thinks at length
can take several hours. The judge still runs on OpenRouter; to judge locally too, add
`--judge-local http://localhost:1234 <judge-model>` (the key is then not needed).

## Safety

The test runs code the model wrote. That code runs as a separate user with no network, cannot read your key or
your results folder, and has limits on processes, memory, CPU time and file size; it can still read the program's
own files inside the container, and the container is not a virtual machine. Details, and the test that checks each
point: [deploy/docker.md](deploy/docker.md#coding-cases-and-the-sandbox).

## If something goes wrong

| You see | Do this |
|---|---|
| `No OpenRouter key was given.` | Put the key in the command: `-e OPENROUTER_API_KEY=sk-or-...`. |
| `OpenRouter rejected the key` | Copy the key again from [openrouter.ai/keys](https://openrouter.ai/keys). |
| `out of credit` | Add credit on OpenRouter, then run the same command: it continues. |
| `OpenRouter has no model called ...` | Use one of the ids it suggests, exactly as written. |
| `Cannot write to the results folder` | SELinux: add `:Z` after the folder. Otherwise create the folder yourself first (`mkdir makobench`). |
| `No terminal to ask "Continue?"` | Add `-it` to `docker run`, or `--yes` after `test`. |
| `Nothing answered at http://...` | Your model server is not running, or listens only on localhost (see above). With rootless Docker, use your computer's network address instead of `localhost`. |
| `reached its spend cap` | Run the same command again: it continues with a fresh cap. |
| The files in the folder belong to another user | Create the folder yourself before the first run (`mkdir makobench`); the test then writes as you. |
| `cannot switch to the data folder's owner` | You added `--cap-drop`: also add `--user $(id -u):$(id -g)`. |

Want the full program (other providers, the GUI, many models, compose)? [DOCKER.md](DOCKER.md).
