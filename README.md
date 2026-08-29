# Image to Website

Backend for turning a single product photograph into a working online shop.

A seller uploads one photo. The service reads the product from it, derives a
palette and type from the photo itself, writes the copy, picks a design,
renders a multi-page site, and gives the seller a public link and an order
desk. Cash on delivery and UPI work with no merchant account.

---

## How a generation runs

```
photo
  └─▶ triage          quality check — resolution, exposure, focus, clutter
  └─▶ detection       vision model → Product Spec (the only stage that reads the photo)
  └─▶ art director    chooses design, hero, feature and pacing          ← agent
  └─▶ grade           palette and type derived from the photo, WCAG-repaired
  └─▶ imagery         background removed if measurement says so, then composed
                      to the exact shape of each slot it will occupy
  └─▶ copy            model writes the page from the Spec, never from the photo
  └─▶ composition     section rhythm, one inverted section, one oversized moment
  └─▶ render          Jinja → HTML
  └─▶ critique        screenshots the result and names what is wrong     ← agent
  └─▶ repair          applies fixes from a fixed vocabulary, re-renders once
  └─▶ catalogue       the priced, orderable version of what was produced
```

The model never writes HTML. It returns JSON; templates render it. That is what
keeps the output structurally sound whatever the model does.

## Layout

| Path | What it holds |
|---|---|
| `core/` | the pipeline — one concern per module |
| `core/artdirector.py` | the only agent: chooses the design, then critiques the rendered page |
| `core/commerce.py` | catalogue, server-side pricing, orders, state machine |
| `core/payments.py` | cash on delivery and UPI |
| `core/publish.py` | hosting — a readable public URL per site |
| `templates/packs/` | the designs; each is a `_shell.html` plus a `home.html` |
| `skills/` | design criteria as editable Markdown, read by the agent at runtime |
| `local_test/` | a self-contained harness: the API, an upload UI, and the order desk |

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env          # then add a model API key
python -m uvicorn local_test.server:app --reload --port 8000
```

| | |
|---|---|
| `/` | upload a photo and generate |
| `/templates` | browse the designs, rendered live |
| `/preview/{id}/home.html` | the generated site |
| `/shop/{id}` | the seller's order desk |
| `/s/{slug}/` | a published storefront |
| `/health` | provider, agent and browser status |

`local_test/` replaces MongoDB, Redis, R2 and Vercel with local equivalents, so
the pipeline runs end to end on one machine with no external service. Orders are
the exception: they are written to disk and survive a restart, because an order
is an obligation between two people.

## Things that are load-bearing

**Money is an integer of minor units.** Paise, cents. Never a float.

**The server prices every order.** The browser sends product ids and quantities;
nothing it says about money is read. A client that could set its own price could
buy for one paisa.

**A UPI payment is a claim until the seller confirms it.** A UPI intent link
reports nothing back, so an order moves to `pending` when the buyer supplies a
reference and to `paid` only when a human has seen the money.

**The agent is never load-bearing.** No key, no browser, a refused call or a
malformed reply — each falls back to the deterministic rules and the site still
ships. The agent improves a page; it cannot block one.

## Configuration

Everything has a working default except the model key. See `.env.example`.

| Variable | Effect |
|---|---|
| `GEMINI_API_KEY` / `OPENAI_API_KEY` | model access; without one the offline path is used |
| `PUBLIC_BASE_URL` | makes asset and API URLs absolute — required for real hosting |
| `SHOP_REQUIRE_KEY` | **set to `1` before taking real orders.** Off by default so local testing is frictionless |
| `ART_DIRECTOR` | `0` disables the agent |

## Status

Generation, imagery, publishing and the commerce layer are working. GST,
shipping, refunds and a hosted payment gateway are not built. The merchant API
is unauthenticated unless `SHOP_REQUIRE_KEY=1`.
