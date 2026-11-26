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

**Windows, PowerShell** (tested with Docker Desktop 4.95 on WSL 2, PowerShell 7, Windows 11):

```powershell
mkdir makobench
docker run --rm -it -e OPENROUTER_API_KEY=sk-or-... -v "${PWD}\makobench:/data" ghcr.io/laserlloyd/crucibleforge:4.5.1 test openai/gpt-6-luna
```

Not tested on Windows yet: a folder path with spaces (the quotes are meant to cover it), Docker Desktop's
first-time "share this folder?" question (answer Share), and the published image (the test used one built locally).
In `cmd.exe` write `"%cd%\makobench:/data"` instead.

**macOS, Terminal** (not tested yet): the Linux command, unchanged.

**Check first, for free.** Write `check` instead of `test` to run only the checks: the folder, the key (and your
balance), the model id, `models.yaml`, and the cost estimate. Nothing is spent. It prints one `PASS` or `FIX` line
for each, and `test` runs the same checks before its first paid call. From the offline test (a stand-in provider
named `fake/fake-model`, so the prices and balance are the stand-in's):

```text
  PASS  folder  your results folder is writable, 298 GB free
  PASS  key     accepted by OpenRouter; balance $5.00
  PASS  config  models.yaml is valid
  PASS  model   fake/fake-model on OpenRouter ($0.10 in / $0.50 out per million tokens)
  PASS  judge   Qwen 3.8 27B through OpenRouter
  PASS  plan    about $0.77 for the model and the judge; the run stops at $1.75 (about twice that)
```

## What happens

1. Within a second it says what it is checking, then one `PASS` line per check: the folder, the key, the model.
2. It looks the model up on OpenRouter, with its price, and plans the test. Planning takes one to three minutes (it
   builds the generated questions to size their answers); a line every 15 seconds says which step it is on.
3. It prints the plan: the model, the judge, whether NSFW+ (adult fiction) is included, the expected cost of the model and
   of the judge (about, and up to, for the standard test, and what the bonus packs add if they all open), and a rough
   time. It sets a spend cap of about twice the estimate (model and judge together, rounded up, at least $1): a guard
   against a runaway, not a budget. `--cap <USD>` after the model id sets another.
4. It asks `Continue? [y/N]`. Nothing is spent before you answer `y`. (`--yes` skips the question: it is for
   scripts. The commands on this page ask, which is why they have `-it`.)
5. It runs, with one progress line every 30 seconds (`42 of 279 answers · $0.21 so far · about 14 min left for the
   answers`, then `judging the answers`) and a plain `note:` for anything worth knowing; the engine's full log goes to
   `results/run.log` in your folder (`-e CF_VERBOSE=1` shows it in the terminal instead). Then it prints the result. Each total is printed with its split: the standard score
   plus the bonus points of the harder packs the model opened (the rank is on the total; today's maximum is 17 for
   Code and Chat, 34 for Overall). For `openai/gpt-6-luna` (rootless Docker, 2026-11-25):

```text
Overall 21.6 of 34 = 16.9 standard + 4.7 bonus
Code    11.9 of 17 = 9.0 standard + 3.0 bonus
Chat    9.7 of 17 = 8.0 standard + 1.7 bonus
Adult (NSFW+, never part of Overall) 9.8 of 26 = 8.9 standard + 0.9 bonus
Judged by Qwen 3.8 27B through OpenRouter; MakoBench's published scores use the local 8-bit (Q8) judge.
Cost of this run: model $… + judge $… = $… (as billed per call where the provider reports it)
  (the board's Run cost: $… counts one standard Code and Chat run only, so models compare)
Open results/report.html in the folder you mounted with -v (for example makobench) in your browser (report.md is the same board as text).
```

The cost line is this run's own spend, every part of it (NSFW+, bonus rounds and retries included), as OpenRouter
bills each call; when the folder's results took several runs, the next line gives their total. The board's Run
cost is a fixed measure for comparing models. Run the same command again on a finished folder and it says
`Nothing left to run: the results are complete` at once, with what the results cost:
`Nothing new was run this time; the results in this folder cost $… in total: model $… + judge $…`.

The adult line can instead read `Adult (NSFW+): no number yet (...)`: the judge did not class every adult answer
(its calls failed, or its check did not pass). The answers are kept; the same command asks the judge again.

**Stopped, or the computer slept?** Run the same command again. Finished answers are kept; it continues where it
stopped and shows what is still owed and what it will cost before it spends anything. Ctrl-C stops cleanly: it
first lets the answers already in progress finish (it says how many and about how long; with a slow local server
that can be minutes), and a second Ctrl-C stops at once (those answers are asked again next time).

**Options** go after `test`: `--no-adult` (or `--no-nsfw-plus`) leaves out NSFW+, the adult-fiction page that is
scored apart and never added to Overall; `--yes` does not ask.

## Open the results in your browser

The report is a file in your folder (`makobench/results/report.html`); double-click it. To have the container show
it instead, publish one port and add `view`. It serves the report, read only, until you press Ctrl-C:

```bash
docker run --rm -it -p 127.0.0.1:8377:8377 -v "$PWD/makobench:/data" ghcr.io/laserlloyd/crucibleforge:4.5.1 view
```

```powershell
docker run --rm -it -p 127.0.0.1:8377:8377 -v "${PWD}\makobench:/data" ghcr.io/laserlloyd/crucibleforge:4.5.1 view
```

Then open <http://localhost:8377>. To watch a test as it runs, add the same `-p 127.0.0.1:8377:8377` to the test
command and `--serve` after the model id: the page shows the model, the step, the answers stored, the cost so far
and the start time, refreshes itself, and turns into the report when the run ends. The test then prints
`Your results are open at http://localhost:8377 (press Ctrl-C to close)` and keeps the page open until Ctrl-C.
Without `--serve` (or `view`) nothing listens.

In Docker Desktop the published port shows as `8377:8377` in the container's row, and clicking it opens the page.
A port shows there only when it is published with `-p`; the image's `EXPOSE` line alone does not do it. (Not tried
on Windows yet; tested on Linux with Podman.)

The page serves only the report (`/`, `/report.html`) and `/report.md`: never `models.yaml`, your keys, the spend
records or the answers themselves, whatever address is asked for. **Anyone who can reach the port can read the
results**: `127.0.0.1:` before the port keeps it on this computer; leave it out only if you mean to share the page on
your network. Another port: `-e PORT=8378 -p 127.0.0.1:8378:8378` (or `--port 8378` after `view` or `test`).

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

On Windows (Docker Desktop, PowerShell), `host.docker.internal` works without `--add-host`:

```powershell
docker run --rm -it -e OPENROUTER_API_KEY=$env:OPENROUTER_API_KEY -v "${PWD}\makobench:/data" ghcr.io/laserlloyd/crucibleforge:4.5.1 test --local http://localhost:11434 qwen3:8b
```

If your server asks for a key (vLLM or llama-server started with `--api-key`, LM Studio with authentication on,
LiteLLM, most shared servers), add `-e LOCAL_API_KEY=<the server's key>` (PowerShell, from a variable:
`-e LOCAL_API_KEY=$env:LOCAL_API_KEY`). A judge on another server takes its own `-e JUDGE_LOCAL_API_KEY=...`; on the
same server it uses `LOCAL_API_KEY`. Like the OpenRouter key, these stay in the container's memory: they are not
written to the results folder, not printed, and the model's code cannot see them. Run `check` first: it shows the
server's model ids when yours is not one of them (give the id exactly as listed) and the context window it reports.

The test sends your server only as many requests at once as it serves: it asks the server (llama-server, StudioForge
and LM Studio-style servers say it) and otherwise sends one at a time; the plan shows the number and where it came
from. `--parallel N` sets it yourself, for example after starting llama-server with `-np 4` or Ollama with
`OLLAMA_NUM_PARALLEL=4`. Time a request waits in the server's queue never counts against it.

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

Keeping that code out of your results folder needs a folder whose file system keeps owners apart. A normal folder on
Linux, a Docker volume and a Windows folder shared by Docker Desktop (which stores owners) do. When the folder's
file system cannot (some network shares, Docker Desktop folders without owner support), the run still works, and
its first lines say so: `Note: this folder's file system does not keep owners apart`. Your key and the network
stay out of that code's reach either way.

## If something goes wrong

Every message ends with what to do, and the command stops with its own exit code (`echo $?` in a Linux or macOS
shell, `$LASTEXITCODE` in PowerShell). Nothing is spent before the plan is shown and you answer `y`.

| You see (the message starts with) | Exit | What to do |
|---|---|---|
| `No results folder was given` | 28 | Add `-v` with a folder (`mkdir makobench` first); the message shows the command for your shell. |
| `Cannot write to the results folder you gave with -v` | 24 | The message names the fix for your system: on Windows or a Mac, use a folder inside your user folder; on Fedora or RHEL, add `:Z`; on Linux, `mkdir` the folder yourself (or `sudo chown -R $(id -u):$(id -g) makobench`). |
| `This container cannot switch to the data folder's owner` | 24 | You added `--cap-drop`: also add `--user $(id -u):$(id -g)`. |
| `There is not enough free disk space` | 32 | Free some space (Docker Desktop: Settings, Resources, Disk image size; or `docker system prune`), then run the same command. |
| `This results folder was last used by a newer CrucibleForge` | 35 | Use the newer image, or a new folder for this one. Nothing in the folder was changed. |
| `models.yaml has a mistake` | 31 | Fix the line it names, or rename `models.yaml` to `models.yaml.old`: the next run writes a fresh one and keeps your results. |
| `No OpenRouter key was given.` | 20 | Put the key in the command: `-e OPENROUTER_API_KEY=sk-or-...`. |
| `The OpenRouter key is empty.` | 20 | Put the key itself after the `=` sign. |
| `The OpenRouter key has quote marks or spaces in it.` | 21 | Give it without quote marks inside; in PowerShell from a variable: `-e OPENROUTER_API_KEY=$env:OPENROUTER_API_KEY`. |
| `This is not an OpenRouter key:` | 21 | It is another service's key. Make one at [openrouter.ai/keys](https://openrouter.ai/keys) (it starts with `sk-or-`). |
| `OpenRouter rejected the key` | 21 | Copy the key again from [openrouter.ai/keys](https://openrouter.ai/keys). |
| `The OpenRouter account is out of credit` | 22 | Add credit on OpenRouter, then run the same command: it continues. |
| `OpenRouter is limiting requests right now (HTTP 429).` | 30 | Wait a minute and run the same command again; nothing is lost. |
| `OpenRouter has no model called` | 23 | Use one of the ids it suggests, exactly as written. |
| `The container could not look up` | 29 | No internet or no name lookups from containers: check the connection, restart Docker. Behind a company proxy add `-e HTTPS_PROXY=http://<proxy>:<port>`. |
| `The container could not reach` | 29 | As above: the connection, or a proxy (`-e HTTPS_PROXY=...`). |
| `Something between this computer and` | 29 | A company proxy or antivirus replaces HTTPS certificates: add `-e HTTPS_PROXY=...` and `-e SSL_CERT_FILE=/data/<certificate>.pem` with the certificate file in your results folder. |
| `This computer's clock looks wrong` | 33 | Set the date and time correctly (automatic time), restart Docker, run again. |
| `Nothing answered at` | 26 | Your model server is not running, or listens only on localhost: start it on `0.0.0.0` (see above). On Linux add `--add-host=host.docker.internal:host-gateway`; with rootless Docker use your computer's network address. |
| `Your model server asks for a key:` | 37 | Add `-e LOCAL_API_KEY=<the server's key>` (for `--judge-local` on another server: `-e JUDGE_LOCAL_API_KEY=...`). |
| `The model server rejected the key in` | 38 | Copy the key again from the server's settings into the variable the message names. |
| `The model server stopped answering` | 39 | Nothing is lost. For your own server, run the same command with `--parallel 1` (it got more requests at once than it serves); for a hosted model, wait a few minutes and run it again. |
| `has no model called` | 23 | The server is up but that model is not loaded: load it, or use a name from the list it prints. |
| `context window is too small for this test` | 34 | Restart your server with the context it names (`llama-server -c N`, LM Studio: Context Length, Ollama: `OLLAMA_CONTEXT_LENGTH=N`). |
| `No terminal to answer "Continue?" on.` | 25 | Add `-it` to `docker run`, or `--yes` after `test`. |
| `Not started. Nothing was spent.` | 25 | You answered no. |
| `The run reached its spend cap` | 36 | Nothing is lost. The message says what was spent. Run the same command again: it continues, and the rest gets its own cap of about twice its estimate. If the model writes long answers, add the higher `--cap` the message names. |
| `The results page could not open: something already uses port` | 40 | Another program in the container has the port: give another one, `-e PORT=8378 -p 127.0.0.1:8378:8378`. If Docker itself says `port is already allocated`, the port is taken on your computer: change only the first number, `-p 127.0.0.1:8378:8377`. |
| `The run ended with code` | 27 | Read the lines above it; run the same command again to continue. |
| `Stopped. Nothing is lost` | 130 | You pressed Ctrl-C (or the computer slept). Run the same command again to continue. |
| `Note: this folder's file system does not keep owners apart` | - | The run goes on; see Safety above. |
| `exec format error` | - | The image does not match your computer's processor: `docker pull` the image again (it is published for amd64 and arm64), or add `--platform linux/amd64`. |
| `WARNING: Support for cgroup v1 is deprecated` | - | Printed by `docker info` on some Docker Desktop setups; harmless. |
| The files in the folder belong to another user | - | Create the folder yourself before the first run (`mkdir makobench`); the test then writes as you. |

Want the full program (other providers, the GUI, many models, compose)? [DOCKER.md](DOCKER.md).
