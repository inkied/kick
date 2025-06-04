import os
import requests
import random
import threading
import time
import queue
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext

# Load env variables (set these on Railway)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
WEBSHARE_API_KEY = os.getenv("WEBSHARE_API_KEY")

WORDLIST_FILE = "wordlist.txt"
AVAILABLE_FILE = "available.txt"
NUM_THREADS = 20
PROXY_REFRESH_INTERVAL = 600  # 10 minutes
CHECK_PAUSE = 0.1

username_queue = queue.Queue()
proxy_list = []
proxy_lock = threading.Lock()
running = False
running_lock = threading.Lock()

bot = Bot(token=TELEGRAM_BOT_TOKEN)

def fetch_proxies():
    url = "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page=1&page_size=100"
    headers = {"Authorization": f"Token {WEBSHARE_API_KEY}"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        proxies = [
            f"http://{p['username']}:{p['password']}@{p['proxy_address']}:{p['port']}"
            for p in data.get('results', [])
        ]
        print(f"[INFO] Fetched {len(proxies)} proxies")
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
        time.sleep(PROXY_REFRESH_INTERVAL)

def send_telegram_message(text):
    try:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text)
    except Exception as e:
        print(f"[ERROR] Telegram send failed: {e}")

def load_wordlist():
    if not os.path.exists(WORDLIST_FILE):
        print(f"[ERROR] Wordlist file '{WORDLIST_FILE}' not found.")
        return []
    with open(WORDLIST_FILE, "r") as f:
        words = [w.strip().lower() for w in f if 3 <= len(w.strip()) <= 15]
    print(f"[INFO] Loaded {len(words)} usernames from wordlist")
    return words

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
            send_telegram_message(f"Available: {username}")
        else:
            print(f"[TAKEN] {username}")
    except Exception as e:
        print(f"[ERROR] {username} request failed: {e}")

def worker():
    while True:
        username = username_queue.get()
        if username is None:
            break
        with running_lock:
            if not running:
                username_queue.task_done()
                break
        check_username(username)
        time.sleep(CHECK_PAUSE)
        username_queue.task_done()

def enqueue_usernames(words):
    checked = set()
    # Load already checked usernames so we skip them
    if os.path.exists(AVAILABLE_FILE):
        with open(AVAILABLE_FILE, "r") as f:
            for line in f:
                checked.add(line.strip().lower())
    for username in words:
        if username not in checked:
            username_queue.put(username)

def start_checking(update: Update, context: CallbackContext):
    global running
    with running_lock:
        if running:
            update.message.reply_text("Already running!")
            return
        running = True
    update.message.reply_text("Starting username checking...")
    words = load_wordlist()
    if not words:
        update.message.reply_text("No usernames found to check!")
        with running_lock:
            running = False
        return
    threading.Thread(target=enqueue_usernames, args=(words,), daemon=True).start()

def stop_checking(update: Update, context: CallbackContext):
    global running
    with running_lock:
        if not running:
            update.message.reply_text("Not running!")
            return
        running = False
    update.message.reply_text("Stopping username checking...")
    # Clear queue
    while not username_queue.empty():
        try:
            username_queue.get_nowait()
            username_queue.task_done()
        except queue.Empty:
            break

def main():
    threading.Thread(target=proxy_refresher, daemon=True).start()
    threads = []
    for _ in range(NUM_THREADS):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        threads.append(t)

    updater = Updater(token=TELEGRAM_BOT_TOKEN, use_context=True)
    dispatcher = updater.dispatcher
    dispatcher.add_handler(CommandHandler("start", start_checking))
    dispatcher.add_handler(CommandHandler("stop", stop_checking))

    print("[INFO] Bot started. Use /start to begin.")
    updater.start_polling()
    updater.idle()

    for _ in range(NUM_THREADS):
        username_queue.put(None)
    for t in threads:
        t.join()

if __name__ == "__main__":
    main()
