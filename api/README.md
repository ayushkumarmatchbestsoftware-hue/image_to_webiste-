# The web layer

Routes, the three pages a person sees, and the bootstrap.

## Run

    # .env needs two lines: LLM_PROVIDER and LLM_API_KEY
    python -m uvicorn api.server:app --reload --port 8000

## What is here

| | |
|---|---|
| `__init__.py` | reads .env, then configures logging. Import order is load-bearing. |
| `server.py` | assembly only: middleware, static mounts, router registration |
| `deps.py` | the merchant-key guard and the order rate limit |
| `routes/` | one router per area: system, generate, sites, designs, shop, publish |
| `ui/` | upload, orders, designs |

## What it talks to

Nothing external. Storage is files under `STORE_DIR`, job state is a dict in
this process, and the model is whichever provider `LLM_PROVIDER` names.
