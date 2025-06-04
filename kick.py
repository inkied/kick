import requests
import random
import threading
import time
import queue
import os

# === Config ===
TELEGRAM_BOT_TOKEN = "8110247076:AAH7D6YN0nWBQsAASHi8"
TELEGRAM_CHAT_ID = "7755395640"
WEBSHARE_API_KEY = "pialip63c4jeia0g8e8memjyj77ctky7ooq9b37q"

AVAILABLE_FILE = "available.txt"
NUM_THREADS = 30
PROXY_REFRESH_INTERVAL = 600  # 10 minutes

# Allowed chars for Kick usernames (letters only)
ALLOWED_CHARS = "abcdefghijklmnopqrstuvwxyz"
MIN_LEN = 4
MAX_LEN = 15

# Wordlist folder and files (make sure these files exist in this folder)
WORDLIST_FOLDER = "./wordlists"
wordlist_files = [
    "Tech.txt",
    "Philosophy.txt",
    "Gaming.txt",
    "Culture.txt",
    "Nature.txt",
    "Mythology.txt",
    "Brandable.txt"
]

# Thread-safe queue for usernames to check
username_queue = queue.Queue()

# Shared proxy list and lock
proxy_list = []
proxy_lock = threading.Lock()

def fetch_proxies():
    url = "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page=1&page_size=100"
    headers = {"Authorization": f"Token {WEBSHARE_API_KEY}"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        proxies = [
            f"http://{p['username']}:{p['password']}@{p['proxy_address']}:{p['port']}"
            for p in data.get('results', [])
        ]
        if proxies:
            print(f"[INFO] Fetched {len(proxies)} proxies.")
        else:
            print("[WARN] Proxy fetch returned empty.")
        return proxies
    except Exception as e:
        print(f"[ERROR] Proxy fetch failed: {e}")
        return []

def proxy_refresher():
    global proxy_list
    while True:
        new_proxies = fetch_proxies()
        if new_proxies:
            with proxy_lock:
                proxy_list = new_proxies
        else:
            print("[WARN] Keeping previous proxies due to empty fetch.")
        time.sleep(PROXY_REFRESH_INTERVAL)

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[ERROR] Telegram send failed: {e}")

def generate_usernames():
    all_words = set()
    for filename in wordlist_files:
        path = os.path.join(WORDLIST_FOLDER, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    word = line.strip().lower()
                    if MIN_LEN <= len(word) <= MAX_LEN and all(c in ALLOWED_CHARS for c in word):
                        all_words.add(word)
        except FileNotFoundError:
            print(f"[WARN] {filename} not found in {WORDLIST_FOLDER}, skipping.")
        except Exception as e:
            print(f"[ERROR] Failed reading {filename}: {e}")

    for word in all_words:
        yield word

def check_username(username):
    with proxy_lock:
        if not proxy_list:
            print("[WARN] No proxies available, skipping check.")
            return
        proxy = random.choice(proxy_list)

    proxies = {"http": proxy, "https": proxy}
    url = f"https://kick.com/api/v2/channels/{username}"

    try:
        response = requests.get(url, proxies=proxies, timeout=10)
        if response.status_code == 404:
            print(f"[AVAILABLE] {username}")
            with open(AVAILABLE_FILE, "a") as f:
                f.write(username + "\n")
            send_telegram_message(f"Available: `{username}`")
        elif response.status_code == 200:
            print(f"[TAKEN] {username}")
        else:
            print(f"[SKIP] {username} - Status {response.status_code}")
    except Exception as e:
        print(f"[ERROR] {username} proxy error or request failed: {e}")

def worker():
    while True:
        username = username_queue.get()
        if username is None:
            break
        check_username(username)
        username_queue.task_done()

def main():
    # Start proxy refresher thread
    threading.Thread(target=proxy_refresher, daemon=True).start()

    # Start worker threads
    threads = []
    for _ in range(NUM_THREADS):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        threads.append(t)

    # Fill the queue with usernames from all wordlists
    for uname in generate_usernames():
        username_queue.put(uname)

    # Wait for all usernames to be processed
    username_queue.join()

    # Stop workers
    for _ in range(NUM_THREADS):
        username_queue.put(None)
    for t in threads:
        t.join()

if __name__ == "__main__":
    main()