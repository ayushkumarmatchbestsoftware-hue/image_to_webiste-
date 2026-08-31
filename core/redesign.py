"""
Re-render an existing site in a different design.

A seller who dislikes the design they were given should not have to start
again — and re-generating would not just cost another set of model calls, it
would come back with different words. They asked to change the look, not the
copy.

So generation writes a content.json beside the pages holding everything it
took to build them: the copy, the Grade, the layout, the image URLs. This
reads that back, swaps the Pack, and re-renders. No model calls, nothing
rewritten, and the same product on the page.

The Grade is re-derived rather than reused, because a light design and a dark
one need different palettes from the same photo — carrying a dark Grade onto a
light Pack produces mud.
"""
import json
import logging

from core import i18n as _i18n

logger = logging.getLogger("redesign")


class RedesignError(Exception):
    """The site cannot be re-rendered — usually because it predates content.json."""


def load_content(website_id: str) -> dict:
    from core.storage import load as store_load
    try:
        raw = store_load(f"websites/{website_id}/content.json")
    except Exception as e:
        raise RedesignError(
            "this site was generated before designs could be changed; "
            "generate it again to switch design") from e
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise RedesignError(f"stored content is unreadable: {e}") from e


def render(website_id: str, pack_slug: str) -> dict:
    """
    Re-render every page of a site in `pack_slug`. Returns {filename: html}.
    """
    from core.packs import PACKS, get_pack, pack_layout
    from core.design import derive_design
    from core.composition import plan_composition, make_sect
    from core.rendering import jinja_env

    if pack_slug not in PACKS:
        raise RedesignError(f"unknown design {pack_slug!r}")

    doc = load_content(website_id)
    data = doc.get("content") or {}
    spec = doc.get("spec") or {}
    shots = doc.get("shots") or {}
    price = doc.get("price") or ""
    spin = int(doc.get("spin") or 0)
    site_name = doc.get("site_name") or "My Brand"

    pack = get_pack(pack_slug)
    layout = pack_layout(pack_slug)

    # Re-derive rather than reuse: a dark Pack needs a dark Grade, and carrying
    # the previous one across turns a light design to mud.
    design = derive_design(spec, doc.get("quality") or {}, spin=spin,
                           density=doc.get("density") or "generous",
                           mode=pack.get("mode", "light"))
    theme = dict(design["theme"])
    if pack.get("accent"):
        theme["accent"] = pack["accent"]
    if (doc.get("theme") or {}).get("_hero_variant"):
        theme["_hero_variant"] = doc["theme"]["_hero_variant"]

    try:
        src = jinja_env.loader.get_source(jinja_env, f"packs/{pack_slug}/home.html")[0]
        n_sections = max(1, src.count("{{ sect("))
    except Exception:
        n_sections = 5
    comp = plan_composition(data, spec, price=price, spin=spin,
                            n_sections=n_sections)

    clean_shots = {k: v for k, v in shots.items() if not str(k).startswith("_")}
    widths = [w for w in (clean_shots.get("square_w"), clean_shots.get("hero_w")) if w]

    base = dict(
        site_name=site_name,
        site_title=(data.get("site_info", {}) or {}).get("site_title", site_name),
        page_title=site_name,
        tagline=(data.get("site_info", {}) or {}).get("tagline", ""),
        theme=theme, footer=data.get("footer", {}), layout=layout,
        image_map={}, image_count=1 if clean_shots else 0,
        has_images=bool(clean_shots), logo=None,
        favicon_url=None, favicon_apple_url=None, favicon_sized=False,
        share_card_url=None, story_card_url=None, site_url="",
        services_img=None, testimonials_img=None, overflow_imgs=[],
        images=[clean_shots.get("hero")] if clean_shots.get("hero") else [],
        shots=clean_shots, is_cutout=bool(shots.get("_is_cutout")),
        shot_cap=int(min(widths) * 1.25) if widths else 0,
        price=price, asset_base=pack["asset_base"], pack=pack,
        **_i18n.context(doc.get('language') or 'en'),
        comp=comp, sect=make_sect(comp),
    )
    body = dict(
        home=data.get("home", {}), about=data.get("about", {}),
        services=data.get("services", []), portfolio=data.get("portfolio", []),
        testimonials=data.get("testimonials", []), faq=data.get("faq", []),
        pricing=data.get("pricing", []),
        stats=[{"label": s.get("label", ""), "number": s.get("number", "")}
               for s in (data.get("stats") or []) if isinstance(s, dict)],
        contact=data.get("contact", {}),
    )

    pages = {"home.html": jinja_env.get_template(
        f"packs/{pack_slug}/home.html").render(
            **base, current_page="home.html", **body)}

    for name, section in (("about.html", "about"), ("portfolio.html", "portfolio"),
                          ("services.html", "services"), ("contact.html", "contact")):
        if section not in layout:
            continue
        try:
            pages[name] = jinja_env.get_template(f"packs/_pages/{name}").render(
                **base, current_page=name, **body)
        except Exception as e:
            # One bad sub-page must not cost the seller the redesign.
            logger.warning(f"{name} skipped during redesign: {e}")

    logger.info(f"redesigned {website_id[:8]} as {pack_slug} "
                f"({len(pages)} pages, no model calls)")
    return pages


def save(website_id: str, pages: dict, pack_slug: str) -> None:
    """Write the re-rendered pages over the stored site and record the design."""
    from core.storage import save as store_save, load as store_load
    for name, html in pages.items():
        store_save(html.encode("utf-8"), "text/html",
                           f"websites/{website_id}", name)
    try:
        doc = json.loads(store_load(
            f"websites/{website_id}/content.json").decode("utf-8"))
        doc["pack"] = pack_slug
        store_save(json.dumps(doc, ensure_ascii=False).encode("utf-8"),
                           "application/json", f"websites/{website_id}",
                           "content.json")
    except Exception as e:
        logger.warning(f"design not recorded for {website_id[:8]}: {e}")
