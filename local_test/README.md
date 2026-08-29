# Local Test Harness — photo-first pipeline

Runs the PRD's image-to-website flow with every external connection replaced
by a local stand-in. Provider is OpenAI.

## Run

    export OPENAI_API_KEY=sk-...        # or put it in .env
    python local_test/server.py         # -> http://127.0.0.1:5000

## Flow

    photo ──▶ POST /triage                FR-2/3/5  verdict + coaching + Product Spec
          ──▶ POST /generate-from-photo   FR-7/8/9/15  design + copy + render
          ──▶ GET  /job-status/{id}       poll
          ──▶ GET  /preview/{id}/home.html

## Endpoints

| Route | Purpose |
|---|---|
| `POST /triage` | quality metrics (local PIL) + Product Spec (vision call) |
| `POST /generate-from-photo` | full photo-first generation, supports `spin` |
| `GET /job-status/{job_id}` | queued / processing / completed / failed |
| `GET /preview/{id}/{page}` | serves generated HTML |
| `GET /media/{key}` | serves stored images |
| `GET /download/{id}` | zip of the site |
| `POST /generate` | legacy text-first path (still works) |

## Local stand-ins

| Real | Replaced with |
|---|---|
| Cloudflare R2 | `./local_store/` |
| Redis · MongoDB | in-process dicts |
| PostgreSQL · credits · JWT | removed |
| Vercel | stub |

## Models

`OPENAI_MODEL_FAST` (default `gpt-4.1-mini`) — triage/detection
`OPENAI_MODEL_CONTENT` (default `gpt-4.1`) — site copy
