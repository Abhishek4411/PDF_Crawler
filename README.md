# Universal PDF Crawler

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-app-FF4B4B.svg?logo=streamlit&logoColor=white)](https://streamlit.io)
[![Selenium](https://img.shields.io/badge/Selenium-ChromeDriver-43B02A.svg?logo=selenium&logoColor=white)](https://www.selenium.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](#license)
[![Status](https://img.shields.io/badge/Status-Active-success)](#roadmap)

A fast, robust, **PDF-only** crawler with a clean Streamlit UI.  
It supports two precise scopes out of the box:

- **Single Page (smart drill‑down):** Stay on the *exact URL* you paste. If the page itself doesn’t expose PDFs (e.g. a listing page), the app **peeks one level** into the page’s visible “View Details” links **on that page only** to extract their PDF links, then stops.
- **Host/Subdomain:** Crawl the same host (subdomain) breadth‑first while **never** hopping to other subdomains or parent domains.

This is designed to work reliably with **GePNIC/“eTendering System”** portals (like Kerala), where the listing pages don’t directly expose PDF links but detail pages do. It also works for most static and JS-rendered websites.

---

## ✨ Features

- **Query‑string preserving** URL normalization (critical for GePNIC `DirectLink` pages using `sp=` tokens).
- **Multi‑signal PDF detection:** 
  - Anchors, `embed/object/iframe`, `meta refresh`
  - Raw URL regex (inside text and `<script>` blocks)
  - Type verification via `HEAD` (`Content‑Type`, `Content‑Disposition`) or `%PDF-` magic‑bytes sniffing
- **Smart rendering:** `auto | always | never`. In `auto`, the crawler tries `requests` first, then falls back to headless Chrome/Selenium if the HTML needs JS.
- **Strict scoping:** `page`, `host` (subdomain‑only), `domain` (registered domain).
- **Respect robots.txt** (toggleable).
- **Kill‑safe:** The **Stop Crawling** button terminates the entire process group (crawler + Chrome/ChromeDriver children) for instant stop.
- **Nice UX:** real‑time logs, filterable downloads list, and one‑click **Download All** (ZIP).

---

## 🧭 Table of Contents

- [Demo GIF / Screenshots](#demo-gif--screenshots)
- [Quick Start](#quick-start)
- [How It Works](#how-it-works)
- [Usage](#usage)
  - [UI (Streamlit)](#ui-streamlit)
  - [CLI (Optional)](#cli-optional)
- [Scopes & Examples](#scopes--examples)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [Security & Ethics](#security--ethics)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

---

## Demo GIF / Screenshots

> _Optional_: Add a short GIF of a run, and a screenshot of the **Downloaded PDFs** tab here.

```text
docs/
 ├─ demo.gif
 └─ screenshot-downloads.png
```

---

## 🚀 Quick Start

1. **Install prerequisites**
   - **Python**: 3.10+
   - **Google Chrome**: Stable channel (headless used by Selenium). ChromeDriver is auto-managed by `webdriver-manager`.
2. **Clone & Install**
   ```bash
   git clone https://github.com/your-org/universal-pdf-crawler.git
   cd universal-pdf-crawler
   python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. **Run the app**
   ```bash
   streamlit run app.py
   ```

> **Note:** If you run into Chrome/driver issues on a fresh server, install Google Chrome first (for Debian/Ubuntu you can use the `.deb` from Google’s site).

---

## 🛠 How It Works

### High level
- The UI (**`app.py`**) launches the crawler (**`pdf_crawler.py`**) in a **separate process group** so the **Stop** button can terminate the crawler and all its children (Chrome + ChromeDriver).
- The crawler performs HTML fetch via `requests`, and if it suspects JS-built content, it optionally renders via **headless Chrome** (Selenium). Cookies set in the browser session are injected into the `requests` session for reliable authenticated/static downloads when needed.
- All found links are **strictly filtered by scope** before fetching/downloading. Only **PDFs** are downloaded; everything else is ignored.

### PDF discovery
- Extract links from: `<a href>`, `<embed/src>`, `<object data>`, `<iframe src>`, `meta refresh` → URL.
- Additionally regex-scan page text and `<script>` contents for absolute URLs.
- A URL is considered a **PDF candidate** if it ends with `.pdf` **or** heuristics match (`FileDownloadServlet`, `FrontEndFileDownloadServlet`, `downloadFile`, etc.).
- Before downloading, the crawler verifies:
  - `Content-Type: application/pdf`, or
  - `Content-Disposition` filename ends with `.pdf`, or
  - First bytes start with `%PDF-`.

### “Single Page” drill‑down
Many tender listing pages (e.g., GePNIC **Latest Active Tenders**) don’t directly expose PDFs.  
In **`page`** scope, if no PDFs are found on the input URL, the crawler performs a **one-level, non‑recursive drill‑down** into the page’s **visible tender detail links** (e.g., `FrontEndViewTender`) solely to harvest the PDFs from those detail pages, **then stops**. You still remain within **the same host** and the crawl does not expand further.

---

## 🧑‍💻 Usage

### UI (Streamlit)

1. **Paste a URL** (e.g., Kerala Latest Active Tenders). Keep the entire query string (`?component=...&sp=...`) intact.
2. Select a **Scope**:
   - `page` → Only the URL you pasted. If needed, auto‑probe detail links _on that page only_ to grab PDFs.
   - `host` → Crawl **same subdomain** only (recommended for portal‑wide downloads).
   - `domain` → Crawl all subdomains of the same registered domain.
3. **Rendering**:
   - `auto` (default): try `requests` first; fall back to headless browser if needed.
   - `always`: always use headless browser (slower, but more reliable on heavy JS pages).
   - `never`: never use browser; `requests` only (fastest).
4. Adjust **Max pages / Max PDFs / Delay**, and whether to **Respect robots.txt**.
5. Click **Start Crawling**.  
   - The **Logs** tab streams crawler output.
   - The **Downloaded PDFs** tab lists files (with filter + “Download All”).  
6. **Stop Crawling** kills the crawler and all its children instantly.

### CLI (Optional)

You can also run the crawler without Streamlit:

```bash
python pdf_crawler.py "https://example.com/page" \
  --scope page|host|domain \
  --render auto|always|never \
  --max-pages 100 --max-pdfs 200 --delay 0.5 \
  --respect-robots   # or --ignore-robots
```

Examples:

```bash
# Single page (smart drill‑down)
python pdf_crawler.py "https://etenders.kerala.gov.in/nicgep/app?component=%24DirectLink&page=FrontEndLatestActiveTenders&service=direct&sp=..." --scope page --render auto

# Same subdomain only
python pdf_crawler.py "https://etenders.kerala.gov.in/nicgep/app?component=%24DirectLink&page=Home&service=direct&sp=..." --scope host --render auto --max-pages 500
```

---

## 🎛️ Scopes & Examples

| Scope  | Stays Within                    | Use case |
|-------:|---------------------------------|---------|
| `page` | The single URL you provided     | Listing pages: drill one level into visible “View Details” links to harvest PDFs, then stop |
| `host` | Same host/subdomain (e.g., `etenders.kerala.gov.in`) | Crawl an entire portal (but not other subdomains) |
| `domain` | Same registered domain (e.g., `*.example.com`) | Corporate sites spanning multiple subdomains |

> **Note:** The **Kerala GePNIC** pages use short‑lived tokens (e.g., `sp=`). Always paste URLs with their full query strings so the portal renders the correct tender page.

---

## ⚙️ Configuration

- **Requirements**: see `requirements.txt` (Python, Streamlit, Selenium, webdriver‑manager, BeautifulSoup, lxml, psutil, tldextract).
- **Chrome/Driver**: handled automatically by `webdriver-manager`. Ensure **Google Chrome** is installed on the machine.
- **Downloads**: stored in `downloaded_pdfs/` (auto‑created). Use the **Download All** button to get a ZIP.
- **Logs**: `pdfcrawler.log` is overwritten per run; also visible live in the UI.
- **Robots**: toggle “Respect robots.txt”. When enabled, the crawler checks each page/PDF with Python’s `RobotFileParser` and skips disallowed URLs.
- **Performance**: 
  - Increase **Delay** if the site rate‑limits or if you’re seeing a lot of 429/5xx.
  - Prefer `auto` render; switch to `always` for JS‑heavy sites that hide links until rendered.
  - Use `host` scope (not `domain`) when you need strict subdomain boundaries.

---

## 🧩 Troubleshooting

- **“Stop Crawling” doesn’t stop** → This project launches the crawler in a new **process group** and sends group termination signals, ensuring Chrome/ChromeDriver also stop. If you still see a stuck Chrome, hit **Stop** again; the app escalates from graceful stop to kill.
- **No PDFs on a listing page** → Use `page` scope: the app auto‑probes the visible detail links once to harvest documents.
- **Chrome/driver errors** → Ensure **Google Chrome** is installed. Try `render=always`. On Linux servers, add `--no-sandbox` and `--disable-dev-shm-usage` (already enabled in code).
- **403 / login / expired token (`sp=`)** → Refresh the listing page in your browser to copy a **fresh** URL with the latest token and paste again.
- **robots.txt blocks** → Either uncheck **Respect robots.txt** (if you have permission) or contact the site owner.

---

## 🔐 Security & Ethics

- Always review and respect the target website’s **Terms of Use** and **robots.txt**.
- Use **reasonable delays** and small concurrency (this crawler is single‑threaded by design).
- Only crawl and download documents you are **authorized** to access.
- Never attempt to bypass authentication or technical restrictions.

---

## 🗺️ Roadmap

- [ ] Option to auto‑follow `sitemap.xml` (when allowed)
- [ ] Retry/backoff policy customization
- [ ] Export CSV of found document URLs
- [ ] Optional hash‑dedupe (skip if same content hash)
- [ ] Unit tests for URL normalization & scope filters

---

## 🤝 Contributing

PRs are welcome! Please open an issue first to discuss major changes.  
Make sure to run the app locally and test both `page` and `host` scopes. Consider including a short screen capture for UI changes.

---

## 📝 License

This project is licensed under the **MIT License**. See the [LICENSE](LICENSE) file (or add one at the project root).

---

## 📎 Acknowledgments / References

- Streamlit rerun & session state
  - `st.rerun`: https://docs.streamlit.io/develop/api-reference/execution-flow/st.rerun  
  - Session State: https://docs.streamlit.io/develop/api-reference/caching-and-state/st.session_state
- Headless Chrome with Selenium
  - Selenium blog on new headless: https://www.selenium.dev/blog/2023/headless-is-going-away/  
  - Chrome headless docs: https://developer.chrome.com/docs/chromium/headless
- Robots.txt parsing
  - Python `urllib.robotparser`: https://docs.python.org/3/library/urllib.robotparser.html
- Subprocess process‑group control
  - `subprocess.Popen(..., start_new_session=True)`: https://docs.python.org/3/library/subprocess.html
- GePNIC portals (example starting points)
  - Kerala eTendering: https://etenders.kerala.gov.in/nicgep/app
  - National eProcurement: https://etenders.gov.in/eprocure/app
