"""
Photo-first generation pipeline — the PRD's flow, built on the existing renderer.

    Source Photo
        │
        ├─▶ Triage        vision.analyze_quality()   FR-2, FR-3   (local, <2s)
        ├─▶ Detection     vision.detect_product()    FR-5         → Product Spec
        │
        │   ── from here, NOTHING reads the photo again (§4.2) ──
        │
        ├─▶ Genre         design.select_visual_genre()  FR-7
        ├─▶ Grade         design.derive_grade()         FR-8
        ├─▶ Skeleton      design.select_skeleton()      FR-9
        ├─▶ Copy          generate_copy()               FR-15
        │
        └─▶ EXISTING Jinja render → storage → preview   (unchanged)

The render half is deliberately untouched: same jinja_env, same templates,
same base_ctx keys, same upload path. Only the intelligence upstream is new.
"""
import asyncio
import datetime as _dt
import html as html_mod
import logging
import os
import re
from typing import Optional

from core.llm import chat_json, MODEL_CONTENT
from core.vision import intake, detect_product, analyze_quality, build_guidance
from core.design import derive_design
from core.packs import (select_pack, get_pack, pack_layout, score_packs,
                        NoPacksInstalled, packs_installed, PACKS)
from core.composition import plan_composition, make_sect
from core import i18n as _i18n
from core.design import select_hero_variant
from core import recents as _recents
from core.artdirector import (direct as ad_direct, shoot as ad_shoot,
                              critique as ad_critique, apply_repairs,
                              FEATURE_KINDS)
from core.offline import api_available, offline_spec, offline_copy
from core.sharecard import build_card, build_favicon
from core.imagery import build_image_set
from core.progress import report
from core.rendering import jinja_env
from core.storage import save as store_save, load as store_load, PUBLIC_URL
from core.jobs import mark_job_processing, mark_job_completed, mark_job_failed
from core.utils import build_image_map_logic

logger = logging.getLogger("photo_pipeline")

# §9.1 — regulated claim vocabulary, blocked AT GENERATION rather than
# filtered afterwards. Food and personal-care only.
REGULATED_TERMS = [
    "healthy", "healthiest", "organic", "authentic", "cures", "cure",
    "medicinal", "detox", "immunity", "chemical-free", "chemical free",
    "100% natural", "pure natural", "clinically", "doctor recommended",
    "fda", "fssai approved", "no side effects", "heals",
]
REGULATED_CATEGORIES = {"food"}


COPY_SYSTEM = """You are a senior copywriter for a boutique brand studio. You are given a PRODUCT SPEC derived from a photograph of one real product, and you write the words for that seller's website.

Return a JSON object with EXACTLY this shape:
{
  "site_info": {"display_name": "...", "site_title": "...", "tagline": "..."},
  "home": {"title": "...", "subtitle": "...", "cta": "...", "label": "...",
           "pillar1_title": "...", "pillar1_desc": "...",
           "pillar2_title": "...", "pillar2_desc": "..."},
  "about": {"heading": "...", "description": "...", "mission": "..."},
  "services": [{"title": "...", "description": "...", "icon": "star|zap|heart|globe|award|shield"}],
  "portfolio": [{"title": "...", "client": "...", "description": "...", "tag": "...", "outcome": "..."}],
  "testimonials": [{"text": "...", "name": "...", "role": "...", "rating": 4}],
  "faq": [{"question": "...", "answer": "..."}],
  "stats": [{"label": "...", "number": "..."}],
  "specifications": [{"label": "...", "value": "..."}],
  "seo": {"title": "...", "description": "...", "keywords": ["...", "..."]},
  "contact": {"title": "...", "description": "...", "email": "...", "phone": "...", "address": "...", "label": "..."},
  "footer": {"copyright": "...", "address": "..."}
}

HARD RULES
1. FACTS. The Product Spec and the seller's own supplied details are the ONLY
   source of truth. Never state a number, date, count, certification, origin or
   material that is not in them. If you have no quantifiable facts, return an
   empty "stats" list — never invent "500+ customers" or "12 years".
2. NO AI-ISMS. Banned: Unlock, Empower, Comprehensive, Seamless, Journey,
   Elevate, Discover the, Experience the, Welcome to, Our journey.
3. REGISTER. Write in the register named in VISUAL GENRE below. A tactile,
   material-led genre does not emit a specification table; a bright toy genre
   does not write like a law firm.
4. CONCRETE. Name the actual material, the actual technique, the actual object.
   "Hand-block printed on 60-count cotton" beats "premium quality fabric".
5. TESTIMONIALS are illustrative and must read as plausible for this product's
   real audience. Vary names, roles and what each one praises — each should
   name a DIFFERENT concrete detail (the finish, the fit, the delivery, how it
   held up). Include "rating" as an integer 1-5. Do not make every rating a 5:
   a page of nothing but five stars reads as fabricated. Use 4 sometimes, and
   a 3 with a mild, fair reservation is more convincing than another 5.
6. Every field listed above is rendered UI text. Never leave one blank or generic.
7. SPECIFICATIONS are what a buyer checks before paying: material, finish,
   dimensions, weight, what is included, care. Take them from the Product Spec
   and the seller's own words ONLY. Four to six rows. If you know three, give
   three - an invented measurement is the one lie a buyer can catch by holding
   the thing, and it is the one that gets refunded.

8. SEO. "title" is what shows in a search result and a browser tab: the product
   and the brand, under 60 characters, no tagline padding. "description" is the
   snippet under it, 140-155 characters, written to be read by a person rather
   than stuffed. "keywords" are 6-10 phrases someone would actually type into a
   search box to find THIS product - real search language, not adjectives from
   the copy. Include the plain generic term even when the brand has a fancier
   name for it.

9. LENGTH. A small seller's page should be read in under a minute, so keep it
   tight and cut anything that is not doing work:
   - site_title       at most 8 words
   - home.subtitle    ONE sentence, at most 20 words
   - home.pillar*_desc  at most 12 words each
   - services[].description  ONE short sentence, at most 14 words
   - about.description  2 sentences maximum
   - portfolio[].description  at most 14 words
   - faq answers      ONE sentence
   Produce at most 3 services, 2 portfolio items, 3 testimonials, 3 FAQs.
   Fewer, sharper lines beat more of them. Never pad to fill a field."""


def _strip_regulated(obj, active: bool):
    """
    §9.1: remove regulated claim vocabulary from generated copy for the
    categories that carry it. Applied structurally so it catches every string
    field rather than only the ones we remembered to check.
    """
    if not active:
        return obj
    if isinstance(obj, str):
        out = obj
        for term in REGULATED_TERMS:
            out = re.sub(rf"\b{re.escape(term)}\b\s*", "", out, flags=re.I)
        return re.sub(r"\s{2,}", " ", out).strip(" ,.;-") or obj
    if isinstance(obj, list):
        return [_strip_regulated(v, active) for v in obj]
    if isinstance(obj, dict):
        return {k: _strip_regulated(v, active) for k, v in obj.items()}
    return obj


async def generate_copy(spec: dict, genre: dict, layout: list,
                        brand_name: str = "", price: str = "",
                        seller_facts: str = "",
                        language: str = "en") -> Optional[dict]:
    """FR-15: all site copy, written from the Product Spec — never the photo."""
    geo = spec.get("geometry") or {}
    # The language instruction is stated once, plainly, and repeated at the end
    # of the prompt. Models drift back to English over a long JSON schema, and
    # a site whose headline is translated but whose FAQ is not looks broken in
    # a way a seller cannot fix.
    from core.i18n import normalise as _norm
    _lang = _norm(language)
    _lang_name = ("English" if _lang == "en" else
                  f"the language with IETF code '{_lang}'")
    _lang_note = ("" if _lang == "en" else
                  "Every string you return must be in that language - headline, "
                  "descriptions, FAQ answers, review text, button labels, all of "
                  "it. Do not leave any field in English. Write as a native "
                  "speaker selling this product would, not as a translation of "
                  "English marketing copy. Keep the brand name exactly as given.")
    prompt = f"""PRODUCT SPEC (derived from the seller's photograph):
- category:            {spec.get('category')}
- sub-type:            {spec.get('sub_type')}
- material:            {spec.get('material')}
- finish:              {spec.get('finish')}
- mood:                {spec.get('mood')}
- implied audience:    {spec.get('implied_audience')}
- implied price band:  {spec.get('implied_price_band')}
- geometry:            {geo.get('orientation')} / {geo.get('shape')}
- visible text on it:  {spec.get('visible_text') or '(none)'}

WRITE IN: {_lang_name}
{_lang_note}

SELLER SUPPLIED:
- brand name:  {brand_name or '(none given — invent one that suits the product)'}
- price:       {price or '(none given — do not mention price anywhere)'}
- extra facts: {seller_facts or '(none)'}

VISUAL GENRE: {genre['name']}
REGISTER: {genre['register']}
ORNAMENT LEVEL: {genre['ornament']}

SECTIONS TO WRITE (generate complete content for every one, in this order):
{', '.join(layout)}

Write the JSON object now."""

    if _lang != "en":
        prompt += ("\n\nREMINDER: every value in the JSON you return "
                   f"must be written in '{_lang}', not English.")

    data = await chat_json(
        system=COPY_SYSTEM, text=prompt,
        model=MODEL_CONTENT, temperature=0.85,
        # 3000 is comfortably above a full site's JSON (~3.2k chars
        # measured) and roughly halves latency versus 6000; the
        # truncation retry in core/llm.py covers the rare overflow.
        max_tokens=3000,
    )
    if not data:
        return None

    active = spec.get("category") in REGULATED_CATEGORIES
    data = _strip_regulated(data, active)

    # Tolerate the older field names in case the model reaches for them —
    # the templates read heading/description and text/name (see home.html:746,
    # :904), and this is exactly the mismatch that silently blanks the About
    # block and every testimonial on the legacy text path.
    ab = data.get("about") or {}
    data["about"] = {**ab,
                     "heading": ab.get("heading") or ab.get("title", ""),
                     "description": ab.get("description") or ab.get("story", "")}
    data["testimonials"] = [
        {**t, "text": t.get("text") or t.get("content", ""),
              "name": t.get("name") or t.get("author", "")}
        for t in (data.get("testimonials") or []) if isinstance(t, dict)
    ]
    return data



def _sections_in(slug: str) -> int:
    """
    How many sections a Pack's home page renders.

    Read from the template rather than guessed, because the Art Director is
    asked for one treatment per section and the inverted section is placed
    relative to the count. A guess of 8 against a Pack that renders 4 puts the
    inverted section on the footer.
    """
    try:
        src = jinja_env.loader.get_source(jinja_env, f"packs/{slug}/home.html")[0]
        return max(1, src.count("{{ sect("))
    except Exception:
        return 5



def _bundle_for_review(html: str, pack_slug: str, shots: dict) -> str:
    """
    Flatten a rendered page into one self-contained document.

    The critic looks at a screenshot, and a screenshot taken over file:// cannot
    resolve the /media/ URLs the page references — every product image would be
    a broken icon and the agent would report defects that do not exist. So the
    stylesheets and the seller's own images are inlined first.
    """
    from core.bundle import build_single_html
    from core.storage import load as store_load, PUBLIC_URL
    imgs = {}
    for url in shots.values():
        if not isinstance(url, str) or not url.startswith(PUBLIC_URL):
            continue
        if url not in html:
            continue
        try:
            imgs[url] = store_load(url[len(PUBLIC_URL):].lstrip("/"))
        except Exception:
            pass
    packs_root = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "templates", "packs")
    return build_single_html(html, pack_slug, packs_root, imgs, editable=False)


async def run_photo_generation_job(
    *, job_id: str, website_id: str, user_id: str, image_path: str,
    image_url: str, extra_paths: Optional[list] = None,
    brand_name: str = "", price: str = "",
    seller_facts: str = "", density: str = "generous", spin: int = 0,
    language: str = "en",
    override_triage: bool = False, spec_override: Optional[dict] = None,
    user_category: str = "", user_sub_type: str = "",
) -> None:
    """
    The full photo-first job. Mirrors run_generation_job's contract — marks the
    job processing, then completed or failed — so the existing polling endpoint
    and frontend work unchanged.
    """
    try:
        await mark_job_processing(job_id)
        await report(job_id, "triage")

        # ── Triage + Detection (FR-2, FR-3, FR-5) ──
        offline = not api_available()

        if spec_override:
            spec = spec_override
            quality = analyze_quality(image_path)
            verdict, guidance = "pass", ""
        elif offline:
            # No usable key. Measure everything measurable off the pixels and
            # take category/sub-type from the seller rather than inventing them.
            quality = analyze_quality(image_path)
            spec = offline_spec(image_path, quality, category=user_category,
                                sub_type=user_sub_type, seller_facts=seller_facts)
            verdict = "fail" if not quality.get("ok") else (
                "warn" if quality.get("defects") else "pass")
            guidance = build_guidance(quality.get("defects", []), spec.get("category"))
            logger.warning(f"[{job_id[:8]}] OFFLINE MODE — no model API available")
            if verdict == "fail" and not override_triage:
                await mark_job_failed(job_id, f"TRIAGE_FAIL: {guidance}")
                return
        else:
            await report(job_id, "detect")
            result = await intake(image_path)
            spec, quality = result["spec"], result["quality"]
            verdict, guidance = result["verdict"], result["guidance"]

            if verdict == "fail" and not override_triage:
                await mark_job_failed(
                    job_id, f"TRIAGE_FAIL: {guidance or 'Photo quality too low'}")
                return
            if not spec:
                # Detection unavailable rather than the photo being unusable:
                # build the Spec from the pixels and the seller's own answer.
                logger.warning(f"[{job_id[:8]}] detection unavailable — offline Spec")
                offline = True
                spec = offline_spec(image_path, quality, category=user_category,
                                    sub_type=user_sub_type, seller_facts=seller_facts)
                verdict = "warn" if quality.get("defects") else "pass"

        logger.info(f"[{job_id[:8]}] spec: {spec.get('category')}/{spec.get('sub_type')} "
                    f"conf={spec.get('confidence')} verdict={verdict}")

        # ── Design derivation (FR-7, FR-8, FR-9, FR-10) ──
        await report(job_id, "design")
        # The Pack is chosen first, because a dark design needs a dark Grade —
        # deriving a light palette and then darkening it produces mud.
        # ── Art Director, pass 1 of 2: choosing the page ──
        # The rules decide FIRST and the agent is shown what they chose. That
        # ordering is what makes the agent safe to add: no key, a refused call,
        # a timeout or a malformed reply all leave the rules' answer standing,
        # and the site ships exactly as it would have.
        #
        # It runs before derive_design because a dark design needs a dark Grade
        # — deriving a light palette and darkening it produces mud — and the
        # agent is allowed to change the design.
        _rule_pack = select_pack(spec, spin=spin) if packs_installed() else None
        _n_sections = _sections_in(_rule_pack) if _rule_pack else 5
        # The director runs before imagery, so it cannot know that staging
        # SUCCEEDED — only that it is possible. That is the honest thing to
        # pass: if staging then fails, the bleed composition finds no staged
        # photo and falls through to the next branch on its own.
        try:
            from core import bgremover as _bg
            _can_stage = _bg.available()
        except Exception:
            _can_stage = False
        direction = await ad_direct(
            spec, seller_facts, PACKS, _n_sections, list(FEATURE_KINDS),
            {"pack": _rule_pack,
             "hero": select_hero_variant(spec, quality, spin),
             "feature_kind": None, "rhythm": None, "invert_at": -1},
            staged=_can_stage)
        _slug_for_mode = direction.get("pack") or _rule_pack

        # ── Don't hand this seller the same design twice ──
        # Choosing the best design for a product is a judgement about that
        # product alone, and two honest judgements can agree: a frying pan and
        # a set of UI icons both read as technical gear, so both were built in
        # the release slip and the seller saw one website twice. Neither
        # decision was wrong, so no per-product fix can prevent it - variety
        # lives in the sequence, not the choice.
        #
        # The ranking below is the agent's pick first, then everything else in
        # score order, so declining a repeat still yields a design that suits
        # the product. Spin is the seller deliberately asking for a different
        # design of the SAME site, so the ledger stands aside for it.
        if _slug_for_mode and spin <= 0:
            _by_score = sorted(score_packs(spec).items(),
                               key=lambda kv: (-kv[1], kv[0]))
            _ranked = [_slug_for_mode] + [k for k, _ in _by_score
                                          if k != _slug_for_mode]
            _slug_for_mode, _why = _recents.steer(user_id, website_id, _ranked)
            if _why:
                logger.info(f"[{job_id[:8]}] design ledger: {_why}")

        _mode = (get_pack(_slug_for_mode).get("mode", "light")
                 if _slug_for_mode else "light")
        design = derive_design(spec, quality, spin=spin, density=density, mode=_mode)
        theme = design["theme"]
        if direction.get("hero"):
            theme = dict(theme)
            theme["_hero_variant"] = direction["hero"]
        logger.info(f"[{job_id[:8]}] art director: {direction.get('_agent')} "
                    f"pack={direction.get('pack')} hero={direction.get('hero')}")

        # ── Template Pack selection, from the Spec (never the photo) ──
        # The Pack decides the page's actual structure and type; the derived
        # Grade above is applied over it as an accent so the site still carries
        # the product's own colour without the Pack losing its identity.
        if not packs_installed():
            await mark_job_failed(
                job_id, "No website designs are installed yet. Add one under "
                        "templates/packs/ before generating.")
            return
        pack_slug = _slug_for_mode
        pack = get_pack(pack_slug)
        layout = pack_layout(pack_slug)
        scores = score_packs(spec)
        # Each Pack keeps its own signature accent, so a noir Site still reads
        # gold and a pulse Site still reads hot-red, while the ground and text
        # stay derived from the photo.
        if pack.get("accent"):
            theme = dict(theme)
            theme["accent"] = pack["accent"]
        logger.info(f"[{job_id[:8]}] pack={pack_slug} scores={scores} "
                    f"genre={design['genre']['name']} anchor={theme.get('_anchor')}")

        # ── Share Cards + favicon (FR-29) ──
        # Built before render so the meta tags can point at them. Best-effort:
        # a Site without a Share Card is worse, but a Site that failed to build
        # because of one would be far worse.
        share_card_url = story_card_url = favicon_url = favicon_apple_url = None
        # The uploaded file is still on local disk at image_path — fetching the
        # same bytes back out of object storage was a pointless round trip.
        # Every photo the seller gave us. The first is the one detection ran
        # on; the rest exist to fill the pages that were previously text-only.
        all_photos = []
        for _p in [image_path] + list(extra_paths or []):
            try:
                with open(_p, "rb") as _fh:
                    all_photos.append(_fh.read())
            except Exception:
                pass
        photo_bytes = all_photos[0] if all_photos else b""

        _brand = (brand_name or "").strip() or ""
        _head = ""

        shots = {}

        async def _make_assets():
            await report(job_id, "imagery")
            """Cards and icons need only the photo and the Grade — both ready
            here — so they overlap the copy call instead of following it."""
            nonlocal share_card_url, story_card_url, favicon_url, favicon_apple_url
            if not photo_bytes:
                return
            # Several distinct crops of the ONE photo, so the page can show the
            # product three or four times without repeating a frame.
            try:
                shots.update(await build_image_set(
                    all_photos, theme, website_id,
                    store_save, asyncio.to_thread,
                    spec=spec, quality=quality))
            except Exception as e:
                logger.warning(f"[{job_id[:8]}] image variants skipped: {e}")
            for kind, target in (("og", "share-card.png"), ("story", "share-story.png")):
                try:
                    blob = await asyncio.to_thread(
                        build_card, photo_bytes, theme, kind, _brand, _head, price)
                    if blob:
                        url = await asyncio.to_thread(
                            store_save, blob, "image/png",
                            f"websites/{website_id}/assets", target)
                        if kind == "og":
                            share_card_url = url
                        else:
                            story_card_url = url
                except Exception as e:
                    logger.warning(f"[{job_id[:8]}] {kind} card skipped: {e}")
            try:
                icons = await asyncio.to_thread(build_favicon, photo_bytes)
                for px, blob in icons.items():
                    u = await asyncio.to_thread(
                        store_save, blob, "image/png",
                        f"websites/{website_id}/assets", f"icon-{px}.png")
                    if px == 32:
                        favicon_url = u
                    else:
                        favicon_apple_url = u
            except Exception as e:
                logger.warning(f"[{job_id[:8]}] favicon skipped: {e}")

        # ── Copy (FR-15) ──
        await report(job_id, "copy")
        if offline:
            data = offline_copy(spec, design["genre"], layout,
                                brand_name, price, seller_facts, language)
            await _make_assets()
        else:
            # The copy call dominates the wall clock; the Share Cards cost
            # under a second of CPU. Running them together hides that entirely.
            data, _ = await asyncio.gather(
                generate_copy(spec, design["genre"], layout,
                              brand_name, price, seller_facts, language),
                _make_assets(),
            )
        await report(job_id, "copy_done")
        if not data:
            await mark_job_failed(job_id, "Copy generation returned nothing")
            return

        # ── Render (existing machinery, untouched) ──
        await report(job_id, "render")
        site_name = (html_mod.escape(" ".join(brand_name.split()))
                     if brand_name.strip()
                     else data.get("site_info", {}).get("display_name", "My Brand"))
        image_map = build_image_map_logic([image_url] if image_url else [], layout)

        from core.commerce import parse_price as _pp
        _minor, _currency = _pp(price)
        _price_amount = f"{_minor // 100}.{_minor % 100:02d}" if _minor else ""

        base_ctx = dict(
            site_name=site_name,
            site_title=data.get("site_info", {}).get("site_title", site_name),
            page_title=site_name,
            tagline=data.get("site_info", {}).get("tagline", ""),
            theme=theme, footer=data.get("footer", {}),
            layout=layout, image_map=image_map,
            image_count=1 if image_url else 0, has_images=bool(image_url),
            logo=None, favicon_url=favicon_url, favicon_apple_url=favicon_apple_url,
            favicon_sized=bool(favicon_url),
            share_card_url=share_card_url, story_card_url=story_card_url,
            site_url="",
            services_img=image_map.get("services"),
            testimonials_img=image_map.get("testimonials"),
            overflow_imgs=image_map.get("overflow", []),
            images=[image_url] if image_url else [],
            shots={k: v for k, v in shots.items() if not k.startswith('_')},
            # A composed plate (product cut out and centred on the Site's own
            # ground) can be letterboxed seamlessly; a real photo cannot.
            is_cutout=bool(shots.get("_is_cutout")),
            # A staged photograph is a real scene at generated resolution, so
            # it can carry a full-bleed hero. A cut-out never could: it holds
            # only the few hundred pixels the seller's product occupied.
            is_staged=bool(shots.get("_staged")),
            # How wide a product image may be drawn: 1.25x its real pixels.
            # Computed here rather than in the stylesheet because the shell
            # rebinds `shots` before including it, and the nested lookups
            # silently produced nothing — the rule rendered as an empty block
            # with only its comment left behind.
            shot_cap=int(min([w for w in (shots.get("square_w"),
                                          shots.get("hero_w")) if w] or [0]) * 1.25),
            price=price,
            asset_base=pack["asset_base"],
            pack=pack,
            # Step 3's own fields. seo_title and meta_description are what a
            # search result actually shows; the site title is a brand line and
            # usually the wrong thing to put there.
            seo=data.get("seo") or {},
            # Structured data needs a NUMBER and a currency, not "Rs 1,450".
            # core/commerce.py already parses seller price text properly, so
            # the schema markup and the checkout cannot disagree about cost.
            price_amount=_price_amount, currency_code=_currency,
            specifications=[sp for sp in (data.get("specifications") or [])
                            if isinstance(sp, dict) and sp.get("label")],
            # Language reaches the templates as `t`, `dir` and a script-aware
            # font pair. Without the last two a Hindi site renders as boxes and
            # an Arabic one runs the wrong way — neither is a translation
            # problem, both are a broken page.
            **_i18n.context(language),
        )

        stats = [{"label": s.get("label", ""), "number": s.get("number", "")}
                 for s in (data.get("stats") or []) if isinstance(s, dict)]

        # ── Composition ──
        # Which section gets which treatment, which one inverts, and what the
        # page's single oversized moment is. Without this every section renders
        # at the same width with the same padding, which is what made the
        # output read as a template with the photos dropped in.
        # How many sections this Pack actually renders. Guessing it puts the
        # inverted section in the wrong place — with a guess of 8 against a
        # Pack that renders 4, "never the last one" stops holding and the
        # invert lands on the footer-adjacent section.
        try:
            _src = jinja_env.loader.get_source(
                jinja_env, f"packs/{pack_slug}/home.html")[0]
            n_sections = _src.count("{{ sect(")
        except Exception:
            n_sections = 5
        comp = plan_composition(data, spec, price=price, spin=spin,
                                n_sections=n_sections,
                                rhythm=direction.get("rhythm"),
                                invert_at=direction.get("invert_at", -1),
                                prefer_feature=direction.get("feature_kind") or "")
        base_ctx["comp"] = comp
        base_ctx["sect"] = make_sect(comp)
        logger.info(f"[{job_id[:8]}] composition({n_sections}): {'/'.join(comp['seq'])} "
                    f"invert@{comp['invert_at']} "
                    f"feature={comp['feature'].get('kind') or 'none'}")

        def _render_home():
            return jinja_env.get_template(f"packs/{pack_slug}/home.html").render(
                **base_ctx, current_page="home.html",
                home=data.get("home", {}), about=data.get("about", {}),
                services=data.get("services", []), portfolio=data.get("portfolio", []),
                testimonials=data.get("testimonials", []), faq=data.get("faq", []),
                pricing=data.get("pricing", []), stats=stats,
                contact=data.get("contact", {}),
            )

        home_html = _render_home()

        # ── Art Director, pass 2 of 2: looking at what was built ──
        # This is the part no rule can do. The page is flattened into a
        # self-contained document, screenshotted with headless Chrome, and the
        # agent is asked what is visibly wrong with it. Anything it can fix is
        # applied from a fixed vocabulary — it never writes markup — and the
        # page is rebuilt ONCE. One repair pass, not a loop: a second look at a
        # changed page invites the agent to keep finding new things to change.
        review = {"verdict": "ship", "defects": [], "_agent": "skipped"}
        try:
            shot = await asyncio.to_thread(
                ad_shoot, _bundle_for_review(home_html, pack_slug, shots))
            review = await ad_critique(shot, {
                "pack": pack_slug, "hero": theme.get("_hero_variant"),
                "feature_kind": (comp.get("feature") or {}).get("kind"),
                "invert_at": comp.get("invert_at")})
            if review.get("actionable"):
                comp2, hero2, contain, applied = apply_repairs(
                    review["actionable"], comp, theme.get("_hero_variant"),
                    bool(base_ctx.get("is_cutout")))
                if applied:
                    if hero2 != theme.get("_hero_variant"):
                        theme = dict(theme)
                        theme["_hero_variant"] = hero2
                        base_ctx["theme"] = theme
                    base_ctx["is_cutout"] = contain
                    # A forced feature kind is honoured only if the content
                    # actually supports it, which re-planning re-checks.
                    comp2 = plan_composition(
                        data, spec, price=price, spin=spin, n_sections=n_sections,
                        rhythm=comp2.get("seq"),
                        invert_at=(comp2.get("invert_at")
                                   if comp2.get("invert_at") is not None else None),
                        prefer_feature=comp2.get("_force_feature", ""))
                    base_ctx["comp"] = comp2
                    base_ctx["sect"] = make_sect(comp2)
                    comp = comp2
                    home_html = _render_home()
                    logger.info(f"[{job_id[:8]}] repaired: {'; '.join(applied)}")
        except Exception as e:
            # A failed review must never cost the seller their site.
            logger.warning(f"[{job_id[:8]}] art direction review skipped: {e}")

        await asyncio.to_thread(
            store_save, home_html.encode("utf-8"), "text/html",
            f"websites/{website_id}", "home.html")

        # ── Sub-pages ──
        # Rendered from templates/packs/_pages/, which extend the SELECTED
        # Pack's own shell — so About and Contact carry that Pack's palette,
        # type and chrome without any of it being duplicated per page. Only
        # pages whose section is in the Pack's layout are written, so the nav
        # never links somewhere that does not exist.
        page_map = {
            "about.html": "about",
            "portfolio.html": "portfolio",
            "services.html": "services",
            "contact.html": "contact",
        }
        async def _write_page(out_name, section):
            if section not in layout:
                return None
            try:
                # The shell marks the current nav item from this. Without it every
                # sub-page rendered with Home highlighted.
                page_html = jinja_env.get_template(f"packs/_pages/{out_name}").render(
                    **base_ctx, current_page=out_name,
                    home=data.get("home", {}), about=data.get("about", {}),
                    services=data.get("services", []), portfolio=data.get("portfolio", []),
                    testimonials=data.get("testimonials", []), faq=data.get("faq", []),
                    pricing=data.get("pricing", []), stats=stats,
                    contact=data.get("contact", {}),
                )
                await asyncio.to_thread(
                    store_save, page_html.encode("utf-8"), "text/html",
                    f"websites/{website_id}", out_name)
                return out_name
            except Exception as page_err:
                # One bad sub-page must never lose the site.
                logger.warning(f"[{job_id[:8]}] {out_name} skipped: {page_err}")
                return None

        # Independent of one another — write them concurrently.
        results = await asyncio.gather(
            *[_write_page(n, sec) for n, sec in page_map.items()])
        written = ["home.html"] + [r for r in results if r]
        logger.info(f"[{job_id[:8]}] pages written: {', '.join(written)}")

        # ── Keep what it took to build this ──
        # Everything needed to re-render this site in a DIFFERENT design, with
        # no model calls: the copy, the Grade, the layout and the image URLs.
        # Without it, letting a seller change their design would mean paying to
        # write the same words again — and they would come back different.
        try:
            import json as _json
            await asyncio.to_thread(
                store_save,
                _json.dumps({
                    "content": data,
                    "spec": spec,
                    "theme": dict(theme),
                    "layout": layout,
                    "shots": shots,
                    "price": price,
                    "site_name": site_name,
                    "brand_name": brand_name,
                    "spin": spin,
                    "density": density,
                    "pack": pack_slug,
                    "language": language,
                    "created_at": _dt.datetime.now(
                        _dt.timezone.utc).isoformat(),
                    # Provenance. These were written to a separate document
                    # store that nothing ever read back; they are kept because
                    # a page that comes out wrong should be traceable to the
                    # decision that caused it rather than guessed at, and this
                    # file is already being written.
                    "user_id": str(user_id),
                    "genre": design["genre"]["name"],
                    "skeleton": design["skeleton"]["name"],
                    "triage": {"verdict": verdict, "guidance": guidance,
                               "quality": quality},
                    "background": shots.get("_background"),
                    "treatment": shots.get("_treatment"),
                }, ensure_ascii=False).encode("utf-8"),
                "application/json", f"websites/{website_id}", "content.json")
        except Exception as e:
            logger.warning(f"[{job_id[:8]}] content not saved, "
                           f"redesign will be unavailable: {e}")

        # ── Catalogue ──
        # The orderable version of what was just generated. Written separately
        # from the website document and to disk, because an order is a real
        # obligation between two people and must outlive a restart — and
        # because the price a buyer is charged has to come from here, never
        # from whatever the browser posts.
        try:
            from core.commerce import build_catalogue, save_catalogue, save_settings
            # Durable, unlike the in-memory website document: publishing builds
            # the public URL slug from this, and a restart must not turn a
            # seller's address into "shop-2".
            save_settings(website_id, {"site_name": site_name})
            cat = build_catalogue(data, spec, price,
                                  {k: v for k, v in shots.items()
                                   if not k.startswith("_")})
            save_catalogue(website_id, cat)
            if not cat.get("orderable"):
                logger.info(f"[{job_id[:8]}] no usable price ({price!r}) — "
                            "the site is enquiry-only, not orderable")
        except Exception as e:
            logger.warning(f"[{job_id[:8]}] catalogue skipped: {e}")

        await report(job_id, "storing")
        await mark_job_completed(job_id, website_id,
                                 f"/preview/{website_id}/home.html")
        logger.info(f"[{job_id[:8]}] completed")

    except Exception as e:
        logger.error(f"[{job_id[:8]}] failed: {e}", exc_info=True)
        try:
            await mark_job_failed(job_id, f"{type(e).__name__}: {e}")
        except Exception:
            pass
    finally:
        for _p in [image_path] + list(extra_paths or []):
            try:
                os.remove(_p)
            except Exception:
                pass
