import asyncio, aiohttp, os, random, traceback
from aiohttp import ClientSession
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
WEBSHARE_KEY = os.getenv("WEBSHARE_API_KEY")
SEM = asyncio.Semaphore(20)
PROXIES = []

WORDS = [
    "zenith", "valor", "oracle", "spectra", "arcane", "ember", "velox",
    "serene", "rift", "eclipse", "nova", "glyph", "aegis", "mystic"
]

async def fetch_proxies():
    url = "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page=1&page_size=100"
    headers = {"Authorization": f"Token {WEBSHARE_KEY}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as res:
            data = await res.json()
            return [
                f"http://{p['username']}:{p['password']}@{p['proxy_address']}:{p['port']}"
                for p in data["results"]
            ]

async def send_msg(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    async with aiohttp.ClientSession() as session:
        await session.post(url, json=payload)

async def is_available(username, proxy, session):
    url = f"https://kick.com/api/v2/channels/{username}"
    try:
        async with SEM:
            async with session.get(url, proxy=proxy, timeout=10) as resp:
                return resp.status == 404
    except:
        return None

async def check_name(username, session):
    for _ in range(3):
        proxy = random.choice(PROXIES)
        ok = await is_available(username, proxy, session)
        if ok is True:
            print(f"[AVAILABLE] {username}")
            with open("available.txt", "a") as f:
                f.write(username + "\n")
            await send_msg(f"Available: `{username}`")
            return
        elif ok is False:
            print(f"[TAKEN] {username}")
            return
    print(f"[SKIPPED] {username}")

async def main():
    global PROXIES
    PROXIES = await fetch_proxies()
    if not PROXIES:
        print("Failed to fetch proxies.")
        return
    async with ClientSession() as session:
        await asyncio.gather(*(check_name(w, session) for w in WORDS))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        print("Fatal error:")
        traceback.print_exc()
