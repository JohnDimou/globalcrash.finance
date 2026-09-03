# GlobalCrash.finance — static site

Deployable build of the GlobalCrash portal. **No server.** The index is recomputed by a
scheduled GitHub Action every Friday, committed, and Vercel deploys the commit.

```
(repo root)
├── ingest.py                   # pulls 36 live sources, computes the GCI → data/gci_data.json
├── build_page.py               # injects the data into the page source
├── requirements.txt
├── vercel.json                 # outputDirectory = site/public, headers, clean URLs
├── .github/workflows/weekly-print.yml   # Fridays 20:05 UTC: ingest → build → commit → (Vercel deploys)
└── site/
    ├── src/page.html           # canonical page source
    ├── assets/                 # face crop + TTFs for og.png / icons
    ├── build_site.py           # emits public/: home + /methodology + /scorecard + /friday-print
    └── public/                 # ← what Vercel serves
```

## URLs

| Path | What |
|---|---|
| `/` | the gauge |
| `/methodology` | how the number is made |
| `/scorecard` | graded against every crisis since 1990 (table rendered at build time) |
| `/friday-print` | the weekly print, with a live countdown |
| `/gci.json` | free JSON of the latest print (CORS `*`) |
| `/og.png` `/sitemap.xml` `/robots.txt` `/site.webmanifest` | social card, crawl files, PWA manifest |

Every page: full `<head>` (title/description/canonical/hreflang/robots/theme-color), Open Graph +
Twitter card, JSON-LD (WebSite · Dataset · WebApplication · FAQPage on home; BreadcrumbList +
Article on subpages), icons, font preconnects, Vercel Web Analytics + Speed Insights.

## Build locally

```
pip install -r requirements.txt
python3 ingest.py                        # ~1 min, live data
python3 build_page.py site/src/page.html
python3 site/build_site.py
```

## Deploy (once)

1. Fork or clone this repo.
2. Vercel → **Add New Project** → import the repo. `vercel.json` already sets the output directory;
   framework "Other", no build command. Deploy.
3. Project → **Settings → Domains** → add `globalcrash.finance` (+ `www`, redirect to apex).
4. Project → **Analytics → Enable** and **Speed Insights → Enable**. (The scripts are already in
   every page; they 404 until enabled and when served locally — harmless.)
5. Actions tab → run **Friday print** once manually to confirm the pipeline commits and Vercel redeploys.

From then on the site refreshes itself every Friday at 20:05 UTC. Zero servers, zero cost
beyond the domain: GitHub Actions (public repo: free; private: ~2 min/week of the 2,000 free minutes)
and Vercel's Hobby tier.
