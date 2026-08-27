# OCA Module Search

Search every [OCA](https://github.com/OCA) module across **all repositories** and
Odoo versions **17.0 / 18.0 / 19.0** from one fast page. Type `follow-up` and see
every matching module, which versions it exists in, and a direct link to its folder
on GitHub.

Data comes straight from the OCA GitHub org (each repo's `__manifest__.py` files) —
fresher and more searchable than the Odoo apps store.

## How it works

- **`build_index.py`** — crawls the OCA org via the GitHub API + raw file CDN,
  parses every module manifest, and writes a compact `oca_index.json`. Standard
  library only, no `pip install`.
- **`index.html`** — a static, dependency-free page that loads `oca_index.json`
  and does instant client-side search (name / summary / technical name / repo /
  category / dependencies), version tabs, and category & repo filters.
- **`.github/workflows/build-index.yml`** — rebuilds the index every Monday and
  redeploys the page to GitHub Pages. Free for public repos.

## Run it locally

```bash
export GITHUB_TOKEN=$(gh auth token)     # 5000 req/hr instead of 60
python3 build_index.py                   # writes oca_index.json (~a few minutes)

# then serve the folder (fetch() needs http, not file://):
python3 -m http.server 8000
# open http://localhost:8000
```

Handy flags while testing:

```bash
python3 build_index.py --limit 15                # crawl only 15 repos
python3 build_index.py --branches 18.0           # just one version
python3 build_index.py --branches 16.0 17.0 18.0 # different versions
```

## Deploy to GitHub Pages (free)

1. Create a **public** repo (e.g. `oca-module-search`) and push these files.
2. Repo **Settings → Pages → Build and deployment → Source: GitHub Actions**.
3. Run the workflow once from the **Actions** tab (`Build OCA index → Run workflow`),
   or wait for Monday. It builds the index, commits it, and publishes the page.
4. Share the resulting `https://<org-or-you>.github.io/oca-module-search/` URL.

No token to configure — Actions injects `GITHUB_TOKEN` automatically.
