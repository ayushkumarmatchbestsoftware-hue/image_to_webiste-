"""
Demo content for the template gallery.

A Pack can only be judged with content of the kind it was built for. Showing
noir a hot-sauce menu and counter a gold ring would flatter neither and tell the
seller nothing, so each design gets a seller from its own use case.

Nothing here touches generation — this exists purely so the designs can be
looked at before a photo is ever uploaded.
"""
import base64
import io

# One brief per Pack, matched to the use case in its registry entry.
BRIEFS = {
    "noir": dict(
        brand="Aurum & Vine", sub_type="hand-set gold ring", material="18ct gold",
        mood="quiet, expensive", price="Rs 42,000",
        obj=(198, 164, 96), ground=(24, 22, 20),
        label="The piece", cta="Enquire", city="Jaipur",
        blurb="A single band, set by hand and finished over three days.",
        services=[("The setting", "Each stone seated by hand."),
                  ("The finish", "Three days of hand polishing."),
                  ("Resizing", "Free for the first year.")],
        stats=[("120", "Pieces a year"), ("1998", "Since")],
        review=("Priya M.", "Wore it daily for two years and it still catches light the same.")),
    "pulse": dict(
        brand="Kestrel Supply", sub_type="technical shell jacket", material="ripstop",
        mood="utilitarian, limited", price="Rs 3,900",
        obj=(47, 58, 68), ground=(232, 232, 228),
        label="Drop 04", cta="Reserve one", city="Bengaluru",
        blurb="Ripstop shells cut in small numbers and finished by hand.",
        services=[("Shell jacket", "Three-layer, taped."),
                  ("Repairs", "Re-tape and re-proof."),
                  ("Fitting", "Sized in person.")],
        stats=[("40", "Made"), ("2019", "Since")],
        review=("Ravi K.", "Wore it through a full monsoon and it never wet through.")),
    "counter": dict(
        brand="Grill & Go", sub_type="charcoal chicken box", material="charcoal-grilled",
        mood="fast, hot, cheap", price="Rs 180",
        obj=(198, 116, 52), ground=(250, 246, 238),
        label="Today", cta="Order takeaway", city="City Center",
        blurb="Charcoal-grilled boxes, made to order from four in the afternoon.",
        services=[("Chicken box", "Half bird, rice, slaw."),
                  ("Paneer box", "Charred, same rice."),
                  ("Fries", "Fried to order.")],
        stats=[("4pm", "Open from"), ("11", "Years")],
        review=("Sameer T.", "The fries actually arrive hot, which is rarer than it should be.")),
    "binder": dict(
        brand="Copper Field", sub_type="letterpress card set", material="cotton stock",
        mood="considered, made", price="Rs 1,450",
        obj=(38, 58, 120), ground=(240, 236, 228),
        label="What we do", cta="Order a set", city="Jaipur",
        blurb="Letterpress on cotton stock, pressed one sheet at a time.",
        services=[("Letterpress", "One sheet at a time."),
                  ("Custom sets", "Your own wording."),
                  ("Trade orders", "For studios and shops.")],
        stats=[("1,400", "Sheets pressed"), ("12", "Years at the press")],
        review=("Ana R.", "Used them for a whole wedding and the ink never smudged.")),
    "bloom": dict(
        brand="Marigold Lane", sub_type="linen cushion cover", material="washed linen",
        mood="soft, domestic", price="Rs 1,100",
        obj=(226, 168, 140), ground=(252, 248, 244),
        label="What we make", cta="Add to basket", city="Goa",
        blurb="Washed linen covers, cut and sewn in small batches.",
        services=[("Cushion covers", "Washed linen, hidden zip."),
                  ("Table linen", "Same cloth, longer cut."),
                  ("Repairs", "We re-stitch what we made.")],
        stats=[("600", "Made by hand"), ("2021", "Since")],
        review=("Kim L.", "Washed it eight times and it has only got softer.")),
    "tumble": dict(
        brand="Tumble & Co", sub_type="wooden stacking toy", material="mango wood",
        mood="playful, sturdy", price="Rs 950",
        obj=(52, 120, 190), ground=(253, 247, 235),
        label="What we make", cta="Add to basket", city="Channapatna",
        blurb="Mango wood toys, lacquered in colours that survive a toddler.",
        services=[("Stacking rings", "Seven sizes, one peg."),
                  ("Pull-along", "Wheels that actually roll."),
                  ("Gift sets", "Boxed, ready to give.")],
        stats=[("9", "Colours"), ("3+", "Ages")],
        review=("Dev P.", "Survived two children and still looks new.")),
}


def _plate(w, h, obj, ground):
    """A stand-in for a treated photo: the product centred on the Grade's ground."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), ground)
    d = ImageDraw.Draw(im)
    px, py = int(w * 0.22), int(h * 0.14)
    d.rounded_rectangle([px, py, w - px, h - py],
                        radius=int(min(w, h) * 0.06), fill=obj)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def render(slug: str) -> str:
    """Render one Pack's home page with its own demo seller. Returns HTML."""
    from core.rendering import jinja_env
    from core.packs import PACKS, get_pack
    from core.design import derive_design
    from core.composition import plan_composition, make_sect

    if slug not in PACKS:
        raise KeyError(slug)
    b = BRIEFS.get(slug) or next(iter(BRIEFS.values()))
    pack = get_pack(slug)
    mode = pack.get("mode", "light")

    spec = {"category": "other", "sub_type": b["sub_type"], "material": b["material"],
            "finish": "matte", "mood": b["mood"], "product_type": slug,
            "dominant_colours": ["#%02x%02x%02x" % b["obj"]],
            "geometry": {"orientation": "tall", "fills_frame": 0.6},
            "confidence": 0.85}
    design = derive_design(spec, {"width": 1200, "height": 1600}, spin=0,
                           density="generous", mode=mode)
    theme = dict(design["theme"])
    if pack.get("accent"):
        theme["accent"] = pack["accent"]

    hero = _plate(900, 1150, b["obj"], b["ground"])
    wide = _plate(1400, 823, b["obj"], b["ground"])
    square = _plate(900, 900, b["obj"], b["ground"])

    data = {
        "site_info": {"site_title": b["brand"], "display_name": b["brand"],
                      "tagline": b["blurb"]},
        "home": {"label": b["label"], "cta": b["cta"], "title": b["brand"],
                 "subtitle": b["blurb"],
                 "pillar1_title": b["services"][0][0], "pillar1_desc": b["services"][0][1],
                 "pillar2_title": b["services"][1][0], "pillar2_desc": b["services"][1][1]},
        "about": {"heading": "The workshop",
                  "description": f"A small workshop in {b['city']} making "
                                 f"{b['sub_type']} in {b['material']}.",
                  "mission": "We make what we would use ourselves."},
        "services": [{"title": t, "description": d} for t, d in b["services"]],
        "portfolio": [{"tag": "Recent", "title": b["services"][0][0],
                       "description": b["services"][0][1], "outcome": "Sold out"}],
        "testimonials": [{"name": b["review"][0], "text": b["review"][1]},
                         {"name": "M. Thorne", "text": "Arrived faster than I expected."},
                         {"name": "S. Chen", "text": "Exactly as described."}],
        "stats": [{"number": n, "label": l} for n, l in b["stats"]],
        "faq": [], "pricing": [],
        "contact": {"title": "Get in touch", "description": "We reply within a day.",
                    "phone": "+91 98000 12345", "email": "hello@example.in",
                    "address": b["city"]},
    }
    try:
        src = jinja_env.loader.get_source(jinja_env, f"packs/{slug}/home.html")[0]
        n_sections = max(1, src.count("{{ sect("))
    except Exception:
        n_sections = 5
    comp = plan_composition(data, spec, price=b["price"], n_sections=n_sections)

    ctx = dict(
        site_name=b["brand"], site_title=b["brand"], page_title=b["brand"],
        tagline=b["blurb"], theme=theme,
        footer={"copyright": f"2026 {b['brand']}"},
        layout=["hero", "services", "portfolio", "about", "contact"],
        image_map={}, image_count=1, has_images=True, logo=None,
        favicon_url=None, favicon_apple_url=None, favicon_sized=False,
        share_card_url=None, story_card_url=None, site_url="", site_lang="en",
        services_img=None, testimonials_img=None, overflow_imgs=[], images=[hero],
        shots={"hero": hero, "wide": wide, "square": square,
               "hero_w": 900, "hero_h": 1150, "wide_w": 1400, "wide_h": 823,
               "square_w": 900, "square_h": 900},
        is_cutout=True, price=b["price"], asset_base=pack.get("asset_base", "/a"),
        pack=dict(pack, slug=slug), comp=comp, sect=make_sect(comp),
        stats=data["stats"], **{k: v for k, v in data.items() if k != "stats"})
    return jinja_env.get_template(f"packs/{slug}/home.html").render(**ctx)
