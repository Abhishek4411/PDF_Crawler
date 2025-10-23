import os
import subprocess
import streamlit as st
import shutil
import base64
from streamlit_autorefresh import st_autorefresh
import psutil
import zipfile
from urllib.parse import urlparse
import random
import sys
import atexit
import signal

# ----------------------------- Streamlit Page Configuration -----------------------------

st.set_page_config(
    page_title="Universal PDF Crawler",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ----------------------------- Configuration -----------------------------

DOWNLOADED_PDFS_DIR = "downloaded_pdfs"
LOG_FILE = "pdfcrawler.log"
PID_FILE = "crawler.pid"

# ----------------------------- Initialize Session State -----------------------------

if 'crawl_count' not in st.session_state:
    st.session_state['crawl_count'] = 0

if 'pdf_list' not in st.session_state:
    st.session_state['pdf_list'] = []

if 'is_crawling' not in st.session_state:
    st.session_state['is_crawling'] = False

if 'app_initialized' not in st.session_state:
    st.session_state['app_initialized'] = False

# ----------------------------- Helper Functions -----------------------------

def is_crawler_running():
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                pid = int(f.read().strip())
            if psutil.pid_exists(pid):
                p = psutil.Process(pid)
                # ensure it is our crawler
                if 'python' in p.name().lower() and any('pdf_crawler.py' in ' '.join(c.cmdline()) for c in [p]):
                    return True
            # stale PID file
            os.remove(PID_FILE)
        except Exception:
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
    return False

def _kill_process_group(pid: int, timeout: float = 5.0):
    """Terminate the whole process group (crawler + its children) robustly."""
    try:
        if hasattr(os, "getpgid") and hasattr(os, "killpg"):
            pgid = os.getpgid(pid)
            # Graceful terminate first
            os.killpg(pgid, signal.SIGTERM)
        else:
            # Windows / no killpg: terminate children via psutil
            if psutil.pid_exists(pid):
                proc = psutil.Process(pid)
                for child in proc.children(recursive=True):
                    child.terminate()
                proc.terminate()
    except Exception:
        pass

    # wait a bit
    try:
        if psutil.pid_exists(pid):
            proc = psutil.Process(pid)
            proc.wait(timeout=timeout)
    except Exception:
        pass

    # force kill if still alive
    if psutil.pid_exists(pid):
        try:
            if hasattr(os, "getpgid") and hasattr(os, "killpg"):
                pgid = os.getpgid(pid)
                os.killpg(pgid, signal.SIGKILL)
            else:
                proc = psutil.Process(pid)
                for child in proc.children(recursive=True):
                    try:
                        child.kill()
                    except Exception:
                        pass
                try:
                    proc.kill()
                except Exception:
                    pass
        except Exception:
            pass

def start_crawler(url, scope, render_mode, max_pages, max_pdfs, delay_s, obey_robots):
    """
    Starts the PDF crawler as a subprocess in its own process group and writes its PID to PID_FILE.
    """
    # Fresh logs and downloads
    if os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)
    if os.path.exists(DOWNLOADED_PDFS_DIR):
        shutil.rmtree(DOWNLOADED_PDFS_DIR)

    cmd = [
        sys.executable, 'pdf_crawler.py', url,
        '--scope', scope,                # 'page' | 'host' | 'domain'
        '--render', render_mode,         # 'auto' | 'always' | 'never'
        '--max-pages', str(max_pages),
        '--max-pdfs', str(max_pdfs),
        '--delay', str(delay_s),
    ]
    cmd += ['--respect-robots' if obey_robots else '--ignore-robots']

    # Start crawler in a NEW SESSION / process group so we can kill the group later.
    # (Equivalent to setsid; safer than preexec_fn on multithreaded envs.)
    # Docs: start_new_session parameter. 
    # https://docs.python.org/3/library/subprocess.html  (Popen)
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True  # <--- critical
    )

    with open(PID_FILE, 'w') as f:
        f.write(str(process.pid))

    st.session_state['crawl_count'] += 1
    st.session_state['is_crawling'] = True
    st.sidebar.success(f"Started crawling {url} (PID: {process.pid}).")
    # immediate UI refresh
    try:
        st.rerun()
    except Exception:
        st.experimental_rerun()

def stop_crawler():
    """
    Stops the PDF crawler subprocess by reading the PID file and terminating the WHOLE PROCESS GROUP.
    """
    if not os.path.exists(PID_FILE):
        st.sidebar.warning("No active crawler to stop.")
        return

    try:
        with open(PID_FILE, 'r') as f:
            pid = int(f.read().strip())
    except Exception as e:
        st.sidebar.error(f"Bad PID file: {e}")
        try:
            os.remove(PID_FILE)
        except Exception:
            pass
        return

    try:
        if psutil.pid_exists(pid):
            _kill_process_group(pid, timeout=5.0)
            st.sidebar.success("Crawler stopped successfully.")
        else:
            st.sidebar.info("Crawler process not found. It may have already stopped.")
    except Exception as e:
        st.sidebar.error(f"Error stopping crawler: {e}")
    finally:
        if os.path.exists(PID_FILE):
            try:
                os.remove(PID_FILE)
            except Exception:
                pass
        st.session_state['is_crawling'] = False
        # immediate UI refresh
        try:
            st.rerun()
        except Exception:
            st.experimental_rerun()

def read_log():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r', encoding="utf-8", errors="ignore") as f:
            return f.read()
    return "No logs available."

def list_pdfs():
    if os.path.exists(DOWNLOADED_PDFS_DIR):
        pdf_files = sorted(os.listdir(DOWNLOADED_PDFS_DIR))
        st.session_state['pdf_list'] = pdf_files
        return pdf_files
    return []

def get_base64_encoded_image(image_path):
    if not os.path.exists(image_path):
        return ""
    with open(image_path, 'rb') as img_file:
        encoded = base64.b64encode(img_file.read()).decode()
    return encoded

def create_zip_file():
    zip_filename = "downloaded_pdfs.zip"
    with zipfile.ZipFile(zip_filename, 'w') as zipf:
        for pdf_file in st.session_state.get('pdf_list', []):
            zipf.write(os.path.join(DOWNLOADED_PDFS_DIR, pdf_file), pdf_file)
    return zip_filename

def validate_url(url):
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except Exception:
        return False

def cleanup_on_start():
    # Stop any running crawler from previous sessions
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                pid = int(f.read().strip())
            if psutil.pid_exists(pid):
                _kill_process_group(pid, timeout=5.0)
        except Exception:
            pass
        try:
            os.remove(PID_FILE)
        except Exception:
            pass

    # Fresh start
    if os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)
    if os.path.exists(DOWNLOADED_PDFS_DIR):
        shutil.rmtree(DOWNLOADED_PDFS_DIR)
    st.session_state['is_crawling'] = False
    st.session_state['pdf_list'] = []

def cleanup_on_exit():
    # Try to stop any running crawler on app shutdown
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                pid = int(f.read().strip())
            if psutil.pid_exists(pid):
                _kill_process_group(pid, timeout=3.0)
        except Exception:
            pass
        try:
            os.remove(PID_FILE)
        except Exception:
            pass

atexit.register(cleanup_on_exit)

# ----------------------------- Streamlit Interface -----------------------------

def main():
    if not st.session_state['app_initialized']:
        cleanup_on_start()
        st.session_state['app_initialized'] = True

    st_autorefresh(interval=5000, limit=None, key="auto_refresh")

    background = get_base64_encoded_image('background.png')
    logo = get_base64_encoded_image('tyrone-logo.png')

    st.markdown(
        f"""
        <style>
        .stApp {{
            background-image: url("data:image/png;base64,{background}");
            background-size: cover;
            background-repeat: no-repeat;
            background-attachment: fixed;
            background-position: center;
        }}
        .chat-bubble {{
            background: rgba(255, 255, 255, 0.9);
            border-radius: 10px;
            padding: 15px;
            margin-bottom: 10px;
            box-shadow: 2px 2px 5px rgba(0,0,0,0.1);
            max-height: 400px;
            overflow-y: auto;
            white-space: pre-wrap;
            font-family: monospace;
            font-size: 14px;
        }}
        .logo {{ text-align: center; margin-bottom: 20px; }}
        .logo img {{ max-width: 200px; }}
        .footer {{
            position: fixed; left: 0; bottom: 0; width: 100%;
            text-align: center; color: #999999; font-size: 12px; padding: 10px;
        }}
        </style>
        """,
        unsafe_allow_html=True
    )

    if logo:
        st.markdown(f"""<div class="logo"><img src="data:image/png;base64,{logo}" alt="Logo"></div>""", unsafe_allow_html=True)
    else:
        st.markdown("""<div class="logo"><h2>Universal PDF Crawler</h2></div>""", unsafe_allow_html=True)

    st.markdown("<h1 style='text-align: center;'>📂 Universal PDF Crawler</h1>", unsafe_allow_html=True)

    # Sidebar
    st.sidebar.header("🛠 Controls")
    url_input = st.sidebar.text_input("Enter Website URL:", value="", placeholder="https://etenders.kerala.gov.in/...")

    st.sidebar.markdown("### Scope")
    scope = st.sidebar.radio(
        "Where should we look?",
        options=["page", "host", "domain"],
        index=0,
        help="page = only this URL (but will peek into visible tender detail links)\nhost = this subdomain only\ndomain = *.example.com"
    )

    st.sidebar.markdown("### Rendering")
    render_mode = st.sidebar.radio(
        "Use headless browser?",
        options=["auto", "always", "never"],
        index=0,
        help="auto: try requests first; if nothing useful is found, fall back to Selenium."
    )

    with st.sidebar.expander("Advanced limits"):
        max_pages = st.number_input("Max pages to crawl", min_value=1, max_value=10000, value=100, step=10)
        max_pdfs = st.number_input("Max PDFs to download", min_value=1, max_value=10000, value=200, step=10)
        delay_s   = st.number_input("Delay between requests (seconds)", min_value=0.0, max_value=10.0, value=0.5, step=0.1)
        obey_robots = st.checkbox("Respect robots.txt", value=True)

    st.sidebar.markdown("---")

    # Utility actions
    c1, c2 = st.sidebar.columns(2)
    if c1.button("Clear Downloads 🧹"):
        if os.path.exists(DOWNLOADED_PDFS_DIR):
            shutil.rmtree(DOWNLOADED_PDFS_DIR)
        st.session_state['pdf_list'] = []
        st.sidebar.success("Cleared downloaded PDFs.")
        try:
            st.rerun()
        except Exception:
            st.experimental_rerun()

    if c2.button("Clear Logs 📝"):
        if os.path.exists(LOG_FILE):
            os.remove(LOG_FILE)
        st.sidebar.success("Cleared logs.")
        try:
            st.rerun()
        except Exception:
            st.experimental_rerun()

    crawler_running = is_crawler_running()
    st.session_state['is_crawling'] = crawler_running

    # Start/Stop
    if not crawler_running:
        if st.sidebar.button("Start Crawling 🔍"):
            if url_input.strip() == "":
                st.sidebar.error("Please enter a valid URL to start crawling.")
            elif not validate_url(url_input):
                st.sidebar.error("Invalid URL. Please enter a valid URL.")
            else:
                start_crawler(
                    url=url_input.strip(),
                    scope=scope,
                    render_mode=render_mode,
                    max_pages=int(max_pages),
                    max_pdfs=int(max_pdfs),
                    delay_s=float(delay_s),
                    obey_robots=obey_robots
                )
    else:
        if st.sidebar.button("Stop Crawling 🛑"):
            stop_crawler()

    st.sidebar.markdown(f"**Crawl Count:** {st.session_state.get('crawl_count', 0)}")

    # Tabs
    tabs = st.tabs(["Home", "Downloaded PDFs", "Logs"])

    with tabs[0]:
        st.header("📊 Crawling Status")
        status_display = st.empty()
        progress_bar = st.progress(0)
        log_content = read_log()
        if crawler_running:
            try:
                with open(PID_FILE, 'r') as f:
                    pid = int(f.read().strip())
                status = f"Crawling in progress. Process PID: {pid}"
            except Exception:
                status = "Crawling in progress."
            status_display.info(status)
            progress_bar.progress(random.randint(0, 100))
        else:
            if "Crawling completed successfully." in log_content:
                status_display.success("Crawling completed successfully.")
            elif "Crawling has been stopped by the user." in log_content:
                status_display.warning("Crawling stopped by user.")
            else:
                status_display.info("Idle")
            progress_bar.empty()

    with tabs[1]:
        st.header("📄 Downloaded PDFs")
        pdf_list = list_pdfs()
        if pdf_list:
            q = st.text_input("Filter by filename:", value="")
            show = [p for p in pdf_list if q.lower() in p.lower()]
            if not show:
                st.info("No matching PDFs.")
            else:
                links = []
                for pdf in show:
                    pdf_path = os.path.join(DOWNLOADED_PDFS_DIR, pdf)
                    with open(pdf_path, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode()
                    href = f'<a href="data:application/octet-stream;base64,{b64}" download="{pdf}">{pdf}</a>'
                    links.append(href)
                st.markdown(f'<div class="chat-bubble">{"<br>".join(links)}</div>', unsafe_allow_html=True)
                zip_filename = create_zip_file()
                with open(zip_filename, "rb") as f:
                    st.download_button(
                        label="Download All PDFs 📥",
                        data=f,
                        file_name=zip_filename,
                        mime="application/zip"
                    )
        else:
            st.info("No PDFs downloaded yet.")

    with tabs[2]:
        st.header("📝 Logs")
        log_content = read_log()
        st.markdown(f"<div class='chat-bubble'><pre>{log_content}</pre></div>", unsafe_allow_html=True)

    st.markdown("<div class='footer'>Developed with ❤️ using Streamlit</div>", unsafe_allow_html=True)

if __name__ == "__main__":
    main()
