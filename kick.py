import asyncio
import aiohttp
import random
import os

WEBHOOK_URL = "YOUR_DISCORD_WEBHOOK_URL_HERE"
WEBSHARE_API_KEY = "YOUR_WEBSHARE_API_KEY_HERE"

PROXY_FETCH_URL = "https://proxy.webshare.io/api/proxy/list/"
PROXY_LIMIT = 100

MAX_RETRIES = 3
RATE_LIMIT_DELAY = 1.2  # seconds delay between requests per worker

async def fetch_proxies():
    headers = {"Authorization": f"Token {WEBSHARE_API_KEY}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(PROXY_FETCH_URL, headers=headers, params={"limit": PROXY_LIMIT, "type": "http"}) as resp:
            data = await resp.json()
            proxies = []
            for item in data.get("results", []):
                proxy = f"http://{item['username']}:{item['password']}@{item['proxy_address']}:{item['ports']['http']}"
                proxies.append(proxy)
            return proxies

async def send_discord_alert(username):
    content = f"✅ Available username: **{username}**"
    async with aiohttp.ClientSession() as session:
        await session.post(WEBHOOK_URL, json={"content": content})

async def check_username(session, username, proxy):
    url = f"https://kick.com/{username}"
    for attempt in range(MAX_RETRIES):
        try:
            async with session.get(url, proxy=proxy, timeout=10) as resp:
                if resp.status == 404:
                    await send_discord_alert(username)
                    return True
                elif resp.status == 200:
                    return False
                else:
                    # unexpected status, retry
                    await asyncio.sleep(1)
        except Exception:
            await asyncio.sleep(1)
    return False

async def worker(name, usernames, proxies):
    async with aiohttp.ClientSession() as session:
        for username in usernames:
            proxy = random.choice(proxies) if proxies else None
            available = await check_username(session, username, proxy)
            print(f"[{name}] Checked {username}: {'Available' if available else 'Taken or Error'}")
            await asyncio.sleep(RATE_LIMIT_DELAY)

async def main():
    wordlist_files = [
        "Brandable.txt", "Culture.txt", "Gaming.txt", 
        "Mythology.txt", "Nature.txt", "Philosophy.txt", "Tech.txt"
    ]
    all_usernames = []
    for filename in wordlist_files:
        if os.path.isfile(filename):
            print(f"Loading {filename}...")
            with open(filename, "r", encoding="utf-8") as f:
                all_usernames.extend([line.strip() for line in f if line.strip()])

    all_usernames = list(set(all_usernames))
    print(f"Total usernames loaded: {len(all_usernames)}")

    proxies = await fetch_proxies()
    print(f"Fetched {len(proxies)} proxies.")

    worker_count = 10
    chunk_size = len(all_usernames) // worker_count
    tasks = []
    for i in range(worker_count):
        chunk = all_usernames[i*chunk_size : (i+1)*chunk_size] if i < worker_count - 1 else all_usernames[i*chunk_size :]
        tasks.append(asyncio.create_task(worker(f"Worker-{i+1}", chunk, proxies)))

    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
