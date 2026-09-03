#!/usr/bin/env python3
"""Build the deployable GlobalCrash.finance static site into ./public.

  python3 build_site.py [path-to-artifact-page.html]

Reads the single-file page (the artifact body: <title>, font <link>, <style>, markup,
scripts — no <html>/<head>), and produces a complete standalone document with a full
SEO head (canonical, Open Graph, Twitter card, JSON-LD, icons, manifest), Vercel
Web Analytics + Speed Insights, plus the social image, favicons, robots.txt,
sitemap.xml, a free JSON endpoint of the index, and vercel.json.

The page source is also copied to ./src/page.html so the repo holds the canonical
copy; passing no argument rebuilds from that copy.
"""
import base64, io, json, os, re, shutil, sys
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageFont, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'src', 'page.html')
PUB = os.path.join(HERE, 'public')
ASSETS = os.path.join(HERE, 'assets')
DATA = os.path.join(HERE, '..', 'data', 'gci_data.json')

DOMAIN = 'https://globalcrash.finance'
SITE_NAME = 'GlobalCrash.finance'
TITLE = 'GlobalCrash.finance — Global Crisis Index, the fragility gauge that grades itself'
DESC = ('The Global Crisis Index scores the financial system\'s fragility from 0 to 100 '
        'across 36 free, public indicators — credit, macro, valuation, housing, crypto, '
        'sentiment and geopolitics. Printed every Friday, graded in public against every '
        'crisis since 1990. Measures fragility, not timing.')
ROUTES = {'methodology': '/methodology', 'scorecard': '/scorecard', 'friday-print': '/friday-print'}
PAGES = {
    'methodology': dict(title='Methodology — how the Global Crisis Index is made | GlobalCrash.finance',
                        desc='How the Global Crisis Index is built: 36 free public indicators across seven pillars, rolling 20-year percentile ranks, two-sided scoring, weekly EMA, the correlation layer, known blind spots and the v2 roadmap.',
                        eyebrow='GLOBALCRASH · METHODOLOGY', crumb='Methodology'),
    'scorecard':   dict(title='Scorecard — the gauge graded against every crisis since 1990 | GlobalCrash.finance',
                        desc='What the Global Crisis Index read six months before and at the peak of every verified crisis since 1990 — hits and misses, base rates and false-alarm rates, re-graded at every print.',
                        eyebrow='GLOBALCRASH · SCORECARD', crumb='Scorecard'),
    'friday-print': dict(title='The Friday print — one official reading a week | GlobalCrash.finance',
                        desc='Why the Global Crisis Index publishes one number of record every Friday at 20:00 UTC, what the live Pulse is between prints, and why weekly is the honest cadence for this data.',
                        eyebrow='GLOBALCRASH · THE FRIDAY PRINT', crumb='The Friday print'),
}
KEYWORDS = ('global crisis index, financial crisis index, recession indicator, market crash '
            'index, financial stress index, systemic risk gauge, fear gauge, credit spreads, '
            'yield curve, VIX, CAPE, economic fragility, recession probability')


def load_page(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def split_head_body(page):
    """Lift <title>, the Google-Fonts <link> and the first <style> block into the head."""
    title = re.search(r'<title>.*?</title>\s*', page, re.S)
    page = page.replace(title.group(0), '', 1) if title else page
    link = re.search(r'<link rel="stylesheet" href="https://fonts\.googleapis\.com[^>]*>\s*', page)
    page = page.replace(link.group(0), '', 1) if link else page
    style = re.search(r'<style>.*?</style>\s*', page, re.S)
    page = page.replace(style.group(0), '', 1) if style else page
    return (link.group(0).strip() if link else ''), (style.group(0).strip() if style else ''), page


def reading():
    with open(DATA) as f:
        d = json.load(f)
    return d


def head_html(fontlink, style, d, page=None, extra_ld=None):
    asof = d.get('asof', '')
    gci, band = d.get('gci'), d.get('band', '').title()
    jsonld = [
        {"@context": "https://schema.org", "@type": "WebSite", "name": SITE_NAME,
         "url": DOMAIN + "/", "description": DESC, "inLanguage": "en",
         "publisher": {"@type": "Organization", "name": SITE_NAME, "url": DOMAIN + "/",
                       "logo": {"@type": "ImageObject", "url": DOMAIN + "/icon-512.png"}}},
        {"@context": "https://schema.org", "@type": "Dataset",
         "name": "Global Crisis Index (GCI)",
         "description": "Weekly 0–100 composite of systemic financial fragility built from 36 "
                        "free public indicators across seven pillars, with a reconstructed "
                        "monthly history to 1990 and a public scorecard against every verified "
                        "crisis.",
         "url": DOMAIN + "/", "sameAs": DOMAIN + "/#methodology",
         "license": "https://creativecommons.org/licenses/by/4.0/",
         "creator": {"@type": "Organization", "name": SITE_NAME},
         "temporalCoverage": "1990-01/..", "dateModified": asof,
         "keywords": KEYWORDS.split(', '),
         "distribution": [{"@type": "DataDownload", "encodingFormat": "application/json",
                           "contentUrl": DOMAIN + "/gci.json"}],
         "variableMeasured": [
             {"@type": "PropertyValue", "name": "Global Crisis Index", "value": gci,
              "unitText": "index points (0–100)"},
             {"@type": "PropertyValue", "name": "Band", "value": band}]},
        {"@context": "https://schema.org", "@type": "WebApplication", "name": SITE_NAME,
         "url": DOMAIN + "/", "applicationCategory": "FinanceApplication",
         "operatingSystem": "Web", "browserRequirements": "Requires JavaScript",
         "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
         "description": DESC},
        {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": "What is the Global Crisis Index?",
             "acceptedAnswer": {"@type": "Answer", "text": "A weekly 0–100 composite of systemic financial fragility built from 36 free public indicators across seven pillars (credit, macro, valuation, housing, crypto, sentiment, geopolitics). Higher means more dry tinder is stacked under the global economy."}},
            {"@type": "Question", "name": "Does the index predict crashes?",
             "acceptedAnswer": {"@type": "Answer", "text": "No. It measures how fragile conditions are — how much fuel is stacked — not when a shock strikes. Every historical crisis is graded in public on the scorecard, misses included."}},
            {"@type": "Question", "name": "How often is it updated?",
             "acceptedAnswer": {"@type": "Answer", "text": "The official print is published every Friday at 20:00 UTC from all 36 indicators at their weekly close. Between prints a live Pulse tracks market moves in real time."}},
            {"@type": "Question", "name": "Where does the data come from?",
             "acceptedAnswer": {"@type": "Answer", "text": "Free, public sources only: FRED, the OFR Financial Stress Index, ECB CISS, CBOE, the NY Fed, NBER, Caldara–Iacoviello GPR, the EPU index, DefiLlama and Coin Metrics."}}]}
    ]
    if page:                         # subpages: WebPage + breadcrumb instead of the home schema set
        jsonld = [jsonld[0], {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "GlobalCrash.finance", "item": DOMAIN + "/"},
            {"@type": "ListItem", "position": 2, "name": PAGES[page]['crumb'], "item": DOMAIN + ROUTES[page]}]}]
        if extra_ld: jsonld.append(extra_ld)
    ld = '\n'.join('<script type="application/ld+json">' + json.dumps(x, ensure_ascii=False, separators=(',', ':')) + '</script>' for x in jsonld)
    social_title = f'Global Crisis Index: {gci} · {band}' if gci is not None else 'Global Crisis Index'
    title = PAGES[page]['title'] if page else TITLE
    desc = PAGES[page]['desc'] if page else DESC
    path = ROUTES[page] if page else '/'
    og_title = (PAGES[page]['crumb'] + ' — ' + SITE_NAME) if page else f'{social_title} — {SITE_NAME}'
    routes_js = '<script>window.ROUTES=' + json.dumps(ROUTES) + '</script>'
    return f'''<!DOCTYPE html>
<html lang="en" prefix="og: https://ogp.me/ns#">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{title}</title>
<meta name="description" content="{desc}">
<meta name="keywords" content="{KEYWORDS}">
<meta name="author" content="{SITE_NAME}">
<meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1">
<meta name="googlebot" content="index, follow, max-image-preview:large">
<link rel="canonical" href="{DOMAIN}{path}">
<link rel="alternate" hreflang="en" href="{DOMAIN}{path}">
<link rel="alternate" hreflang="x-default" href="{DOMAIN}{path}">
<link rel="alternate" type="application/json" title="Global Crisis Index — latest print (JSON)" href="/gci.json">
<meta name="language" content="en">
<meta name="coverage" content="Worldwide">
<meta name="distribution" content="global">
<meta name="rating" content="general">
<meta name="referrer" content="strict-origin-when-cross-origin">
<meta name="format-detection" content="telephone=no">
<meta name="color-scheme" content="dark light">
<meta name="theme-color" content="#0B0F14" media="(prefers-color-scheme: dark)">
<meta name="theme-color" content="#F1F3F5" media="(prefers-color-scheme: light)">
<meta name="application-name" content="{SITE_NAME}">
<meta name="apple-mobile-web-app-title" content="GlobalCrash">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">

<meta property="og:type" content="website">
<meta property="og:site_name" content="{SITE_NAME}">
<meta property="og:locale" content="en_US">
<meta property="og:url" content="{DOMAIN}{path}">
<meta property="og:title" content="{og_title}">
<meta property="og:description" content="{desc}">
<meta property="og:image" content="{DOMAIN}/og.png">
<meta property="og:image:secure_url" content="{DOMAIN}/og.png">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="GlobalCrash.finance — Global Crisis Index gauge reading {gci} ({band}) as of {asof}">
<meta property="og:updated_time" content="{asof}T20:00:00Z">

<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{og_title}">
<meta name="twitter:description" content="{desc}">
<meta name="twitter:image" content="{DOMAIN}/og.png">
<meta name="twitter:image:alt" content="GlobalCrash.finance — Global Crisis Index gauge reading {gci} ({band})">
<meta name="twitter:label1" content="Latest print">
<meta name="twitter:data1" content="{gci} · {band}">
<meta name="twitter:label2" content="As of">
<meta name="twitter:data2" content="{asof}">

<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="icon" href="/favicon.ico" sizes="32x32">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="manifest" href="/site.webmanifest">

<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
{fontlink}
{style}
{ld}
{routes_js}

<script defer src="/_vercel/insights/script.js"></script>
<script defer src="/_vercel/speed-insights/script.js"></script>
</head>
<body>
'''


FOOT = '''
</body>
</html>
'''


# ---------- subpages ----------
SUB_CSS = """
<style>
/* subpages: same shell, article layout */
.subpage{max-width:760px; margin:0 auto; padding:44px 20px 72px}
.subpage .crumbs{font-family:"Geist Mono",monospace; font-size:9px; letter-spacing:.16em; color:var(--muted); margin:0 0 18px}
.subpage .crumbs a{color:var(--muted); text-decoration:none} .subpage .crumbs a:hover{color:var(--accent)}
.subpage h1{font-family:"Fraunces","Familjen Grotesk",serif; font-size:clamp(32px,5vw,46px); line-height:1.06; margin:6px 0 18px; letter-spacing:-.01em; text-wrap:balance}
.subpage h3{font-size:17px; margin:30px 0 10px}
.subpage p{font-size:15px; color:var(--muted); line-height:1.7; margin:0 0 12px; max-width:66ch}
.subpage p b{color:var(--ink); font-weight:600}
.subpage .lede{font-size:17px; color:var(--ink); line-height:1.6}
.subpage .ptable{font-size:12px}
.subnav{max-width:760px; margin:0 auto; padding:0 20px 80px; display:grid; gap:12px}
@media(min-width:640px){.subnav{grid-template-columns:repeat(3,1fr)}}
.subnav a{display:block; padding:16px 18px; border:1px solid var(--hair); border-radius:12px; background:var(--panel);
  color:var(--ink); text-decoration:none; transition:border-color .15s, transform .15s; box-shadow:var(--shadow)}
.subnav a:hover{border-color:var(--accent); transform:translateY(-1px)}
.subnav .k{display:block; font-family:"Geist Mono",monospace; font-size:8.5px; letter-spacing:.16em; color:var(--muted); margin-bottom:6px}
.subnav .t{display:block; font-weight:600; font-size:14px}
.subnav a.primary{border-color:var(--accent); background:var(--accent-soft)}
.printline-now{display:inline-flex; align-items:center; gap:10px; font-family:"Geist Mono",monospace; font-size:11px;
  letter-spacing:.08em; color:var(--ink); border:1px solid var(--hair2); border-radius:10px; padding:10px 14px; margin:6px 0 22px}
.printline-now i{width:8px; height:8px; border-radius:50%; background:var(--accent); box-shadow:0 0 0 4px var(--accent-soft)}
</style>"""

SUB_JS = """
<script>
"use strict";
// theme (same behaviour as the gauge page)
(function(){
  let t=null; try{ t=localStorage.getItem('dg-theme'); }catch(e){}
  if(t) document.documentElement.setAttribute('data-theme',t);
  const b=document.getElementById('themeBtn');
  if(b) b.addEventListener('click',()=>{
    const a=document.documentElement.getAttribute('data-theme');
    const light = a ? a==='light' : matchMedia('(prefers-color-scheme: light)').matches;
    const n = light ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme',n); try{ localStorage.setItem('dg-theme',n); }catch(e){}
  });
  // view switch: remember the choice and go to the gauge
  const seg=(v)=>{ try{ localStorage.setItem('dg-view',v); }catch(e){} location.href='/'; };
  const ss=document.getElementById('segSimple'), sa=document.getElementById('segAdv');
  if(ss) ss.addEventListener('click',()=>seg('simple')); if(sa) sa.addEventListener('click',()=>seg('adv'));
  // next-print countdown, if the page shows one
  const cd=document.getElementById('nextPrint');
  if(cd){ const tick=()=>{ const n=new Date(); const d=new Date(Date.UTC(n.getUTCFullYear(),n.getUTCMonth(),n.getUTCDate(),20,0,0));
      while(d.getUTCDay()!==5||d<=n) d.setUTCDate(d.getUTCDate()+1);
      const ms=d-n, D=Math.floor(ms/864e5), H=Math.floor(ms%864e5/36e5), M=Math.floor(ms%36e5/6e4);
      cd.textContent=`NEXT PRINT IN ${D}D ${H}H ${M}M · FRIDAY 20:00 UTC`; }; tick(); setInterval(tick,30000); }
})();
</script>"""


def extract_shell(page):
    """Header + footer markup from the page, with hrefs rewritten for real routes."""
    header = re.search(r'<header[\s\S]*?</header>', page).group(0)
    footer = re.search(r'<footer[\s\S]*?</footer>', page).group(0)
    header = header.replace('href="#" id="brandHome"', 'href="/" id="brandHome"')
    footer = site_links(footer)
    # section anchors must point at the home page from a subpage
    footer = re.sub(r'href="#(?!/)([a-zA-Z][\w-]*)"', lambda m: f'href="/#{m.group(1)}"' if m.group(1) not in ROUTES else f'href="{ROUTES[m.group(1)]}"', footer)
    return header, footer


def site_links(html):
    for k, v in ROUTES.items():
        html = html.replace(f'href="#{k}"', f'href="{v}"')
    return html


def inner(page, elem_id):
    m = re.search(r'<div class="pagev" id="%s" hidden>\s*<div class="pwrap">([\s\S]*?)</div>\s*</div>' % elem_id, page)
    body = m.group(1)
    body = re.sub(r'<button class="pback mono">.*?</button>\s*', '', body)
    body = re.sub(r'<p class="eyebrow">.*?</p>\s*', '', body)
    body = re.sub(r'<h2>(.*?)</h2>', r'<h1>\1</h1>', body, count=1)
    body = re.sub(r'(<h1>.*?</h1>\s*)<p>', r'\1<p class="lede">', body, count=1)
    return site_links(body)


def idx_at(hist, t):
    return min(range(len(hist)), key=lambda i: abs(hist[i][0] - t))


def render_scorecard_rows(d):
    hist, ev = d['history'], d['events_all']
    rows, warned, majors = [], 0, 0
    for e in ev:
        before = hist[idx_at(hist, e['t'] - 0.5)][1]; peak = e['v']
        major = 'BEAR' in (e.get('badge') or '')
        if major:
            majors += 1; warned += (before >= 50)
        cls = 'hot' if before >= 60 else 'warn' if before >= 50 else 'cool'
        yr = '' if re.search(r"'\d\d", e['short']) else f"<span style=\"color:var(--muted)\"> '{str(int(e['t']))[2:]}</span>"
        rows.append(f"<tr><td>{e['short']}{yr}</td><td class=\"n {cls}\">{before:.1f}</td>"
                    f"<td class=\"n\">{peak:.1f}</td><td class=\"n\">{e['dd']}%</td></tr>")
    months = len(hist); ge50 = sum(1 for p in hist if p[1] >= 50)
    ge60 = fa60 = 0
    for p in hist:
        if p[1] >= 60:
            ge60 += 1
            if not any(e['t'] >= p[0] and e['t'] <= p[0] + 1 for e in ev): fa60 += 1
    summary = (f"<b>{warned} of the {majors} bear-market episodes</b> since 1990 had this index at 50 or higher six months "
               f"before peak stress — computed from the same series on the chart, re-graded at every print.")
    stats = (f"<b>Base rates, against ourselves.</b> {round(ge50/months*100)}% of ALL months since 1990 read 50 or higher — so "
             f"\"above 50\" alone is weak evidence, and the table above should be read with that in mind. Of the {ge60} months at 60+, "
             f"{round(fa60/ge60*100) if ge60 else 0}% were false alarms — no anchored episode within the following 12 months. That is the "
             f"noise-to-signal trade-off (Kaminsky &amp; Reinhart) every early-warning system pays; we publish ours instead of hiding it. Monthly granularity.")
    return ''.join(rows), summary, stats


def subnav(current):
    cards = {
        'home':        ('THE GAUGE', 'Back to the live index', '/'),
        'methodology': ('METHODOLOGY', 'How the number is made', ROUTES['methodology']),
        'scorecard':   ('SCORECARD', 'Graded against every crisis', ROUTES['scorecard']),
        'friday-print': ('THE FRIDAY PRINT', 'One official reading a week', ROUTES['friday-print']),
    }
    out = []
    for k, (kicker, t, href) in cards.items():
        if k == current: continue
        cls = ' class="primary"' if k == 'home' else ''
        out.append(f'<a href="{href}"{cls}><span class="k">{kicker}</span><span class="t">{t}</span></a>')
    return '<nav class="subnav" aria-label="More">' + ''.join(out) + '</nav>'


def build_subpages(page, fontlink, style, d, header, footer):
    m_body = inner(page, 'pageMethod')
    s_body = inner(page, 'pageScore')
    rows, summary, stats = render_scorecard_rows(d)
    s_body = s_body.replace('<tbody></tbody>', '<tbody>' + rows + '</tbody>')
    s_body = s_body.replace('<p id="scoreSummary"></p>', f'<p id="scoreSummary">{summary}</p>')
    s_body = s_body.replace('<p id="scoreStats"></p>', f'<p id="scoreStats">{stats}</p>')
    pm = re.search(r'<div class="modal" id="printModal"[\s\S]*?<div class="mbox">([\s\S]*?)</div>\s*</div>', page).group(1)
    pm = re.sub(r'<button class="lockbtn" id="printModalClose">.*?</button>\s*', '', pm)
    pm = re.sub(r'<h3 id="pmTitle">(.*?)</h3>', r'<h1>\1</h1>', pm, count=1)
    pm = re.sub(r'(<h1>.*?</h1>\s*)', r'\1<div class="printline-now"><i></i><span id="nextPrint">NEXT PRINT · FRIDAY 20:00 UTC</span></div>', pm, count=1)
    pm = pm.replace('<p class="pm-note">', '<p class="lede">')
    p_body = pm + ('<h3>What a print contains</h3>'
                   '<p>The headline reading and band, the 13-week velocity, the correlation regime, every pillar score with its live member '
                   'count, and the full list of inputs that moved — all frozen at Friday 20:00 UTC and appended to the public record that the '
                   '<a href="' + ROUTES['scorecard'] + '">scorecard</a> is graded from. The previous print is never revised.</p>'
                   '<h3>Reading it well</h3>'
                   '<p>Treat the print as the verdict and the Pulse as the preview. A print that moves a point or two is noise; a move across a '
                   'band edge, or a velocity swing of five points in a quarter, is the story. Method, weights and blind spots are on the '
                   '<a href="' + ROUTES['methodology'] + '">methodology</a> page.</p>')
    specs = {
        'methodology': (m_body, {"@context": "https://schema.org", "@type": "TechArticle", "headline": "How the Global Crisis Index is made",
                                 "url": DOMAIN + ROUTES['methodology'], "publisher": {"@type": "Organization", "name": SITE_NAME}, "dateModified": d.get('asof', '')}),
        'scorecard':   (s_body, {"@context": "https://schema.org", "@type": "Article", "headline": "The gauge, graded in public",
                                 "url": DOMAIN + ROUTES['scorecard'], "publisher": {"@type": "Organization", "name": SITE_NAME}, "dateModified": d.get('asof', '')}),
        'friday-print': (p_body, {"@context": "https://schema.org", "@type": "Article", "headline": "The Friday print",
                                  "url": DOMAIN + ROUTES['friday-print'], "publisher": {"@type": "Organization", "name": SITE_NAME}, "dateModified": d.get('asof', '')}),
    }
    for slug, (body, ld) in specs.items():
        meta = PAGES[slug]
        html = (head_html(fontlink, style, d, page=slug, extra_ld=ld) + SUB_CSS + '\n' + header + '\n'
                + '<main><article class="subpage">'
                + f'<p class="crumbs"><a href="/">GLOBALCRASH</a> · {meta["crumb"].upper()}</p>'
                + body + '</article>' + subnav(slug) + '</main>\n' + footer + SUB_JS + FOOT)
        os.makedirs(os.path.join(PUB, slug), exist_ok=True)
        with open(os.path.join(PUB, slug, 'index.html'), 'w', encoding='utf-8') as f:
            f.write(html)


def prepare_home(page):
    """Real links instead of overlays; the overlay blocks themselves leave the home document."""
    page = site_links(page)
    page = re.sub(r'<div class="pagev" id="pageMethod" hidden>[\s\S]*?</div>\s*</div>\s*', '', page, count=1)
    page = re.sub(r'<div class="pagev" id="pageScore" hidden>[\s\S]*?</div>\s*</div>\s*', '', page, count=1)
    return page


# ---------- performance: the mood wallpapers leave the HTML ----------
def externalize_moods(page):
    """The four Washington engravings are ~1.6 MB of base64 inline (92% of the page) and block
    parsing. They're drawn at <=11% opacity, upscaled to cover the viewport, so an 800px WebP is
    visually identical. Write them as hashed files under /img and point the page at them."""
    import hashlib
    m = re.search(r'const WASH_MOODS=\[([\s\S]*?)\];', page)
    if not m:
        return page, None
    os.makedirs(os.path.join(PUB, 'img'), exist_ok=True)
    body = m.group(1); urls = []
    def repl(mm):
        raw = base64.b64decode(mm.group(1).split(',', 1)[1])
        im = Image.open(io.BytesIO(raw)).convert('RGB')
        W = 800; im = im.resize((W, int(im.size[1] * W / im.size[0])), Image.LANCZOS)
        buf = io.BytesIO(); im.save(buf, 'WEBP', quality=55, method=6)
        name = f"mood-{hashlib.sha1(buf.getvalue()).hexdigest()[:10]}.webp"
        with open(os.path.join(PUB, 'img', name), 'wb') as f:
            f.write(buf.getvalue())
        url = '/img/' + name; urls.append(url)
        return f"src:'{url}',w:{im.size[0]},h:{im.size[1]}"
    body2 = re.sub(r"src:'(data:image/jpeg;base64,[^']+)',\s*w:\s*\d+,\s*h:\s*\d+", repl, body)
    # preload the stoic face (index 1): the mood every mid-band reading starts on
    return page.replace(m.group(0), 'const WASH_MOODS=[' + body2 + '];', 1), (urls[1] if len(urls) > 1 else (urls[0] if urls else None))


# ---------- assets ----------
def face_with_red_eyes(size):
    """Circular Washington crop with the red eyes baked in at the exact pupil positions."""
    face = Image.open(os.path.join(ASSETS, 'face.png')).convert('RGB').resize((size, size), Image.LANCZOS)
    # pupils at (53.4,69.0) and (107.0,69.0) of the 160px crop
    s = size / 160
    glow = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    r = max(2, int(size * 0.045))
    for (x, y) in ((53.4 * s, 69.0 * s), (107.0 * s, 69.0 * s)):
        g.ellipse([x - r * 2.2, y - r * 2.2, x + r * 2.2, y + r * 2.2], fill=(255, 40, 30, 90))
    glow = glow.filter(ImageFilter.GaussianBlur(r * 0.9))
    g = ImageDraw.Draw(glow)
    for (x, y) in ((53.4 * s, 69.0 * s), (107.0 * s, 69.0 * s)):
        g.ellipse([x - r, y - r, x + r, y + r], fill=(255, 64, 48, 255))
        g.ellipse([x - r * .45, y - r * .45, x + r * .45, y + r * .45], fill=(255, 190, 170, 255))
    out = face.convert('RGBA')
    out.alpha_composite(glow)
    mask = Image.new('L', (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size * 4 - 1, size * 4 - 1], fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)
    out.putalpha(mask)
    return out


def build_icons():
    for n, name in ((512, 'icon-512.png'), (192, 'icon-192.png'), (180, 'apple-touch-icon.png')):
        im = face_with_red_eyes(n)
        if name == 'apple-touch-icon.png':   # iOS wants an opaque square
            bg = Image.new('RGB', (n, n), (11, 15, 20)); bg.paste(im, (0, 0), im); im = bg
        im.save(os.path.join(PUB, name))
    ico = face_with_red_eyes(64)
    ico.save(os.path.join(PUB, 'favicon.ico'), sizes=[(16, 16), (32, 32), (48, 48)])
    # svg favicon = the same mark
    face32 = face_with_red_eyes(96)
    buf = io.BytesIO(); face32.save(buf, 'PNG')
    b64 = base64.b64encode(buf.getvalue()).decode()
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96">'
           f'<image href="data:image/png;base64,{b64}" width="96" height="96"/></svg>')
    with open(os.path.join(PUB, 'favicon.svg'), 'w') as f:
        f.write(svg)


def build_og(d):
    W, H = 1200, 630
    im = Image.new('RGB', (W, H), (11, 15, 20))
    dr = ImageDraw.Draw(im)
    # faint radial glow behind the face
    glow = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([-80, 40, 560, 680], fill=(224, 168, 60, 38))
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    im.paste(Image.alpha_composite(im.convert('RGBA'), glow).convert('RGB'))
    dr = ImageDraw.Draw(im)
    face = face_with_red_eyes(400)
    im.paste(face, (80, 115), face)
    ring = ImageDraw.Draw(im)
    ring.ellipse([78, 113, 482, 517], outline=(58, 72, 86), width=3)

    fr = ImageFont.truetype(os.path.join(ASSETS, 'Fraunces.ttf'), 58)
    fam = ImageFont.truetype(os.path.join(ASSETS, 'Familjen.ttf'), 30)
    mono = ImageFont.truetype(os.path.join(ASSETS, 'GeistMono.ttf'), 19)
    mono_big = ImageFont.truetype(os.path.join(ASSETS, 'GeistMono.ttf'), 118)
    ink, muted, accent = (243, 247, 251), (166, 180, 194), (224, 168, 60)
    x = 560
    dr.text((x, 118), 'GlobalCrash', font=fam, fill=ink)
    w = dr.textlength('GlobalCrash', font=fam)
    dr.text((x + w, 118), '.finance', font=fam, fill=accent)
    dr.text((x, 160), 'GLOBAL CRISIS INDEX · 36 INDICATORS · ONE NUMBER', font=mono, fill=muted)
    # auto-fit the headline + footer line to the text column (1200 - x - 40)
    maxw = W - x - 40
    def fit(text, path, size):
        f = ImageFont.truetype(path, size)
        while dr.textlength(text, font=f) > maxw and size > 12:
            size -= 1; f = ImageFont.truetype(path, size)
        return f
    frp = os.path.join(ASSETS, 'Fraunces.ttf'); mop = os.path.join(ASSETS, 'GeistMono.ttf')
    h1, h2 = 'How much dry tinder is', 'under the global economy?'
    frf = fit(h2, frp, 58)
    dr.text((x, 212), h1, font=frf, fill=ink)
    dr.text((x, 212 + int(frf.size * 1.15)), h2, font=frf, fill=ink)
    gci, band, asof = d.get('gci'), d.get('band', ''), d.get('asof', '')
    bandcol = {'CALM': (46, 160, 140), 'WATCH': (150, 160, 170), 'ELEVATED': (224, 168, 60),
               'HIGH-STRESS': (226, 112, 60), 'CRITICAL': (200, 60, 60)}.get(band, accent)
    dr.text((x, 392), f'{gci}', font=mono_big, fill=ink)
    gw = dr.textlength(f'{gci}', font=mono_big)
    # band pill
    px, py = x + gw + 28, 452
    tw = dr.textlength(band, font=mono)
    dr.rounded_rectangle([px, py, px + tw + 36, py + 46], radius=8, fill=bandcol)
    dr.text((px + 18, py + 11), band, font=mono, fill=(11, 15, 20))
    foot = f'PRINT {asof}  ·  36 INDICATORS  ·  7 PILLARS  ·  SINCE 1990'
    dr.text((x, 540), foot, font=fit(foot, mop, 19), fill=muted)
    im.save(os.path.join(PUB, 'og.png'), optimize=True)


def build_static(d):
    asof = d.get('asof') or datetime.now(timezone.utc).strftime('%Y-%m-%d')
    with open(os.path.join(PUB, 'robots.txt'), 'w') as f:
        f.write(f'User-agent: *\nAllow: /\n\nSitemap: {DOMAIN}/sitemap.xml\n')
    with open(os.path.join(PUB, 'sitemap.xml'), 'w') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
                'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">\n'
                f'  <url><loc>{DOMAIN}/</loc><lastmod>{asof}</lastmod><changefreq>weekly</changefreq><priority>1.0</priority>'
                f'<image:image><image:loc>{DOMAIN}/og.png</image:loc><image:title>Global Crisis Index</image:title></image:image></url>\n'
                + ''.join(f'  <url><loc>{DOMAIN}{p}</loc><lastmod>{asof}</lastmod><changefreq>weekly</changefreq><priority>0.8</priority></url>\n' for p in ROUTES.values())
                + f'  <url><loc>{DOMAIN}/gci.json</loc><lastmod>{asof}</lastmod><changefreq>weekly</changefreq><priority>0.6</priority></url>\n'
                '</urlset>\n')
    with open(os.path.join(PUB, 'site.webmanifest'), 'w') as f:
        json.dump({"name": SITE_NAME, "short_name": "GlobalCrash", "description": DESC,
                   "start_url": "/", "display": "standalone", "background_color": "#0B0F14",
                   "theme_color": "#0B0F14",
                   "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                             {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}]},
                  f, indent=1)
    shutil.copy(DATA, os.path.join(PUB, 'gci.json'))
    with open(os.path.join(HERE, '..', 'vercel.json'), 'w') as f:   # repo root: no dashboard settings needed
        json.dump({
            "$schema": "https://openapi.vercel.sh/vercel.json",
            "framework": None, "buildCommand": None, "installCommand": None,
            "outputDirectory": "site/public",
            "cleanUrls": True, "trailingSlash": False,
            "redirects": [{"source": "/(.*)", "has": [{"type": "host", "value": "www.globalcrash.finance"}],
                           "destination": "https://globalcrash.finance/$1", "permanent": True}],
            "headers": [
                {"source": "/(.*)", "headers": [
                    {"key": "X-Content-Type-Options", "value": "nosniff"},
                    {"key": "X-Frame-Options", "value": "SAMEORIGIN"},
                    {"key": "Referrer-Policy", "value": "strict-origin-when-cross-origin"},
                    {"key": "Permissions-Policy", "value": "camera=(), microphone=(), geolocation=()"},
                    {"key": "Strict-Transport-Security", "value": "max-age=63072000; includeSubDomains; preload"}]},
                {"source": "/(og.png|favicon.ico|favicon.svg|apple-touch-icon.png|icon-192.png|icon-512.png)",
                 "headers": [{"key": "Cache-Control", "value": "public, max-age=86400, stale-while-revalidate=604800"}]},
                {"source": "/img/(.*)", "headers": [
                    {"key": "Cache-Control", "value": "public, max-age=31536000, immutable"}]},
                {"source": "/gci.json", "headers": [
                    {"key": "Access-Control-Allow-Origin", "value": "*"},
                    {"key": "Cache-Control", "value": "public, max-age=3600, stale-while-revalidate=86400"}]}]
        }, f, indent=1)


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else SRC
    page = load_page(src)
    if os.path.abspath(src) != os.path.abspath(SRC):
        os.makedirs(os.path.dirname(SRC), exist_ok=True)
        with open(SRC, 'w', encoding='utf-8') as f:
            f.write(page)
    os.makedirs(PUB, exist_ok=True)
    d = reading()
    fontlink, style, body = split_head_body(page)
    header, footer = extract_shell(body)
    body = prepare_home(body)
    body, first_mood = externalize_moods(body)
    head = head_html(fontlink, style, d)
    if first_mood:
        head = head.replace('<link rel="preconnect" href="https://fonts.googleapis.com">',
                            f'<link rel="preload" as="image" href="{first_mood}" type="image/webp">\n<link rel="preconnect" href="https://fonts.googleapis.com">', 1)
    html = head + body.strip() + FOOT
    with open(os.path.join(PUB, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html)
    build_subpages(page, fontlink, style, d, header, footer)
    build_icons(); build_og(d); build_static(d)
    print(f"built public/index.html ({len(html)//1024} KB) · GCI {d.get('gci')} {d.get('band')} as of {d.get('asof')}")
    print('assets:', ', '.join(sorted(os.listdir(PUB))))


if __name__ == '__main__':
    main()
