# app.py

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

# ----------------------------- Streamlit Page Configuration -----------------------------

st.set_page_config(
    page_title="Universal PDF Crawler",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ----------------------------- Configuration -----------------------------

# Directory to save downloaded PDFs
DOWNLOADED_PDFS_DIR = "downloaded_pdfs"

# Log file path
LOG_FILE = "pdfcrawler.log"

# PID file path
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
    """
    Checks if the crawler subprocess is currently running by reading the PID file.
    """
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                pid = int(f.read())
            if psutil.pid_exists(pid):
                process = psutil.Process(pid)
                # Ensure the process is indeed the crawler
                if 'python' in process.name().lower() and 'pdf_crawler.py' in ' '.join(process.cmdline()).lower():
                    return True
            # If PID doesn't match, remove the PID file
            os.remove(PID_FILE)
        except:
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
    return False

def start_crawler(url):
    """
    Starts the PDF crawler as a subprocess and writes its PID to a file.
    """
    # Start the crawler process
    process = subprocess.Popen(
        [sys.executable, 'pdf_crawler.py', url],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    with open(PID_FILE, 'w') as f:
        f.write(str(process.pid))
    st.session_state['crawl_count'] += 1
    st.session_state['is_crawling'] = True
    st.sidebar.success(f"Started crawling {url} (PID: {process.pid}).")

def stop_crawler():
    """
    Stops the PDF crawler subprocess by reading the PID file and terminating the process.
    """
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                pid = int(f.read())
            if psutil.pid_exists(pid):
                process = psutil.Process(pid)
                process.terminate()
                try:
                    process.wait(timeout=5)
                    st.sidebar.success("Crawler stopped successfully.")
                except psutil.TimeoutExpired:
                    process.kill()
                    st.sidebar.error("Crawler did not terminate gracefully and was killed.")
            else:
                st.sidebar.info("Crawler process not found. It might have already stopped.")
            os.remove(PID_FILE)
        except Exception as e:
            st.sidebar.error(f"Error stopping crawler: {e}")
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
        finally:
            st.session_state['is_crawling'] = False
    else:
        st.sidebar.warning("No active crawler to stop.")

def read_log():
    """
    Reads the log file and returns its content.
    """
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r') as f:
            return f.read()
    return "No logs available."

def list_pdfs():
    """
    Lists all downloaded PDFs.
    """
    if os.path.exists(DOWNLOADED_PDFS_DIR):
        pdf_files = sorted(os.listdir(DOWNLOADED_PDFS_DIR))
        st.session_state['pdf_list'] = pdf_files
        return pdf_files
    return []

def get_base64_encoded_image(image_path):
    """
    Encodes an image to base64 for embedding in HTML/CSS.
    """
    if not os.path.exists(image_path):
        return ""
    with open(image_path, 'rb') as img_file:
        encoded = base64.b64encode(img_file.read()).decode()
    return encoded

def create_zip_file():
    """
    Creates a ZIP file of all downloaded PDFs.
    """
    zip_filename = "downloaded_pdfs.zip"
    with zipfile.ZipFile(zip_filename, 'w') as zipf:
        for pdf_file in st.session_state.get('pdf_list', []):
            zipf.write(os.path.join(DOWNLOADED_PDFS_DIR, pdf_file), pdf_file)
    return zip_filename

def validate_url(url):
    """
    Validates the entered URL.
    """
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except:
        return False

def cleanup_on_start():
    """
    Cleans up any existing crawler processes when the app starts.
    """
    # Stop any running crawler
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                pid = int(f.read())
            if psutil.pid_exists(pid):
                process = psutil.Process(pid)
                process.terminate()
                try:
                    process.wait(timeout=5)
                    print(f"Stopped existing crawler process with PID {pid}.")
                except psutil.TimeoutExpired:
                    process.kill()
                    print(f"Killed unresponsive crawler process with PID {pid}.")
            else:
                print(f"No process with PID {pid} found. Removing PID file.")
            os.remove(PID_FILE)
        except Exception as e:
            print(f"Error stopping existing crawler: {e}")
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
    # Check for any pdf_crawler.py processes without a PID file
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        cmdline = proc.info.get('cmdline')
        if cmdline and isinstance(cmdline, list):
            cmdline_str = ' '.join(cmdline)
            if 'pdf_crawler.py' in cmdline_str:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                    print(f"Terminated rogue pdf_crawler.py process with PID {proc.info['pid']}.")
                except psutil.TimeoutExpired:
                    proc.kill()
                    print(f"Killed unresponsive pdf_crawler.py process with PID {proc.info['pid']}.")
                except Exception as e:
                    print(f"Error stopping process with PID {proc.info['pid']}: {e}")
    # Delete PDFs and logs
    if os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)
    if os.path.exists(DOWNLOADED_PDFS_DIR):
        shutil.rmtree(DOWNLOADED_PDFS_DIR)
    st.session_state['is_crawling'] = False
    st.session_state['pdf_list'] = []

def cleanup_on_exit():
    """
    Cleans up any existing crawler processes when the app exits.
    """
    stop_crawler()
    # Clear logs and PDFs on exit
    if os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)
    if os.path.exists(DOWNLOADED_PDFS_DIR):
        shutil.rmtree(DOWNLOADED_PDFS_DIR)

atexit.register(cleanup_on_exit)

# ----------------------------- Streamlit Interface -----------------------------

def main():
    # Perform cleanup only once when the app starts
    if not st.session_state['app_initialized']:
        cleanup_on_start()
        st.session_state['app_initialized'] = True

    # Auto-refresh every 5 seconds to ensure the UI stays updated
    st_autorefresh(interval=5000, limit=None, key="auto_refresh")

    # Apply custom CSS for background and chat bubbles
    background = get_base64_encoded_image('background.png')
    logo = get_base64_encoded_image('tyrone-logo.png')  # Adjust to your logo file name

    st.markdown(
        f"""
        <style>
        /* Background image */
        .stApp {{
            background-image: url("data:image/png;base64,{background}");
            background-size: cover;
            background-repeat: no-repeat;
            background-attachment: fixed;
            background-position: center;
        }}
        /* Custom styles */
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
        .logo {{
            text-align: center;
            margin-bottom: 20px;
        }}
        .logo img {{
            max-width: 200px;
        }}
        .footer {{
            position: fixed;
            left: 0;
            bottom: 0;
            width: 100%;
            text-align: center;
            color: #999999;
            font-size: 12px;
            padding: 10px;
        }}
        </style>
        """,
        unsafe_allow_html=True
    )

    # Display logo
    if logo:
        st.markdown(
            f"""
            <div class="logo">
                <img src="data:image/png;base64,{logo}" alt="Logo">
            </div>
            """,
            unsafe_allow_html=True
        )
    else:
        st.markdown(
            """
            <div class="logo">
                <h2>Universal PDF Crawler</h2>
            </div>
            """,
            unsafe_allow_html=True
        )

    # Title
    st.markdown("<h1 style='text-align: center;'>📂 Universal PDF Crawler</h1>", unsafe_allow_html=True)

    # Sidebar for input and controls
    st.sidebar.header("🛠 Controls")
    url_input = st.sidebar.text_input("Enter Website URL to Crawl:", value="", placeholder="https://www.example.com")

    # Determine if crawler is running
    crawler_running = is_crawler_running()
    st.session_state['is_crawling'] = crawler_running

    # Toggle Start/Stop Button
    if not crawler_running:
        toggle_button_label = "Start Crawling 🔍"
        if st.sidebar.button(toggle_button_label):
            if url_input.strip() == "":
                st.sidebar.error("Please enter a valid URL to start crawling.")
            elif not validate_url(url_input):
                st.sidebar.error("Invalid URL. Please enter a valid URL.")
            else:
                # Start the crawler
                start_crawler(url_input)
    else:
        toggle_button_label = "Stop Crawling 🛑"
        if st.sidebar.button(toggle_button_label):
            stop_crawler()

    st.sidebar.markdown("---")
    st.sidebar.write(f"**Crawl Count:** {st.session_state.get('crawl_count', 0)}")

    # Main area with tabs
    tabs = st.tabs(["Home", "Downloaded PDFs", "Logs"])

    with tabs[0]:
        st.header("📊 Crawling Status")
        status_display = st.empty()
        progress_bar = st.progress(0)
        log_content = read_log()
        if crawler_running:
            # Attempt to read the PID and confirm if it's running
            try:
                with open(PID_FILE, 'r') as f:
                    pid = int(f.read())
                status = f"Crawling in progress. Process PID: {pid}"
            except:
                status = "Crawling in progress."
            status_display.info(status)
            # Update progress bar (simulate progress)
            progress_bar.progress(random.randint(0, 100))
        else:
            if "Crawling completed successfully." in log_content:
                status = "Crawling completed successfully."
                status_display.success(status)
            elif "Crawling has been stopped by the user." in log_content:
                status = "Crawling stopped by user."
                status_display.warning(status)
            else:
                status = "Idle"
                status_display.info(status)
            progress_bar.empty()

    with tabs[1]:
        st.header("📄 Downloaded PDFs")
        pdf_list = list_pdfs()
        if pdf_list:
            pdf_links = []
            for pdf in pdf_list:
                pdf_path = os.path.join(DOWNLOADED_PDFS_DIR, pdf)
                with open(pdf_path, "rb") as f:
                    pdf_data = f.read()
                b64 = base64.b64encode(pdf_data).decode()
                href = f'<a href="data:application/octet-stream;base64,{b64}" download="{pdf}">{pdf}</a>'
                pdf_links.append(href)
            pdf_html = "<br>".join(pdf_links)
            st.markdown(
                f"""
                <div class="chat-bubble">
                {pdf_html}
                </div>
                """,
                unsafe_allow_html=True
            )
            # Download all PDFs as ZIP
            zip_filename = create_zip_file()
            with open(zip_filename, "rb") as f:
                btn = st.download_button(
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
        st.markdown(
            f"""
            <div class="chat-bubble">
                <pre>{log_content}</pre>
            </div>
            """,
            unsafe_allow_html=True
        )

    # Footer
    st.markdown(
        """
        <div class="footer">
            Developed with ❤️ using Streamlit
        </div>
        """,
        unsafe_allow_html=True
    )

if __name__ == "__main__":
    main()
