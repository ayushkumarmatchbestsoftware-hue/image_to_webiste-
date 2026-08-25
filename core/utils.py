import random
import json
import re
import asyncio
import traceback
# NOTE: PIL is intentionally NOT imported at module level — it's only needed
# by generate_website_content_logic() below when images were actually
# uploaded, so it's imported lazily right at that point of use instead.

# NICHE_DESIGN, LAYOUT_POOLS, PALETTE_MAP, INDUSTRY_TEMPLATES would go here
# or be imported from constants.py

def clean_editor_artifacts(html: str) -> str:
    """Remove editor-only markup before persisting or exporting a page."""
    if not html: return html
    # 1. Remove toolbars and bars
    html = re.sub(r'<div\s+id=["\']edit-toolbar["\'][\s\S]*?</div>(\s*</div>)?', '', html, flags=re.IGNORECASE)
    html = re.sub(r'<div\s+id=["\']save-bar["\'][\s\S]*?</div>', '', html, flags=re.IGNORECASE)
    html = re.sub(r'<div\s+id=["\']save-indicator["\'][\s\S]*?</div>', '', html, flags=re.IGNORECASE)
    html = re.sub(r'<div\s+id=["\']edit-hint-bar["\'][\s\S]*?</div>', '', html, flags=re.IGNORECASE)
    
    # 2. Remove image edit indicators
    html = re.sub(r'<div\s+class=["\']edit-img-indicator["\'][\s\S]*?</div>', '', html, flags=re.IGNORECASE)
    
    # 3. Remove style blocks containing editor CSS (like the grain overlay or hidden UI)
    html = re.sub(r'<style[^>]*>\s*/\*\s*Hide legacy editor UI[\s\S]*?\.eb-img-edit:hover[\s\S]*?</style>', '', html, flags=re.IGNORECASE)
    
    # 4. Clean up interactive attributes
    html = re.sub(r'\s*contenteditable=(["\'])?true\1?', '', html, flags=re.IGNORECASE)
    # NOTE: data-swappable / data-sync-field are permanent template markers (not
    # editor-injected artifacts) used respectively to gate the image-swap overlay
    # and to keep duplicated sections (e.g. Contact) in sync across pages, so they
    # must survive this cleanup and are excluded from the strip below.
    html = re.sub(r'\s*data-(?!swappable=|sync-field=)[a-zA-Z0-9_\-]+=(["\']).*?\1', '', html)
    
    # 5. Remove editor-related classes
    def _clean_classes(m):
        sp = m.group(1)
        classes = m.group(2).split()
        # image-drop-zone and drag-hover are added by editor JS
        kept = [c for c in classes if not c.startswith("eb-") and c not in ("image-drop-zone", "drag-hover")]
        if not kept: return sp
        return f'{sp}class="{" ".join(kept)}"'
    
    html = re.sub(r'(\s*)class="([^"]*)"', _clean_classes, html)
    html = re.sub(r"(\s*)class='([^']*)'", _clean_classes, html)
    
    # 6. Remove editor scripts
    html = re.sub(r'<script[^>]*sortablejs[^>]*>\s*</script>', '', html, flags=re.IGNORECASE)
    html = re.sub(r'new\s+Sortable\([^,]+,\s*\{[\s\S]*?\}\s*\)\s*;', '', html, flags=re.IGNORECASE)
    html = html.replace('initImageDropZones();', '').replace('showSaveIndicator();', '')
    
    return html

def sync_fields_between_pages(source_html: str, target_html: str) -> str:
    """
    Copy the text of elements tagged data-sync-field="..." from source_html into
    the matching-tagged elements of target_html. Used to keep sections that are
    duplicated across two pages (e.g. the Contact block embedded in home.html and
    the standalone contact.html) in sync after the user edits either one in the
    editor. Best-effort: fields missing from either side are left untouched.
    """
    if not source_html or not target_html:
        return target_html

    field_pattern = re.compile(
        r'<([a-zA-Z0-9]+)([^>]*\bdata-sync-field=(["\'])([\w.\-]+)\3[^>]*)>(.*?)</\1>',
        re.IGNORECASE | re.DOTALL
    )

    new_values = {}
    for tag, attrs, quote, field, content in field_pattern.findall(source_html):
        new_values[field] = content

    if not new_values:
        return target_html

    def _replace(m):
        tag, attrs, quote, field, content = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        if field in new_values and new_values[field] != content:
            return f"<{tag}{attrs}>{new_values[field]}</{tag}>"
        return m.group(0)

    return field_pattern.sub(_replace, target_html)

def get_niche_key_logic(prompt: str, NICHE_DESIGN) -> str:
    p = prompt.lower()
    for kw in NICHE_DESIGN:
        if kw in p:
            return kw
    return None

def get_fallback_tokens_logic(prompt: str, NICHE_DESIGN):
    key = get_niche_key_logic(prompt, NICHE_DESIGN)
    return NICHE_DESIGN.get(key, {
        "font_heading": "Roboto", "font_body": "Roboto",
        "primary": "#4f46e5", "primary_dark": "#312e81",
        "bg": "#ffffff", "bg_alt": "#f8fafc",
        "text_main": "#0f172a", "text_muted": "#475569",
        "accent": "#f59e0b", "mood": "strategic minimalist"
    })

def get_layout_blueprint_logic(prompt: str, LAYOUT_POOLS):
    p = prompt.lower()
    for kw, layouts in LAYOUT_POOLS.items():
        if kw in p:
            return random.choice(layouts)
    return random.choice(LAYOUT_POOLS.get("default", [["hero", "about", "services", "contact"]]))

def hex_to_rgb(hex_color: str):
    try:
        h = hex_color.strip().lstrip('#')
        if len(h) == 3:
            h = ''.join(c*2 for c in h)
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    except Exception:
        return None

def relative_luminance(r, g, b):
    vals = []
    for c in [r, g, b]:
        c /= 255.0
        vals.append(c/12.92 if c <= 0.03928 else ((c+0.055)/1.055)**2.4)
    return 0.2126*vals[0] + 0.7152*vals[1] + 0.0722*vals[2]

def contrast_ratio(hex1: str, hex2: str) -> float:
    rgb1 = hex_to_rgb(hex1)
    rgb2 = hex_to_rgb(hex2)
    if not rgb1 or not rgb2:
        return 1.0
    l1 = relative_luminance(*rgb1)
    l2 = relative_luminance(*rgb2)
    lighter = max(l1, l2)
    darker  = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)

def is_dark(hex_color: str) -> bool:
    rgb = hex_to_rgb(hex_color)
    if not rgb:
        return False
    return relative_luminance(*rgb) < 0.3

def rgba_from_hex(hex_color: str, opacity: float = 0.12) -> str:
    rgb = hex_to_rgb(hex_color)
    if not rgb:
        return f"rgba(0,0,0,{opacity})"
    return f"rgba({rgb[0]},{rgb[1]},{rgb[2]},{opacity})"

async def generate_website_content_logic(genai_client, prompt, system_prompt, get_fallback_tokens, get_layout_blueprint, INDUSTRY_TEMPLATES, validate_and_fix_theme, image_paths=None, image_count=0, industry=None, requested_sections=None):
    if not genai_client:
        return None
    try:
        fallback = get_fallback_tokens(prompt)
        layout   = get_layout_blueprint(prompt)

        # Portfolio item COUNT is intentionally independent of how many images
        # were uploaded — the template renders a portfolio card fine with no
        # photo, so a business with 0-2 images must not end up with an empty
        # portfolio section. expected_port_count only governs how many of
        # those items get PAIRED with an uploaded image (images 3-5, capped
        # at 3 slots); it is never the total item count Gemini is told to
        # produce.
        expected_port_count = min(3, max(0, image_count - 2))
        image_summary = ""
        if image_count > 0:
            image_summary = f"\nIMAGE ALLOCATION (MUST MATCH):\n- Image 1: Hero\n- Image 2: About"
            if image_count >= 3:
                image_summary += f"\n- Images 3 to {min(5, image_count)}: Portfolio Case Studies (pair these with the first {expected_port_count} portfolio items — see rule 2 below)."

        industry_block = ""
        industry_template = INDUSTRY_TEMPLATES.get(industry) if industry else None
        if industry_template:
            industry_block = f"\n\n{industry_template['inject']}\n"
            layout = industry_template["default_sections"]

        # If the user explicitly picked which sections to include (the
        # "Pages to Include" checkboxes), that choice is authoritative and
        # overrides both the auto-inferred blueprint above and the industry
        # template's defaults — otherwise a section the user deliberately
        # selected could be silently skipped just because it wasn't part of
        # a generic guess for this business type.
        if requested_sections:
            layout = requested_sections
        layout_line = (
            f"REQUIRED SECTIONS: {layout}\n"
            "The user explicitly selected these sections — you MUST generate complete, "
            "real content for every single one of them. Do not omit or skip any section "
            "in this list, even if it seems less central to this specific business."
            if requested_sections
            else f"SUGGESTED LAYOUT: {layout}"
        )

        full_prompt = f"""Business: {prompt}{industry_block}
{image_summary}

DEVELOPER DESIGN TOKENS (pair these with Roboto for a premium look):
- font_heading: Roboto
- font_body: Roboto
- primary color: {fallback['primary']}
- bg: {fallback['bg']}
- bg_alt: {fallback['bg_alt']}
- text_main: {fallback['text_main']}
- text_muted: {fallback['text_muted']}
- accent: {fallback['accent']}
- mood: {fallback['mood']}

{layout_line}
IMAGES UPLOADED: {image_count}
{"Analyze uploaded images for color and mood." if image_count > 0 else "No images — generate theme from business type only."}

Write copy like a senior creative director for a high-end boutique agency. Roleplay as a human.
CRITICAL RULES for Copy:
1. NO AI-ISMS: Ban "Unlock", "Empower", "Comprehensive", "Seamless", "Journey", "Elevate".
2. PORTFOLIO: Generate AT LEAST 3 portfolio/case-study items with specific, realistic project details for this business — this is a fixed minimum regardless of how many images were uploaded; a portfolio card with no photo is perfectly fine and already supported. {f"The first {expected_port_count} of these items will be paired with uploaded images 3 to {min(5, image_count)} — make those especially detailed and specific to what the image shows." if expected_port_count > 0 else ""}
3. TESTIMONIALS: If "testimonials" is a requested/suggested section, generate AT LEAST 3 distinct testimonials, each 2-3 full sentences (not a one-line blurb) that references a specific, concrete detail of the work this business does — varied author names, roles, and company names, no two testimonials praising the same aspect of the business.
4. Content must be 100% realistic. If it's a law firm, sound like a top attorney. If it's a software agency, use specialized technical terms.
5. NO "Welcome to", "Experience the", "Discover the", "Our journey".
6. FACTS: The "Business:" text above is the only source of truth. Extract EVERY concrete fact it contains (years of experience, counts, locations, founding date, certifications, etc.) — not just the first one you notice — and reuse the exact same figures everywhere they are relevant; "stats" is the canonical place for all of them together. Never state a number that isn't grounded in what was actually written above, and never contradict a fact you already stated elsewhere in this same response. If the business description genuinely contains few or no quantifiable facts, a short or empty "stats" list is correct — never invent additional numbers just to make the list longer."""

        content_parts = [full_prompt]
        # Keep track of opened PIL images so we can close them after the API call
        _opened_images = []
        if image_paths:
            from PIL import Image  # lazy import — only needed when images were uploaded
            for i, path in enumerate(image_paths):
                try:
                    img = Image.open(path)
                    _opened_images.append(img)
                    label = "Hero Image" if i == 0 else "About Background" if i == 1 else f"Portfolio Project Image {i-1}"
                    content_parts.append(f"--- ATTACHED IMAGE {i+1} ({label}) ---")
                    content_parts.append(img)
                except Exception as e:
                    print(f">>> [IMG ERR] {path}: {str(e)}")

        try:
            response = await asyncio.to_thread(
                genai_client.models.generate_content,
                model="gemini-3.1-flash-image-preview",
                contents=content_parts,
                config={
                    "system_instruction": system_prompt,
                    "temperature": 0.85,
                    # Was 4500 — too tight a budget for a full 9-section site
                    # (rich portfolio + 3+ multi-sentence testimonials + stats
                    # + faq + pricing + theme/layout metadata all compete for
                    # the same output budget). The model was complying with
                    # the "at least 3 items" instructions but compressing each
                    # field's substance to fit, which read as "sparse" content
                    # even though prompt wording alone kept getting stronger.
                    "max_output_tokens": 8192
                }
            )
        finally:
            # ── Leak Fix #2: Always close PIL image objects to free file handles
            # and decoded bitmap memory. Done AFTER the API call so Gemini can
            # read the pixels, but immediately after to avoid holding RAM longer.
            for _img in _opened_images:
                try:
                    _img.close()
                except Exception:
                    pass
            _opened_images.clear()

        if not response: return None

        try:
            text = response.text.strip()
        except ValueError as ve:
            return None

        text = re.sub(r'^```[a-z]*\n?', '', text, flags=re.MULTILINE)
        text = re.sub(r'\n?```$', '', text, flags=re.MULTILINE)
        text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as je:
            return None

        data["theme"] = validate_and_fix_theme(
            data.get("theme", {}), fallback, has_image=(image_count > 0)
        )
        return data

    except Exception as e:
        traceback.print_exc()
        return None

def validate_and_fix_theme(theme: dict, fallback: dict, has_image: bool = False) -> dict:
    fixed = dict(theme)
    required = ["primary", "primary_dark", "bg", "bg_alt", "text_main", "text_muted",
                "accent", "font_heading", "font_body", "hero_style", "card_style", "divider_style"]
    for key in required:
        if not fixed.get(key):
            fixed[key] = fallback.get(key, "")

    fixed["primary_light"] = rgba_from_hex(fixed.get("primary", "#111111"), 0.12)
    bg = fixed.get("bg", "#ffffff")
    bg_rgb = hex_to_rgb(bg)
    if bg_rgb:
        fixed["nav_bg"] = f"rgba({bg_rgb[0]},{bg_rgb[1]},{bg_rgb[2]},0.92)"
    else:
        fixed["nav_bg"] = "rgba(255,255,255,0.92)"

    bg_lum = relative_luminance(*bg_rgb) if bg_rgb else 1.0
    primary_hex = fixed.get("primary", "#111111")
    bg_too_similar_to_primary = False
    if bg_rgb:
        p_rgb = hex_to_rgb(primary_hex)
        if p_rgb:
            p_lum = relative_luminance(*p_rgb)
            if abs(bg_lum - p_lum) < 0.15:
                bg_too_similar_to_primary = True

    if bg_too_similar_to_primary:
        fixed["bg"]     = fallback.get("bg", "#f8f8f8")
        fixed["bg_alt"] = fallback.get("bg_alt", "#efefef")
        bg = fixed["bg"]
        bg_rgb = hex_to_rgb(bg)
        bg_lum = relative_luminance(*bg_rgb) if bg_rgb else 1.0
        if bg_rgb:
            fixed["nav_bg"] = f"rgba({bg_rgb[0]},{bg_rgb[1]},{bg_rgb[2]},0.92)"

    text_main = fixed.get("text_main", "#111111")
    if contrast_ratio(text_main, bg) < 4.5:
        if bg_lum < 0.4:
            fixed["text_main"]  = "#f5f5f5"
            fixed["text_muted"] = "rgba(255,255,255,0.62)"
        else:
            fixed["text_main"]  = fallback.get("text_main", "#111111")
            fixed["text_muted"] = fallback.get("text_muted", "#555555")

    if fixed.get("bg") == fixed.get("bg_alt"):
        fixed["bg_alt"] = fallback.get("bg_alt", "#f0f0f0")

    if fixed.get("hero_style") not in ["split-left", "fullbleed", "bold-center"]:
        fixed["hero_style"] = "split-left"
    
    if not has_image:
        fixed["hero_style"] = "bold-center"

    if fixed.get("card_style") not in ["flat", "outlined", "elevated"]:
        fixed["card_style"] = "elevated"

    if fixed.get("divider_style") not in ["diagonal", "wave", "none"]:
        fixed["divider_style"] = "none"

    top_tier = ["Roboto", "Plus Jakarta Sans", "Outfit", "Fraunces", "Playfair Display"]
    if not fixed.get("font_heading") or fixed["font_heading"] not in top_tier:
        fixed["font_heading"] = fallback.get("font_heading", "Roboto")
    if not fixed.get("font_body") or fixed["font_body"] not in top_tier:
        fixed["font_body"] = fallback.get("font_body", "Roboto")

    return fixed

def build_image_map_logic(image_context: list, layout: list):
    clean_images = [img for img in image_context if img and isinstance(img, str) and img.startswith('http')]
    num_imgs = len(clean_images)
    mapping = {"hero": None, "about": None, "portfolio": [], "services": None, "testimonials": None, "overflow": []}
    if num_imgs == 0: return mapping

    VISUAL_PRIORITY = ["hero", "about", "portfolio", "services", "testimonials"]
    user_layout_set = set(layout)
    assignment_queue = [s for s in VISUAL_PRIORITY if s == "hero" or s in user_layout_set]
    img_idx = 0

    for section in assignment_queue:
        if img_idx >= num_imgs: break
        if section == "portfolio":
            remaining = clean_images[img_idx:img_idx + 3]
            mapping["portfolio"] = remaining
            img_idx += len(remaining)
        else:
            mapping[section] = clean_images[img_idx]
            img_idx += 1

    if img_idx < num_imgs: mapping["overflow"] = clean_images[img_idx:]
    return mapping
async def perform_chat_edit_logic(genai_client, instruction: str, html: str, page_name: str) -> dict:
    """Uses LLM to perform structural or content edits on existing HTML."""
    system_prompt = """You are an expert Frontend Web Developer.
Task: Edit the provided HTML based on user instructions.
Input: User instruction and current HTML.
Output: JSON object with 'html' (the full updated HTML) and 'summary' (briefly what you changed).

RULES:
1. PRESERVE ALL SCRIPTS and STYLES unless explicitly asked to change them.
2. KEEP all 'eb-' classes and 'contenteditable' attributes if present.
3. OUTPUT ONLY valid HTML within the JSON.
4. Response MUST be a JSON object."""

    try:
        content = f"Instruction: {instruction}\n\nPage Name: {page_name}\n\nHTML Content:\n{html}"
        
        response = await asyncio.to_thread(
            genai_client.models.generate_content,
            model="gemini-2.0-flash",
            contents=[content],
            config={
                "system_instruction": system_prompt,
                "temperature": 0.3,
                "response_mime_type": "application/json"
            }
        )

        if not response or not response.text:
            return {"error": "AI returned no response"}

        try:
            data = json.loads(response.text)
            return data
        except json.JSONDecodeError:
            # Fallback regex extraction if not perfect JSON
            match = re.search(r'\{.*\}', response.text, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            return {"error": "Failed to parse AI response as JSON"}

    except Exception as e:
        traceback.print_exc()
        return {"error": str(e)}
