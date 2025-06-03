import asyncio
import aiohttp
import random
import os
import traceback
from aiohttp import ClientSession
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
WEBSHARE_API_KEY = os.getenv("WEBSHARE_API_KEY")

AVAILABLE_FILE = "available.txt"
SEM = asyncio.Semaphore(30)
PROXIES = []

# Embedded pronounceable/brandable words (no 4Ls)
USERNAMES = [
    "lunar", "stellar", "zenith", "nova", "valor", "loyalty", "oracle", "spectra",
    "arcane", "neon", "sable", "ember", "cobalt", "velox", "mystic", "glimmer",
    "solace", "serene", "novaic", "lumen", "rift", "verge", "eclipse", "vanta",
    "nimbus", "kairo", "quanta", "aether", "onyx", "silica", "zento", "mira",
    "astra", "fable", "haven", "ethos", "tundra", "polar", "kinetic", "thrive",
    "spire", "civic", "noble", "omni", "kinra", "terra", "hexen", "flare",
    "sonar", "glyph", "strive", "eunoia", "aurora", "ethereal", "bravo",
    "osiris", "soluna", "calyx", "zephyr", "vortex", "lyric", "lyra", "venra",
    "liora", "aegis", "nevia", "verra", "tovia", "kalos", "lazur", "naeva", "xylon",
    "orion", "indra", "zenko", "pyxis", "siren", "echo", "halcyon", "nira", "lazra",
    "cypher", "aeris", "mystra", "novae", "umbra", "exalt", "fira", "astrae",
    "aurix", "luxor", "vynra", "zoria", "celix", "ravon", "ethra",
    "arwyn", "delyra", "xelos", "myla", "kaida", "soira", "talyn", "valen"
]

async def fetch_proxies():
    url = "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page=1&page_size=100"
    headers = {"Authorization": f"Token {WEBSHARE_API_KEY}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    raise Exception(f"Webshare responded with status {resp.status}")
                data = await resp.json()
                proxies = [
                    f"http://{proxy['username']}:{proxy['password']}@{proxy['proxy_address']}:{proxy['port']}"
                    for proxy in data['results']
                ]
                print(f"[INFO] Loaded {len(proxies)} proxies.")
                return proxies
    except Exception as e:
        print(f"[FATAL] Proxy fetch failed: {e}")
        traceback.print_exc()
        return []

async def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Telegram creds not set.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    print(f"[TELEGRAM ERROR] Status {resp.status}")
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")

async def is_available(username, proxy, session):
    url = f"https://kick.com/api/v2/channels/{username}"
    try:
        async with SEM:
            async with session.get(url, proxy=proxy, timeout=10) as response:
                return response.status == 404
    except Exception as e:
        print(f"[PROXY FAIL] {proxy} → {e}")
        return None

async def check_username(username, session):
    for _ in range(3):
        proxy = random.choice(PROXIES)
        available = await is_available(username, proxy, session)
        if available is True:
            print(f"[✅ AVAILABLE] {username}")
            with open(AVAILABLE_FILE, "a") as f:
                f.write(username + "\n")
            await send_telegram_message(f"✅ Available: `{username}`")
            return
        elif available is False:
            print(f"[❌ TAKEN] {username}")
            return
    print(f"[⚠️ SKIPPED] {username} (proxy errors)")

async def main():
    global PROXIES
    print("[BOOT] Verifying environment...")
    print(f"[ENV] Telegram token: {TELEGRAM_BOT_TOKEN[:10]}... | Chat ID: {TELEGRAM_CHAT_ID} | Webshare: {WEBSHARE_API_KEY[:10]}...")

    PROXIES = await fetch_proxies()
    if not PROXIES:
        print("[EXIT] No proxies available. Exiting.")
        return

    print(f"[START] Checking {len(USERNAMES)} usernames...\n")
    async with ClientSession() as session:
        await asyncio.gather(*(check_username(u, session) for u in USERNAMES))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print("[FATAL ERROR]:", str(e))
        traceback.print_exc()
