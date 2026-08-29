---
name: product-staging
description: Place a seller's product into a photographed setting that belongs to its own world, instead of cutting it out onto a flat colour. Runs during generation; the setting is derived from the Product Spec, never asked for.
version: 1.0.0
status: active
tags: [staging, scene, bg-removal, image-editing]
category: imagery
---

# Product staging

## Why this exists

A cut-out on a flat colour says "this product was photographed against something
we removed". A product on a bench at a cricket ground says "this is what owning
it looks like". The second sells; the first only tidies.

It also solves a problem no amount of processing can: a seller's photo contains
a product a few hundred pixels wide, and no cut-out from it can fill a
full-bleed hero without going soft. A staged photograph is generated at its own
resolution, so the hero becomes possible at all.

## What the setting should be

Derived from the Product Spec, and from nothing else. Never asked for, never
chosen from a list of nice-looking places.

Read `sub_type`, `category`, `material`, `finish` and `mood`, and name the place
this object actually belongs:

| The Spec says | The setting is |
|---|---|
| a cricket bat, willow | a club ground — bench, pads, pavilion out of focus, evening light |
| a gold ring, 18ct | a jeweller's bench or a dark velvet pad under a single lamp |
| a charcoal chicken box | a service counter at dusk, warm light, the shop behind |
| a letterpress card set | a studio bench, ink, a press out of focus |
| a linen cushion | a made bed or a window seat in daylight |
| a wooden stacking toy | a playroom floor, soft daylight |

The test is whether a buyer would recognise the place as where the thing lives.
A marble worktop under studio lighting flatters everything and belongs to
nothing, which is why it reads as stock photography.

## Writing the description

Ask the text model for it — the Spec is already in hand, and a described scene
built from the product's own world beats any fixed list. Give it the Spec and
ask for one sentence naming:

- **the surface** the product rests on
- **what is behind it**, out of focus
- **the light** — time of day, direction, hardness

Keep it to one sentence. A long description makes the image model invent
clutter that competes with the product.

## What must not change

The product. Same shape, same colour, same proportions, same markings, same
wear. A buyer receives the real thing; if staging restyles it, the photograph
becomes a lie the seller did not tell.

Say this in the prompt explicitly, and check it afterwards. A staged photo that
no longer matches the original is discarded — not shipped and hoped over.

## The prompt

```
Place this exact product into <the setting>.
Keep the product itself completely unchanged - same shape, colour, proportions,
markings and wear. Do not redraw, restyle or beautify it.
Photograph it as a real product photograph: <lighting>, shallow depth of field,
the background softly out of focus.
The product must remain the clear subject and must not be cropped.
```

## Checking the result

Stricter than for a plain background swap, because far more of the frame has
changed:

- **the product still matches** — colour and proportion, measured against the
  original, not eyeballed
- **the product is still the subject** — it has not shrunk into a landscape
- **nothing was cropped away**

If any check fails, fall back. In order: a flat plate on the site's own ground,
then the local cut-out, then the untouched photo. Each of those is worse
looking and none of them is wrong, which is the right way round.

## Cost

One image call per site. It is the single most expensive thing in the pipeline
and the single biggest difference in the result, so it is worth being explicit
that it is optional: with no image quota the site still generates, using the
cut-out path, and nothing fails.
