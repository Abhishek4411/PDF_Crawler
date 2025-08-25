# pdf_crawler.py

import os
import sys
import time
import threading
import requests
import random
import re
import shutil
import signal
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
import logging
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.common.exceptions import WebDriverException, TimeoutException, NoSuchElementException, StaleElementReferenceException
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
import pytesseract
from PIL import Image
from io import BytesIO
from urllib.robotparser import RobotFileParser
import tldextract  # For domain extraction

# ----------------------------- Configuration -----------------------------

DOWNLOADED_PDFS_DIR = "downloaded_pdfs"
LOG_FILE = "pdfcrawler.log"

# ----------------------------- Logging Setup -----------------------------

def setup_logger():
    logger = logging.getLogger('pdf_crawler_logger')
    logger.setLevel(logging.DEBUG)  # Set to DEBUG for detailed logs

    # Remove all handlers associated with the logger object
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # File handler
    fh = logging.FileHandler(LOG_FILE, mode='w')  # Clear log file each run
    fh.setLevel(logging.DEBUG)
    fh_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    fh.setFormatter(fh_formatter)
    logger.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)  # Adjust as needed
    ch_formatter = logging.Formatter('%(levelname)s - %(message)s')
    ch.setFormatter(ch_formatter)
    logger.addHandler(ch)

    return logger

logger = setup_logger()

# ----------------------------- Graceful Shutdown -----------------------------

stop_event = threading.Event()

def handle_signal(signum, frame):
    logger.info(f"Received termination signal: {signum}. Stopping crawler...")
    stop_event.set()

signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)

# ----------------------------- Helper Functions -----------------------------

def sanitize_filename(url):
    parsed = urlparse(url)
    filename = os.path.basename(parsed.path)
    if not filename.endswith('.pdf'):
        filename += '.pdf'
    filename = filename.split('?')[0].split('#')[0]
    return "".join(c if c.isalnum() or c in (' ', '.', '_') else '_' for c in filename)

def is_valid_pdf(url):
    return url.lower().endswith('.pdf')

def normalize_url(url, base_url):
    url = urljoin(base_url, url)
    parsed = urlparse(url)
    cleaned_url = parsed.scheme + "://" + parsed.netloc + parsed.path
    return cleaned_url.rstrip('/')

def get_registered_domain(url):
    ext = tldextract.extract(url)
    return ext.registered_domain

def download_pdf(pdf_url, pdf_list, duplicates_list, base_domain, max_pdfs):
    if len(pdf_list) >= max_pdfs:
        logger.info(f"Reached maximum number of PDFs to download ({max_pdfs}). Skipping further downloads.")
        return
    if get_registered_domain(pdf_url) != base_domain:
        logger.info(f"Skipping PDF from different domain: {pdf_url}")
        return
    try:
        filename = sanitize_filename(pdf_url)
        filepath = os.path.join(DOWNLOADED_PDFS_DIR, filename)
        if not os.path.exists(DOWNLOADED_PDFS_DIR):
            os.makedirs(DOWNLOADED_PDFS_DIR, exist_ok=True)
        if os.path.exists(filepath):
            logger.info(f"Duplicate PDF found: {filename}")
            duplicates_list.append(filename)
            return
        response = requests.get(pdf_url, stream=True, timeout=30, verify=False)
        response.raise_for_status()
        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if stop_event.is_set():
                    logger.info("Stopping PDF download as stop event is set.")
                    return
                if chunk:
                    f.write(chunk)
        pdf_list.append(filename)
        logger.info(f"Downloaded PDF: {filename}")
    except Exception as e:
        logger.error(f"Failed to download PDF from {pdf_url}: {e}")

def extract_pdfs_from_images(soup, base_url, pdf_list, duplicates_list, base_domain, max_pdfs):
    images = soup.find_all('img', src=True)
    for img in images:
        if stop_event.is_set():
            break
        if len(pdf_list) >= max_pdfs:
            logger.info(f"Reached maximum number of PDFs to download ({max_pdfs}). Skipping further downloads.")
            break
        img_url = normalize_url(img['src'], base_url)
        try:
            response = requests.get(img_url, timeout=10, verify=False)
            response.raise_for_status()
            img_data = Image.open(BytesIO(response.content)).convert('L')
            img_data = img_data.point(lambda x: 0 if x < 140 else 255, '1')
            text = pytesseract.image_to_string(img_data, lang='eng')
            pdf_urls = re.findall(r'(https?://\S+?\.pdf)', text, re.IGNORECASE)
            for pdf_url in pdf_urls:
                if stop_event.is_set():
                    break
                pdf_url = normalize_url(pdf_url, base_url)
                if get_registered_domain(pdf_url) == base_domain:
                    download_pdf(pdf_url, pdf_list, duplicates_list, base_domain, max_pdfs)
        except Exception as e:
            logger.error(f"Failed to process image {img_url}: {e}")

def extract_embedded_pdfs(soup, base_url, pdf_list, duplicates_list, base_domain, max_pdfs):
    embed_tags = soup.find_all(['embed', 'object', 'iframe'], src=True)
    for tag in embed_tags:
        if stop_event.is_set():
            break
        if len(pdf_list) >= max_pdfs:
            logger.info(f"Reached maximum number of PDFs to download ({max_pdfs}). Skipping further downloads.")
            break
        src = tag['src']
        pdf_url = normalize_url(src, base_url)
        if is_valid_pdf(pdf_url) and get_registered_domain(pdf_url) == base_domain:
            download_pdf(pdf_url, pdf_list, duplicates_list, base_domain, max_pdfs)

def extract_pdfs_from_text(soup, base_url, pdf_list, duplicates_list, base_domain, max_pdfs):
    text = soup.get_text(separator=' ')
    pdf_urls = re.findall(r'(https?://\S+?\.pdf)', text, re.IGNORECASE)
    for pdf_url in pdf_urls:
        if stop_event.is_set():
            break
        if len(pdf_list) >= max_pdfs:
            logger.info(f"Reached maximum number of PDFs to download ({max_pdfs}). Skipping further downloads.")
            break
        pdf_url = normalize_url(pdf_url, base_url)
        if get_registered_domain(pdf_url) == base_domain:
            download_pdf(pdf_url, pdf_list, duplicates_list, base_domain, max_pdfs)

def detect_available_browsers():
    browsers = [
        {'name': 'Chrome', 'check': shutil.which('google-chrome') or shutil.which('chrome')},
    ]
    for browser in browsers:
        if browser['check']:
            return browser['name']
    return None

def handle_dynamic_content(url, browser_choice):
    driver = None
    try:
        options = None
        service = None
        if browser_choice == 'Chrome':
            options = ChromeOptions()
            options.add_argument("--headless")
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            service = ChromeService(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
        else:
            logger.error(f"Unsupported browser choice: {browser_choice}")
            return None, None
        driver.set_page_load_timeout(60)
        driver.get(url)
        time.sleep(2)
        page_source = driver.page_source
        return driver, page_source
    except TimeoutException as e:
        logger.error(f"Timeout loading page {url}: {e}")
        if driver:
            driver.quit()
        return None, None
    except WebDriverException as e:
        logger.error(f"Selenium WebDriver error: {e}")
        if driver:
            driver.quit()
        return None, None
    except Exception as e:
        logger.error(f"Unexpected error in handle_dynamic_content: {e}")
        if driver:
            driver.quit()
        return None, None

def is_allowed_by_robots(url, user_agent='*'):
    parsed_url = urlparse(url)
    robots_url = f"{parsed_url.scheme}://{parsed_url.netloc}/robots.txt"
    try:
        rp = RobotFileParser()
        rp.set_url(robots_url)
        rp.read()
        allowed = rp.can_fetch(user_agent, url)
        logger.debug(f"Robots.txt allows crawling {url}: {allowed}")
        return allowed
    except Exception as e:
        logger.error(f"Failed to fetch robots.txt from {robots_url}: {e}")
        return True  # Assume allowed if robots.txt cannot be fetched

def extract_pdfs_from_direct_links(soup, base_url, pdf_list, duplicates_list, base_domain, max_pdfs):
    pdf_links = soup.find_all('a', href=True)
    for link in pdf_links:
        if stop_event.is_set():
            break
        if len(pdf_list) >= max_pdfs:
            logger.info(f"Reached maximum number of PDFs to download ({max_pdfs}). Skipping further downloads.")
            break
        href = link['href']
        pdf_url = normalize_url(href, base_url)
        if is_valid_pdf(pdf_url) and get_registered_domain(pdf_url) == base_domain:
            download_pdf(pdf_url, pdf_list, duplicates_list, base_domain, max_pdfs)

def crawl_website(base_url, pdf_list, duplicates_list, browser_choice, max_pages=100, max_pdfs=50):
    visited = set()
    to_visit = [base_url]
    base_domain = get_registered_domain(base_url)
    pages_crawled = 0
    try:
        while to_visit and not stop_event.is_set():
            if pages_crawled >= max_pages:
                logger.info(f"Reached maximum number of pages to crawl ({max_pages}). Stopping.")
                break
            if len(pdf_list) >= max_pdfs:
                logger.info(f"Reached maximum number of PDFs to download ({max_pdfs}). Stopping.")
                break
            current_url = to_visit.pop(0)
            current_url = normalize_url(current_url, base_url)
            if current_url in visited:
                continue
            logger.info(f"Crawling: {current_url}")
            if not is_allowed_by_robots(current_url):
                logger.info(f"Disallowed by robots.txt: {current_url}")
                continue
            visited.add(current_url)
            pages_crawled += 1
            driver, page_source = handle_dynamic_content(current_url, browser_choice)
            if not driver or not page_source:
                continue
            soup = BeautifulSoup(page_source, 'html.parser')
            # Extract PDFs
            extract_pdfs_from_direct_links(soup, current_url, pdf_list, duplicates_list, base_domain, max_pdfs)
            extract_embedded_pdfs(soup, current_url, pdf_list, duplicates_list, base_domain, max_pdfs)
            extract_pdfs_from_images(soup, current_url, pdf_list, duplicates_list, base_domain, max_pdfs)
            extract_pdfs_from_text(soup, current_url, pdf_list, duplicates_list, base_domain, max_pdfs)
            # Find new links to crawl
            for link in soup.find_all('a', href=True):
                href = link['href']
                href = normalize_url(href, current_url)
                if (href not in visited and href not in to_visit and get_registered_domain(href) == base_domain):
                    to_visit.append(href)
                    logger.debug(f"Adding to queue: {href}")
            driver.quit()
            time.sleep(random.uniform(1, 2))
    except Exception as e:
        logger.error(f"An error occurred during crawling: {e}")
    finally:
        if not stop_event.is_set():
            logger.info("Crawling completed successfully.")
        else:
            logger.info("Crawling has been stopped by the user.")

# ----------------------------- Main Function -----------------------------

def main():
    if len(sys.argv) != 2:
        print("Usage: python pdf_crawler.py <URL>")
        sys.exit(1)
    base_url = sys.argv[1]
    if not base_url.startswith('http'):
        base_url = 'http://' + base_url
    pdf_list = []
    duplicates_list = []
    browser_choice = detect_available_browsers()
    if not browser_choice:
        logger.error("No supported browsers are installed on this system.")
        sys.exit(1)
    else:
        logger.info(f"Using browser: {browser_choice}")
    logger.info(f"Started crawling: {base_url}")
    crawl_website(base_url, pdf_list, duplicates_list, browser_choice, max_pages=1000, max_pdfs=100)

if __name__ == "__main__":
    main()
