import requests
import threading
import time
import random
import queue
import os
from pathlib import Path

# === Config ===
TELEGRAM_BOT_TOKEN = "8110247076:AAH7D6YN0nWBQsAASHi8"
TELEGRAM_CHAT_ID = "7755395640"
WEBSHARE_API_KEY = "pialip63c4jeia0g8e8memjyj77ctky7ooq9b37q"

WORDLIST_DIR = "wordlists"
BATCH_SIZE = 200
NUM_THREADS = 25
PROXY_REFRESH_INTERVAL = 600  # 10 minutes
DELAY_BETWEEN_BATCHES = 15  # seconds
ALLOWED_CHARS = "abcdefghijklmnopqrstuvwxyz"
AVAILABLE_FILE = "available.txt"

username_queue = queue.Queue()
proxy_list = []
proxy_lock = threading.Lock()
running_event = threading.Event()

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")

def fetch_proxies():
    url = "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page=1&page_size=100"
    headers = {"Authorization": f"Token {WEBSHARE_API_KEY}"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        proxies = [
            f"http://{p['username']}:{p['password']}@{p['proxy_address']}:{p['port']}"
            for p in data.get("results", [])
        ]
        if proxies:
            print(f"[PROXY] Fetched {len(proxies)} proxies.")
        return proxies
    except Exception as e:
        print(f"[PROXY ERROR] {e}")
        return []

def proxy_refresher():
    global proxy_list
    while True:
        new_proxies = fetch_proxies()
        if new_proxies:
            with proxy_lock:
                proxy_list = new_proxies
        time.sleep(PROXY_REFRESH_INTERVAL)

def get_wordlists():
    return sorted(Path(WORDLIST_DIR).glob("*.txt"))

def load_batch_words(wordlist_path, batch_size):
    try:
        with open(wordlist_path, "r", encoding="utf-8") as f:
            lines = [line.strip().lower() for line in f]
            lines = [w for w in lines if w.isalpha() and all(c in ALLOWED_CHARS for c in w)]
            random.shuffle(lines)
            for i in range(0, len(lines), batch_size):
                yield lines[i:i+batch_size]
    except Exception as e:
        print(f"[WORDLIST ERROR] {wordlist_path.name} - {e}")
        return

def check_username(username):
    with proxy_lock:
        if not proxy_list:
            print("[WARN] No proxies available.")
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
            send_telegram_message(f"✅ Available: `{username}`")
        elif response.status_code == 200:
            print(f"[TAKEN] {username}")
        else:
            print(f"[SKIP] {username} - Status {response.status_code}")
    except Exception as e:
        print(f"[PROXY FAIL] {username} - {e}")

def worker():
    while True:
        username = username_queue.get()
        if username is None:
            break
        check_username(username)
        username_queue.task_done()

def start_checking():
    threads = []
    for _ in range(NUM_THREADS):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        threads.append(t)

    wordlists = get_wordlists()
    print(f"[INFO] Found {len(wordlists)} wordlists.")

    for wordlist_path in wordlists:
        if not running_event.is_set():
            break
        print(f"\n[THEME] Checking: {wordlist_path.name}")
        for batch in load_batch_words(wordlist_path, BATCH_SIZE):
            if not running_event.is_set():
                break
            for username in batch:
                username_queue.put(username)
            username_queue.join()
            print(f"[BATCH COMPLETE] Sleeping {DELAY_BETWEEN_BATCHES}s...\n")
            time.sleep(DELAY_BETWEEN_BATCHES)

    for _ in threads:
        username_queue.put(None)
    for t in threads:
        t.join()

def telegram_command_listener():
    offset = None
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            if offset:
                url += f"?offset={offset}"
            res = requests.get(url, timeout=30)
            updates = res.json().get("result", [])

            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message", {})
                chat_id = str(message.get("chat", {}).get("id"))
                text = message.get("text", "").strip().lower()

                if chat_id != TELEGRAM_CHAT_ID:
                    continue

                if text == "/start":
                    if not running_event.is_set():
                        running_event.set()
                        send_telegram_message("▶️ Started checking Kick usernames.")
                        threading.Thread(target=start_checking, daemon=True).start()
                elif text == "/stop":
                    if running_event.is_set():
                        running_event.clear()
                        send_telegram_message("⏹️ Stopping after current batch finishes.")
        except Exception as e:
            print(f"[TELEGRAM LISTENER ERROR] {e}")
        time.sleep(3)

def main():
    print("[BOT] Kick username checker running. Send /start to begin.")
    threading.Thread(target=proxy_refresher, daemon=True).start()
    telegram_command_listener()

if __name__ == "__main__":
    main()
