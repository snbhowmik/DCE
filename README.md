# Biokraft Demand-Capacity Engine (DCE)

An AI-assisted planning tool for a **capacity-constrained food manufacturer**. It turns order, production, and marketing history into uncertainty-aware recommendations for:

- **allocating scarce output** between D2C and B2B, across regions;
- **placing marketing spend** where demand can actually be served and where it has historically paid off;
- **onboarding B2B accounts**: accept, defer, or phase in;
- **flagging shortfalls and surpluses** early, with mitigations that can still take effect in time.

Built for the Biokraft Foods Private Limited problem statement (cultivated chicken today; seaweed, microbial, and mycelium products in R&D).

> **Status:** in build. Implementation follows `docs/TASK.md`.

---

## Documentation map (read in this order)

| # | Doc | What it answers |
|---|---|---|
| 1 | [`docs/IDEATION.md`](docs/IDEATION.md) | Why this problem matters, the business logic, marketing-funnel concepts, strategy modes, principles |
| 2 | [`docs/PRD.md`](docs/PRD.md) | What to build: requirements, screens, success metrics |
| 3 | [`docs/ARCH.md`](docs/ARCH.md) | How: data contract, modules, forecasting, optimizer formulation, API |
| 4 | [`docs/TASK.md`](docs/TASK.md) | In what order, with acceptance criteria |
| 5 | [`docs/NOTES.md`](docs/NOTES.md) | Decision log, assumptions, integrity and evaluation logs; append after every task |

---

## Data independence (read before touching data)

Historical data comes from a **separate Data Generating Process** (`biokraft-dgp`), built by a different AI (Gemini CLI) in a different repository. This repo sees **only**:

- the data contract in `contract/` (ARCH §3), and
- CSV drops in `data/incoming/<world_id>/`.

Worlds arrive **blind**. Their regimes are revealed only after evaluation results are frozen. Never read, fetch, or reverse-engineer the DGP. See IDEATION §11.

---

## Quickstart

> Commands become valid as Phase 0 of TASK.md lands.

```bash
# prerequisites: Python 3.11+, uv, Node 20+, pnpm
make setup                          # backend (uv) + frontend (pnpm) deps
cp .env.example .env                # add ANTHROPIC_API_KEY for the AI layer

# put a world drop in place (from the DGP side)
#   data/incoming/world_01/*.csv + manifest.json

uv run dce ingest data/incoming/world_01        # validate + store
uv run dce run --world world_01 --mode STABILITY
uv run dce compare --world world_01 --modes GROWTH STABILITY D2C_EXPANSION
uv run dce onboard --world world_01 --candidate examples/candidate.yaml

make run-api                        # FastAPI on :8000  (/docs for OpenAPI)
make run-web                        # Next.js on :3000
make test                           # all tests incl. invariants
```

Or everything at once: `docker compose up`.

### Deploy on RHEL 8.10 (one command)

```bash
# on the server, from a checkout that includes data/incoming/ (it is gitignored: rsync it over)
rsync -a data/incoming/ server:/path/to/checkout/data/incoming/
sudo bash deploy/install.sh        # uv + Python 3.12, Node 20, build, ingest, precompute, systemd, nginx :80
```

Services: `dce-api` (uvicorn on 127.0.0.1:8000), `dce-web` (Next.js on 127.0.0.1:3000), nginx on :80 in front of both,
with rate limits on what-ifs and re-runs. SELinux (`httpd_can_network_connect`) and firewalld are configured.
New data drops: copy them to `/opt/dce/data/incoming/` and run `sudo -u dce bash /opt/dce/deploy/refresh-worlds.sh`.
Optional password: uncomment `auth_basic` in `/etc/nginx/conf.d/dce.conf` and add users with `htpasswd -B`.
World display names live in `config/worlds.yaml` (UI only).

### Dashboard (what reviewers see)

```bash
uv run dce precompute     # or: make precompute; plans every world in 3 modes and stores the payloads
make run-api              # terminal 1: FastAPI on :8000
make run-web              # terminal 2: dashboard on http://localhost:3000
```

The dashboard reads stored runs, so every screen loads instantly; **Re-run world** re-plans in the background.
For the AI-written brief, put a Groq key in `.env` (`GROQ_API_KEY=…`, see `.env.example`). Without a key the brief
falls back to a deterministic template. Either way, every number in it is checked against the run (D-053).

---

## Repository layout

```
docs/        IDEATION, PRD, ARCH, TASK, NOTES
contract/    data contract schemas (the only interface with the DGP)
config/      strategy modes, mitigations, scoring weights, app settings
data/        incoming/ (DGP drops, gitignored) · fixtures/ (unit tests only) · processed/
backend/dce/ ingest · demand · metrics · forecast · capacity · response ·
             optimize · risk · mitigate · onboarding · ai · eval · store · api
frontend/    Next.js dashboard
reports/     frozen evaluation reports
```

---

## Strategy modes

| Mode | Optimizes for | Posture |
|---|---|---|
| `GROWTH` | revenue + reach; favors stable, wide-reach B2B accounts | plans at P50 capacity |
| `STABILITY` | fulfillment reliability; protects B2B commitments | plans at ~P15 capacity |
| `D2C_EXPANSION` | D2C growth, only where evidence shows sustained response | P50 + capped exploration |
| `CUSTOM` | user-set weights | configurable |

Modes change the optimizer only, never the forecast (enforced by test).

---

## For AI coding agents (Claude Code etc.)

1. Read `docs/IDEATION.md` §6 (principles) before any code.
2. Take the next unchecked task in `docs/TASK.md` whose dependencies are done.
3. After each task: tick it, append a Task Log entry and any Decisions to `docs/NOTES.md`, run `make test`.
4. Never access the DGP repo or generator. Never report results on `data/fixtures/`.
5. The LLM layer may explain numbers but never create them.
6. When ARCH is ambiguous, write a proposed Decision in NOTES.md and choose the simplest option consistent with the principles.

A minimal `CLAUDE.md` can simply say: *"Follow the 'For AI coding agents' section of README.md."*

---

## What this project does and does not claim

It demonstrates that the pipeline adapts forecasts, allocations, and mitigations correctly across multiple independently generated regimes, validated on held-out periods and against rule-based baselines. It does **not** predict Biokraft's actual sales. With real data, the same pipeline recalibrates.
