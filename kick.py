import asyncio
import aiohttp
import os
import random
import time

DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1373466041342099507/GMkMgWO6DXx6ULaDvCxq1kfM5MzluC4v1DbKSBEyz5fp39-qCB2VN142Uj8ptiQM_re7"
WEBSHARE_API_KEY = "fedc729e117b46eaccc7ebb6a2a04e4337e7a3a5a02b30bf5fb1bde2354902f0"
WORDLIST_FILES = ["Brandable.txt", "Culture.txt", "Gaming.txt", "Mythology.txt", "Nature.txt", "Philosophy.txt", "Tech.txt"]
MAX_CONCURRENT_CHECKS = 20

proxies = []
proxy_index = 0
lock = asyncio.Lock()

async def fetch_proxies():
    url = "https://proxy.webshare.io/api/v2/proxy/list/"
    headers = {"Authorization": f"Token {WEBSHARE_API_KEY}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            data = await resp.json()
            return [f"http://{proxy['username']}:{proxy['password']}@{proxy['ip']}:{proxy['port']}" for proxy in data['results'][:100]]

async def get_next_proxy():
    global proxy_index
    async with lock:
        proxy = proxies[proxy_index % len(proxies)]
        proxy_index += 1
        return proxy

async def check_username(session, username):
    proxy = await get_next_proxy()
    try:
        async with session.get(f"https://kick.com/api/v1/channels/{username}", proxy=proxy, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 404:
                print(f"[AVAILABLE] {username}")
                await send_to_discord(username)
            elif resp.status == 200:
                print(f"[TAKEN] {username}")
    except Exception:
        pass  # Skip failures silently

async def send_to_discord(username):
    await asyncio.sleep(5)  # Delay to prevent spam
    payload = {"content": f"`{username}` is available on Kick.com!"}
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(DISCORD_WEBHOOK, json=payload)
        except Exception:
            pass

async def load_wordlist():
    usernames = set()
    for file in WORDLIST_FILES:
        if os.path.exists(file):
            with open(file, "r") as f:
                usernames.update(line.strip().lower() for line in f if line.strip())
    return list(usernames)

async def main():
    global proxies
    print("Checker Started")
    proxies = await fetch_proxies()
    wordlist = await load_wordlist()

    connector = aiohttp.TCPConnector(limit_per_host=MAX_CONCURRENT_CHECKS)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [check_username(session, username) for username in wordlist]
        await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
