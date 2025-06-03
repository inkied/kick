import asyncio
import aiohttp
import os
import random
from aiohttp import ClientSession
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
WEBSHARE_API_KEY = os.getenv("WEBSHARE_API_KEY")

AVAILABLE_FILE = "available.txt"
SEM = asyncio.Semaphore(30)
PROXIES = []

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
    "aurix", "luxor", "vynra", "zoria", "celix", "ravon", "ethra", "arwyn",
    "delyra", "xelos", "myla", "kaida", "soira", "talyn", "valen"
]

async def fetch_proxies():
    url = "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page=1&page_size=100"
    headers = {"Authorization": f"Token {WEBSHARE_API_KEY}"}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers, timeout=15) as resp:
                data = await resp.json()
                return [
                    f"http://{p['username']}:{p['password']}@{p['proxy_address']}:{p['port']}"
                    for p in data.get("results", [])
                ]
        except Exception as e:
            print(f"[ERROR] Failed to fetch proxies: {e}")
            return []

async def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(url, json=payload, timeout=10)
        except Exception as e:
            print(f"[Telegram Error] {e}")

async def is_available(username, proxy, session):
    url = f"https://kick.com/api/v2/channels/{username}"
    try:
        async with SEM:
            async with session.get(url, proxy=proxy, timeout=10) as resp:
                return resp.status == 404
    except Exception:
        return None

async def check_username(username, session):
    for _ in range(3):
        proxy = random.choice(PROXIES)
        result = await is_available(username, proxy, session)
        if result is True:
            print(f"[AVAILABLE] {username}")
            with open(AVAILABLE_FILE, "a") as f:
                f.write(username + "\n")
            await send_telegram_message(f"✅ Available: `{username}`")
            return
        elif result is False:
            print(f"[TAKEN] {username}")
            return
    print(f"[SKIPPED] {username} (proxy issues)")

async def main():
    global PROXIES
    PROXIES = await fetch_proxies()
    if not PROXIES:
        print("[EXIT] No proxies available. Check Webshare key.")
        return
    async with ClientSession() as session:
        await asyncio.gather(*(check_username(u, session) for u in USERNAMES))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"[FATAL ERROR] {e}")
        import traceback
        traceback.print_exc()
