"""
In-place editing for preview mode.

Injected into a rendered page when it is served with ?edit=1. Turns the text
the generator wrote into editable text, adds a small toolbar, and posts the
result back. Nothing is baked into the Pack templates, so a Pack stays a plain
static design and the editing behaviour lives in exactly one place.

What is editable: headings, paragraphs, list text, the price tag and the CTA
label — i.e. the strings the copy stage produced. Structure, nav and images are
not editable, because moving elements is explicitly out of scope (PRD §5, "we
are not a design tool").

The seller's photo IS replaceable: clicking it opens a file picker, and the new
image is posted to /replace-image.
"""

EDIT_SELECTORS = (
    "h1,h2,h3,h4,h5,h6,"
    "p,"
    "#welcome b,"
    ".price-tag,.cta,.enquire,.more,"
    ".card h3,.card p,"
    ".review__text,.review__name,.review__role,"
    ".rating-summary__count,"
    ".quote p,.quote span,"
    ".three .it h3,.three .it p,"
    "#featured h4,#featured p,"
    "#articles p,"
    "#contact-block p,.contact-in p,"
    "li span"
)

EDIT_JS = """
<style id="eb-style">
  [contenteditable="true"]:hover { outline:1px dashed rgba(0,120,255,.45);
        outline-offset:3px; border-radius:2px; cursor:text; }
  [contenteditable="true"]:focus { outline:2px solid #0078ff; outline-offset:3px; }
  #eb-bar { position:fixed; left:0; right:0; bottom:0; z-index:2147483647;
        background:#12151b; color:#e6e9ef; border-top:1px solid #2a2f3a;
        font:13px/1 ui-sans-serif,system-ui,Segoe UI,Roboto,sans-serif;
        display:flex; align-items:center; gap:10px; padding:9px 14px; }
  #eb-bar b { font-weight:600; }
  #eb-bar .sp { flex:1; }
  #eb-bar button { border:1px solid #2a2f3a; background:#1e222b; color:#e6e9ef;
        padding:7px 14px; border-radius:6px; font:inherit; cursor:pointer; }
  #eb-bar button.primary { background:#5b8cff; border-color:#5b8cff; color:#fff;
        font-weight:600; }
  #eb-bar button:disabled { opacity:.5; cursor:not-allowed; }
  #eb-msg { color:#9aa3b2; }
  #eb-msg.ok { color:#3ecf8e; } #eb-msg.err { color:#ff5c5c; }
  .eb-img-hot { outline:2px dashed rgba(0,120,255,.5); outline-offset:3px; cursor:pointer; }
  body { padding-bottom:52px !important; }
  @media print { #eb-bar,#eb-style { display:none !important; } }
</style>
<div id="eb-bar">
  <b>Edit mode</b>
  <span id="eb-msg">click any text to change it &middot; click the photo to replace it</span>
  <span class="sp"></span>
  <button id="eb-undo">Undo</button>
  <button id="eb-view">View</button>
  <button id="eb-dl">Download</button>
  <button id="eb-save" class="primary">Save</button>
</div>
<input type="file" id="eb-file" accept="image/*" style="display:none">
<script>
(function(){
  var SEL = "__SELECTORS__", WID = "__WID__", PAGE = "__PAGE__";
  var msg = document.getElementById('eb-msg');
  var dirty = false, stack = [];

  function say(t, c){ msg.textContent = t; msg.className = c || ''; }

  // Make generated text editable, skipping anything inside the toolbar.
  document.querySelectorAll(SEL).forEach(function(el){
    if (el.closest('#eb-bar')) return;
    if (!el.textContent.trim()) return;
    el.setAttribute('contenteditable','true');
    el.addEventListener('focus', function(){ stack.push([el, el.innerHTML]); });
    el.addEventListener('input', function(){ dirty = true; say('unsaved changes'); });
  });

  // Photo replacement.
  var picker = document.getElementById('eb-file'), target = null;
  document.querySelectorAll('img').forEach(function(im){
    if (im.closest('#eb-bar')) return;
    im.classList.add('eb-img-hot');
    im.addEventListener('click', function(e){
      e.preventDefault(); target = im; picker.click();
    });
  });
  picker.addEventListener('change', function(){
    var f = picker.files[0]; if (!f || !target) return;
    var fd = new FormData(); fd.append('website_id', WID); fd.append('image', f);
    say('uploading photo...');
    fetch('/replace-image', {method:'POST', body:fd})
      .then(function(r){ return r.json(); })
      .then(function(d){
        if (d.error) { say(d.error, 'err'); return; }
        // Swap every copy of the old src, since one photo fills several slots.
        var old = target.getAttribute('src');
        document.querySelectorAll('img[src="'+old+'"]').forEach(function(x){
          x.setAttribute('src', d.url);
        });
        dirty = true; say('photo replaced - remember to Save', 'ok');
      })
      .catch(function(e){ say(String(e), 'err'); });
  });

  // ---- reviews: add, remove, and set the star rating ----
  // A seller's real reviews are not the ones a model invented, so these have
  // to be genuinely editable rather than only re-wordable.
  function star(el, n){
    el.textContent = '★★★★★☆☆☆☆☆'.slice(5 - n, 10 - n);
    el.setAttribute('data-rating', n);
  }
  function wireReview(card){
    if (card.dataset.ebWired) return;
    card.dataset.ebWired = '1';
    var stars = card.querySelector('.review__stars');
    if (stars){
      stars.style.cursor = 'pointer';
      stars.title = 'Click to set the rating';
      stars.addEventListener('click', function(e){
        var box = stars.getBoundingClientRect();
        var n = Math.max(1, Math.min(5, Math.ceil((e.clientX - box.left) / (box.width / 5))));
        star(stars, n); dirty = true; say('rating set to ' + n);
      });
    }
    var del = document.createElement('button');
    del.type = 'button'; del.className = 'eb-del'; del.textContent = 'Remove';
    del.addEventListener('click', function(){
      card.remove(); dirty = true; say('review removed');
    });
    card.appendChild(del);
  }
  var revWrap = document.getElementById('eb-reviews');
  if (revWrap){
    revWrap.querySelectorAll('[data-eb-item="review"]').forEach(wireReview);
    var add = document.createElement('button');
    add.type = 'button'; add.className = 'eb-add'; add.textContent = '+ Add a review';
    add.addEventListener('click', function(){
      var first = revWrap.querySelector('[data-eb-item="review"]');
      var card = first ? first.cloneNode(true)
                       : document.createElement('article');
      if (!first){ card.className = 'review'; card.setAttribute('data-eb-item','review'); }
      delete card.dataset.ebWired;
      var d = card.querySelector('.eb-del'); if (d) d.remove();
      var t = card.querySelector('.review__text');
      if (t) t.textContent = 'Write what a customer told you.';
      var n = card.querySelector('.review__name'); if (n) n.textContent = 'Their name';
      var r = card.querySelector('.review__role'); if (r) r.textContent = 'Where they are';
      var av = card.querySelector('.avatar'); if (av) av.textContent = 'T';
      revWrap.appendChild(card);
      card.querySelectorAll(SEL).forEach(function(el){
        el.setAttribute('contenteditable','true');
        el.addEventListener('input', function(){ dirty = true; say('unsaved changes'); });
      });
      wireReview(card);
      dirty = true; say('review added - now edit it');
      card.scrollIntoView({behavior:'smooth', block:'center'});
    });
    revWrap.insertAdjacentElement('afterend', add);
  }

  document.getElementById('eb-undo').addEventListener('click', function(){
    var last = stack.pop();
    if (!last) { say('nothing to undo'); return; }
    last[0].innerHTML = last[1]; dirty = true; say('undone');
  });

  document.getElementById('eb-save').addEventListener('click', function(){
    var btn = this; btn.disabled = true; say('saving...');
    var doc = document.documentElement.cloneNode(true);
    ['eb-bar','eb-style','eb-file'].forEach(function(id){
      var n = doc.querySelector('#'+id); if (n) n.remove();
    });
    var s = doc.querySelector('script[data-eb]'); if (s) s.remove();
    doc.querySelectorAll('[contenteditable]').forEach(function(n){
      n.removeAttribute('contenteditable');
    });
    doc.querySelectorAll('.eb-img-hot').forEach(function(n){
      n.classList.remove('eb-img-hot');
    });
    doc.querySelectorAll('.eb-del,.eb-add').forEach(function(n){ n.remove(); });
    doc.querySelectorAll('[data-eb-wired]').forEach(function(n){
      n.removeAttribute('data-eb-wired');
    });
    fetch('/save', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({website_id: WID, page_name: PAGE,
                            html: '<!doctype html>' + doc.outerHTML})
    }).then(function(r){ return r.json(); })
      .then(function(d){
        btn.disabled = false;
        if (d.success) { dirty = false; say('saved', 'ok'); }
        else say(d.error || 'save failed', 'err');
      })
      .catch(function(e){ btn.disabled = false; say(String(e), 'err'); });
  });

  document.getElementById('eb-view').addEventListener('click', function(){
    location.href = location.pathname;
  });
  document.getElementById('eb-dl').addEventListener('click', function(){
    location.href = '/download/' + WID;
  });

  window.addEventListener('beforeunload', function(e){
    if (dirty) { e.preventDefault(); e.returnValue = ''; }
  });
})();
</script>
"""


def inject(html: str, website_id: str, page_name: str = "home.html") -> str:
    """Add the edit layer just before </body>."""
    layer = (EDIT_JS
             .replace("__SELECTORS__", EDIT_SELECTORS)
             .replace("__WID__", website_id)
             .replace("__PAGE__", page_name))
    layer = layer.replace("<script>", '<script data-eb="1">', 1)
    if "</body>" in html:
        return html.replace("</body>", layer + "\n</body>", 1)
    return html + layer
