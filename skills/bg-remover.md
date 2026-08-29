---
name: bg-remover
description: Replace the background of a seller's product photo so it belongs on the website being generated for them. Runs automatically during generation — the new background is decided by the site's own palette and mood, never asked for.
version: 2.0.0
status: active
tags: [bg-removal, background-removal, image-editing]
category: bg-removal
---

# Background replacement

## How this differs from the chat version

The original skill asked the user what background they wanted. Here nobody is
asked anything. A seller uploads one photo, and by the time they see it the
site already has a palette, a mood and a design — so the background is decided
**from the site**, not from a request. Getting that right is the whole job:
a product floating on a colour that does not appear anywhere else on the page
looks worse than the original snapshot did.

## When it runs

During generation, after the Grade is derived and before the imagery is cut to
its slots. It is attempted only when `analyze_background()` has already
measured the photo and decided the background is worth removing — a product
already shot on clean white is left alone.

## What the new background should be

Exactly one of these, in this order of preference:

| Condition | New background |
|---|---|
| The Grade has a ground colour (always, in practice) | That exact colour, flat and even — it is the page's own surface, so the product sits ON the page rather than on a cut-out island |
| The product is a food or lifestyle item AND the pack's mood is warm | The same ground with a soft, even surface shadow beneath the product |
| Anything unclear | Plain white |

Never invent a scene. A beach, a studio, a marble worktop — these look
impressive in isolation and wrong on the page, because the page has its own
colour and the two will not agree. The site's ground colour is the answer
almost every time.

## The prompt

Three things must be in it, and the third is the one that matters most here:

1. **Keep the subject unchanged.** Same pose, framing, proportions, and every
   product detail. A buyer receives the real thing; if the model restyles the
   product, the photo becomes a lie.
2. **State the new background exactly** — the hex colour from the Grade.
3. **Ask for a clean edge with no halo or colour fringing**, and say
   explicitly that thin structures must survive: chair legs, handles, chains,
   spokes, straps. These are what segmentation gets wrong, and they are the
   reason this path exists.

Shape:

```
Replace the background of this product photograph with a flat, even <HEX> surface.
Keep the product exactly as it is — same pose, framing, proportions, colour and
every detail. Do not restyle, redraw or beautify the product itself.
Cut cleanly around the product with no halo, glow or colour fringing.
Preserve every thin structure exactly — legs, bases, handles, chains, spokes,
straps — do not thicken, smooth or drop them.
Remove any shadow, reflection or floor from the original photograph.
```

## Verifying the result

The model returns a photograph, not a cut-out — there is no alpha channel.
Before accepting it, check:

- **The product still matches the original.** Compare mean colour and aspect;
  a large shift means the model restyled it, and the result must be discarded.
- **The background is actually the colour asked for.** Sample the corners.
- **The subject did not shrink or move.**

If any check fails, discard and fall back. A wrong photo of the product is
worse than a plainly cut-out one, because the seller may not notice and the
buyer receives something different.

## Falling back

This path needs an image-editing model, and it will not always be there — an
unfunded key returns HTTP 429 for image models while text models keep working,
which is easy to misread as the feature being broken.

When it is unavailable, or when a check above fails, fall back to the local
`rembg` cut-out in `core/imagedirector.py`. That path needs no key and no
network. It is weaker on thin structures — a chair base, a wire handle — which
is exactly the gap this skill closes when it can run.

Never block a generation on this. A site with an untouched photo ships; a site
that failed to generate does not.
