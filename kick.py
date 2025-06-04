import requests
import random
import threading
import time
import queue
import os
from glob import glob
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext

# === CONFIG ===
TELEGRAM_BOT_TOKEN = "8110247076:AAH7D6YN0nWBQsAASHi8"
TELEGRAM_CHAT_ID = "7755395640"
WEBSHARE_API_KEY = "pialip63c4jeia0g8e8memjyj77ctky7ooq9b37q"

AVAILABLE_FILE = "available.txt"
NUM_THREADS = 30
PROXY_REFRESH_INTERVAL = 600  # seconds (10 minutes)
CHECK_PAUSE = 0.1  # seconds pause between checks for proxy longevity
WORDLIST_FOLDER = "wordlists"

# Allowed chars for Kick usernames (letters only)
ALLOWED_CHARS = set("abcdefghijklmnopqrstuvwxyz")

# Thread-safe queue for usernames
username_queue = queue.Queue()

# Proxy list and lock
proxy_list = []
proxy_lock = threading.Lock()

# Control flags
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
    try:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, parse_mode="Markdown")
    except Exception as e:
        print(f"[ERROR] Telegram send failed: {e}")


def load_wordlists():
    word_files = glob(os.path.join(WORDLIST_FOLDER, "*.txt"))
    if not word_files:
        print(f"[ERROR] No wordlist .txt files found in '{WORDLIST_FOLDER}' folder!")
        return []
    all_words = []
    for wf in word_files:
        try:
            with open(wf, "r", encoding="utf-8") as f:
                for line in f:
                    w = line.strip().lower()
                    if 3 <= len(w) <= 15 and set(w) <= ALLOWED_CHARS:
                        all_words.append(w)
        except Exception as e:
            print(f"[ERROR] Failed reading {wf}: {e}")
    if not all_words:
        print("[WARN] Wordlists loaded but no valid words found.")
    else:
        print(f"[INFO] Loaded {len(all_words)} usernames from wordlists.")
    return all_words


def username_generator(words):
    # Cycle through words infinitely
    while True:
        random.shuffle(words)
        for word in words:
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
        print(f"[ERROR] {username} proxy or request failed: {e}")


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


def start_checking(update: Update, context: CallbackContext):
    global running
    with running_lock:
        if running:
            update.message.reply_text("Already running!")
            return
        running = True

    update.message.reply_text("Started username checking!")

    # Load usernames
    words = load_wordlists()
    if not words:
        update.message.reply_text("No valid wordlists found. Cannot start.")
        with running_lock:
            running = False
        return

    def enqueue_usernames():
        gen = username_generator(words)
        while True:
            with running_lock:
                if not running:
                    break
            username_queue.put(next(gen))

    threading.Thread(target=enqueue_usernames, daemon=True).start()


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
    # Start proxy refresher thread
    threading.Thread(target=proxy_refresher, daemon=True).start()

    # Start worker threads
    threads = []
    for _ in range(NUM_THREADS):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        threads.append(t)

    # Setup Telegram bot handlers
    updater = Updater(token=TELEGRAM_BOT_TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start_checking))
    dispatcher.add_handler(CommandHandler("stop", stop_checking))

    print("[INFO] Bot started. Waiting for /start command...")
    updater.start_polling()
    updater.idle()

    # On shutdown: stop workers
    for _ in range(NUM_THREADS):
        username_queue.put(None)
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
