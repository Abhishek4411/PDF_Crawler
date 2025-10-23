# pdf_crawler.py — supports "page" (with one-level tender drilldown), "host", "domain".
# Graceful/killable run; safe with Streamlit launcher.

import os
import sys
import time
import threading
import logging
import signal
import re
from collections import deque
from urllib.parse import urlparse, urljoin, urlunparse, parse_qsl, urlencode
import argparse

import requests
from bs4 import BeautifulSoup
import tldextract
from urllib.robotparser import RobotFileParser

from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.common.exceptions import WebDriverException, TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

DOWNLOADED_PDFS_DIR = "downloaded_pdfs"
LOG_FILE = "pdfcrawler.log"
UA = "Mozilla/5.0 (compatible; UniversalPDFCrawler/1.0; +https://example.invalid)"

# ----------------------------- Logging -----------------------------
def setup_logger():
    logger = logging.getLogger('pdf_crawler_logger')
    logger.setLevel(logging.DEBUG)
    for h in logger.handlers[:]:
        logger.removeHandler(h)
    fh = logging.FileHandler(LOG_FILE, mode='w', encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(fh)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter('%(levelname)s - %(message)s'))
    logger.addHandler(ch)
    return logger

logger = setup_logger()
stop_event = threading.Event()

def handle_signal(signum, frame):
    logger.info(f"Received termination signal: {signum}. Stopping crawler...")
    stop_event.set()

# Guard signals so "streamlit run pdf_crawler.py" does not explode
try:
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
except Exception:
    pass

# ----------------------------- URL helpers -----------------------------
def canonicalize_url(u: str, keep_query=True) -> str:
    p = urlparse(u)
    query = ""
    if keep_query and p.query:
        q = parse_qsl(p.query, keep_blank_values=True)
        q.sort()
        query = urlencode(q, doseq=True)
    return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path, p.params, query, ""))

def normalize_url(u: str, base: str) -> str:
    absu = urljoin(base, u)
    p = urlparse(absu)
    return urlunparse((p.scheme, p.netloc, p.path, p.params, p.query, ""))

def get_host(u: str) -> str:
    return urlparse(u).hostname or ""

def same_registered_domain(a: str, b: str) -> bool:
    ea = tldextract.extract(a); eb = tldextract.extract(b)
    return f"{ea.domain}.{ea.suffix}" == f"{eb.domain}.{eb.suffix}"

def is_allowed_by_robots(url: str, user_agent='*') -> bool:
    try:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = RobotFileParser()
        rp.set_url(robots_url); rp.read()
        allowed = rp.can_fetch(user_agent, url)
        logger.debug(f"Robots.txt allows crawling {url}: {allowed}")
        return allowed
    except Exception as e:
        logger.warning(f"Robots.txt unavailable, assuming allowed for {url}: {e}")
        return True

def ensure_dir(path): os.makedirs(path, exist_ok=True)

def sanitize_filename(name: str) -> str:
    name = name.strip().replace("\n"," ").replace("\r"," ")
    keep = " ._-()[]{}"
    return "".join(c if c.isalnum() or c in keep else "_" for c in name)

# ----------------------------- Networking -----------------------------
def build_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    s.max_redirects = 5
    return s

def fetch_with_requests(session: requests.Session, url: str, timeout=30):
    r = session.get(url, timeout=timeout)
    r.raise_for_status()
    ct = (r.headers.get("Content-Type") or "").lower()
    return r, ct

def spin_up_driver():
    options = ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    service = ChromeService(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(60)
    return driver

def fetch_with_selenium(url: str):
    driver = None
    try:
        driver = spin_up_driver()
        driver.get(url)
        time.sleep(2)
        html = driver.page_source
        cookies = driver.get_cookies()
        return html, cookies, driver
    except (TimeoutException, WebDriverException) as e:
        logger.error(f"Selenium error on {url}: {e}")
        if driver: driver.quit()
        return None, [], None

def apply_cookies_from_driver(session: requests.Session, driver):
    try:
        for c in driver.get_cookies():
            domain = c.get("domain") or get_host(driver.current_url)
            session.cookies.set(c.get("name"), c.get("value"), domain=domain, path=c.get("path", "/"))
    except Exception:
        pass

# ----------------------------- PDF detection -----------------------------
PDF_HINTS = (
    ".pdf",
    "FileDownloadServlet",
    "FrontEndFileDownloadServlet",
    "downloadFile",
    "documentDownload",
    "getDocument",
)
URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.I)

def looks_like_pdf_url(u: str) -> bool:
    lu = u.lower()
    return any(h in lu for h in PDF_HINTS) or lu.endswith(".pdf")

def head_says_pdf(session: requests.Session, url: str, timeout=20) -> bool:
    try:
        r = session.head(url, allow_redirects=True, timeout=timeout)
        ct = (r.headers.get("Content-Type") or "").lower()
        cd = r.headers.get("Content-Disposition") or ""
        if "application/pdf" in ct: return True
        if ".pdf" in cd.lower():   return True
    except Exception:
        return False
    return False

def sniff_stream_is_pdf(session: requests.Session, url: str, timeout=30) -> bool:
    try:
        with session.get(url, stream=True, allow_redirects=True, timeout=timeout) as r:
            r.raise_for_status()
            chunk = next(r.iter_content(1024), b"")
            return chunk.startswith(b"%PDF-")
    except Exception:
        return False

def choose_filename(url: str, resp: requests.Response) -> str:
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r'filename\*?=([^;]+)', cd, flags=re.I)
    if m:
        raw = m.group(1).strip().strip('"').strip("'")
        if "''" in raw: raw = raw.split("''", 1)[1]
        return sanitize_filename(raw)
    p = urlparse(resp.url)
    fn = os.path.basename(p.path) or "download.pdf"
    if not fn.lower().endswith(".pdf"):
        fn = fn.split("?")[0] or "download.pdf"
        if not fn.lower().endswith(".pdf"):
            fn = "download.pdf"
    return sanitize_filename(fn)

def uniquify(path: str) -> str:
    base, ext = os.path.splitext(path)
    i, new_path = 1, path
    while os.path.exists(new_path):
        new_path = f"{base} ({i}){ext}"; i += 1
    return new_path

def download_pdf(session: requests.Session, url: str, out_dir: str) -> str | None:
    ensure_dir(out_dir)
    try:
        with session.get(url, stream=True, allow_redirects=True, timeout=60) as r:
            r.raise_for_status()
            ct = (r.headers.get("Content-Type") or "").lower()
            if "application/pdf" not in ct:
                head = next(r.iter_content(1024), b"")
                if not head.startswith(b"%PDF-"):
                    logger.info(f"Not a PDF after sniff: {url}")
                    return None
                filename = choose_filename(url, r)
                path = uniquify(os.path.join(out_dir, filename))
                with open(path, "wb") as f:
                    f.write(head)
                    for chunk in r.iter_content(1024 * 64):
                        if stop_event.is_set(): return None
                        if chunk: f.write(chunk)
                logger.info(f"Downloaded PDF (sniff): {os.path.basename(path)}")
                return path
            filename = choose_filename(url, r)
            path = uniquify(os.path.join(out_dir, filename))
            with open(path, "wb") as f:
                for chunk in r.iter_content(1024 * 64):
                    if stop_event.is_set(): return None
                    if chunk: f.write(chunk)
            logger.info(f"Downloaded PDF: {os.path.basename(path)}")
            return path
    except Exception as e:
        logger.error(f"Failed to download PDF from {url}: {e}")
        return None

# ----------------------------- HTML parsing -----------------------------
def soup_from_html(html: str): return BeautifulSoup(html, "lxml")

def extract_candidate_pdf_urls_from_soup(soup: BeautifulSoup, base_url: str) -> set[str]:
    found = set()
    for a in soup.find_all("a", href=True):
        href = normalize_url(a["href"], base_url)
        if looks_like_pdf_url(href):
            found.add(href)
    for tag in soup.find_all(["embed","object","iframe"]):
        src = tag.get("src") or tag.get("data")
        if not src: continue
        href = normalize_url(src, base_url)
        if looks_like_pdf_url(href):
            found.add(href)
    for meta in soup.find_all("meta", attrs={"http-equiv": re.compile("^refresh$", re.I)}):
        content = meta.get("content") or ""
        m = re.search(r'url=([^;]+)', content, flags=re.I)
        if m:
            href = normalize_url(m.group(1).strip(), base_url)
            if looks_like_pdf_url(href):
                found.add(href)
    # raw URL scraping from scripts/text
    chunks = []
    for s in soup.find_all("script"):
        if s.string: chunks.append(s.string)
    chunks.append(soup.get_text(" "))
    for chunk in chunks:
        for u in URL_RE.findall(chunk or ""):
            href = normalize_url(u, base_url)
            if looks_like_pdf_url(href):
                found.add(href)
    return found

def extract_detail_links_for_gepnic(soup: BeautifulSoup, base_url: str) -> set[str]:
    """Single-page drilldown: find tender detail links typical to GePNIC pages."""
    detail = set()
    for a in soup.find_all("a", href=True):
        txt = (a.get_text(" ") or "").strip()
        href = normalize_url(a["href"], base_url)
        if "FrontEndViewTender" in href or re.search(r"view\s*more\s*details", txt, re.I):
            detail.add(href)
    return detail

def get_page_soup(session: requests.Session, url: str, render: str):
    html, cookies, driver = None, [], None
    # requests first
    try:
        resp, ct = fetch_with_requests(session, url)
        if "text/html" in ct or resp.text.strip().startswith("<"):
            html = resp.text
    except Exception as e:
        logger.warning(f"Requests fetch failed on {url}: {e}")
    # fallback to selenium if needed
    if (html is None or "href" not in html) and render in ("auto","always"):
        html, cookies, driver = fetch_with_selenium(url)
        if html and driver:
            apply_cookies_from_driver(session, driver)
    soup = soup_from_html(html) if html else None
    if driver: driver.quit()
    return soup

# ----------------------------- Crawl -----------------------------
def crawl(start_url: str, scope: str, render: str, max_pages: int, max_pdfs: int, delay: float, respect_robots: bool):
    ensure_dir(DOWNLOADED_PDFS_DIR)
    session = build_session()
    visited_pages = set()
    downloaded_urls = set()
    pages_crawled = 0
    pdfs_downloaded = 0

    def accept_pdf(u: str) -> bool:
        if u in downloaded_urls: return False
        # stay inside fence
        if scope == "page":
            if get_host(u) != get_host(start_url): return False
        elif scope == "host":
            if get_host(u) != get_host(start_url): return False
        elif scope == "domain":
            if not same_registered_domain(u, start_url): return False
        # verify type
        if u.lower().endswith(".pdf") or looks_like_pdf_url(u):
            return head_says_pdf(session, u) or sniff_stream_is_pdf(session, u)
        return head_says_pdf(session, u) or sniff_stream_is_pdf(session, u)

    # ------------- PAGE MODE (with one-level drilldown) -------------
    if scope == "page":
        if respect_robots and not is_allowed_by_robots(start_url):
            logger.info(f"Disallowed by robots.txt: {start_url}")
            logger.info("Crawling completed successfully.")
            return

        logger.info(f"Crawling (single page): {start_url}")
        soup = get_page_soup(session, start_url, render)
        if not soup:
            logger.info("No HTML obtained; nothing to do.")
            logger.info("Crawling completed successfully.")
            return

        candidates = extract_candidate_pdf_urls_from_soup(soup, start_url)

        # If none on listing, probe detail pages linked ON THIS PAGE only (non-recursive)
        if not candidates:
            detail_links = extract_detail_links_for_gepnic(soup, start_url)
            logger.info(f"No PDFs on page. Probing {len(detail_links)} detail link(s) for PDFs...")
            for durl in sorted(detail_links):
                if stop_event.is_set(): break
                if get_host(durl) != get_host(start_url): continue
                if respect_robots and not is_allowed_by_robots(durl): continue
                dsoup = get_page_soup(session, durl, render)
                if not dsoup: continue
                candidates |= extract_candidate_pdf_urls_from_soup(dsoup, durl)
                time.sleep(delay)

        for u in sorted(candidates):
            if stop_event.is_set(): break
            if pdfs_downloaded >= max_pdfs:
                logger.info(f"Reached maximum PDFs ({max_pdfs})."); break
            if accept_pdf(u):
                path = download_pdf(session, u, DOWNLOADED_PDFS_DIR)
                if path:
                    downloaded_urls.add(u)
                    pdfs_downloaded += 1
                    time.sleep(delay)

        logger.info("Crawling completed successfully.")
        return

    # ------------- HOST/DOMAIN MODES (BFS) -------------
    q = deque([start_url])
    while q and not stop_event.is_set():
        page = q.popleft()
        page_canon = canonicalize_url(page)
        if page_canon in visited_pages: continue

        if respect_robots and not is_allowed_by_robots(page):
            logger.info(f"Disallowed by robots.txt: {page}")
            visited_pages.add(page_canon); continue

        if scope == "host" and get_host(page) != get_host(start_url): continue
        if scope == "domain" and not same_registered_domain(page, start_url): continue

        logger.info(f"Crawling: {page}")
        visited_pages.add(page_canon)
        pages_crawled += 1

        soup = get_page_soup(session, page, render)
        if not soup:
            if pages_crawled >= max_pages: break
            continue

        pdf_candidates = extract_candidate_pdf_urls_from_soup(soup, page)
        for u in sorted(pdf_candidates):
            if stop_event.is_set(): break
            if pdfs_downloaded >= max_pdfs:
                logger.info(f"Reached maximum PDFs ({max_pdfs})."); break
            if respect_robots and not is_allowed_by_robots(u): continue
            if accept_pdf(u):
                path = download_pdf(session, u, DOWNLOADED_PDFS_DIR)
                if path:
                    downloaded_urls.add(u); pdfs_downloaded += 1
                    time.sleep(delay)

        if pages_crawled >= max_pages: break

        for a in soup.find_all("a", href=True):
            href = normalize_url(a["href"], page)
            if scope == "host" and get_host(href) != get_host(start_url): continue
            if scope == "domain" and not same_registered_domain(href, start_url): continue
            canon = canonicalize_url(href)
            if canon not in visited_pages:
                q.append(href)

        time.sleep(delay)

    logger.info("Crawling completed successfully.")

# ----------------------------- CLI -----------------------------
def main():
    parser = argparse.ArgumentParser(description="Universal PDF Crawler")
    parser.add_argument("url", help="Start URL")
    parser.add_argument("--scope", choices=["page","host","domain"], default="page")
    parser.add_argument("--render", choices=["auto","always","never"], default="auto")
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--max-pdfs", type=int, default=200)
    parser.add_argument("--delay", type=float, default=0.5)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--respect-robots", dest="respect_robots", action="store_true", default=True)
    group.add_argument("--ignore-robots",  dest="respect_robots", action="store_false")
    args = parser.parse_args()

    start_url = args.url.strip()
    if not start_url.startswith("http"): start_url = "http://" + start_url

    logger.info(f"Scope: {args.scope} | Render: {args.render} | MaxPages={args.max_pages} | MaxPDFs={args.max_pdfs} | Delay={args.delay}s | RespectRobots={args.respect_robots}")
    logger.info(f"Started crawling: {start_url}")

    try:
        crawl(start_url, args.scope, args.render, args.max_pages, args.max_pdfs, args.delay, args.respect_robots)
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        # Make it easy for the UI to detect a graceful end
        pass

if __name__ == "__main__":
    main()
