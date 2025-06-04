import aiohttp
import asyncio
import random
import time
import os

WEBHOOK_URL = "your_discord_webhook_url_here"
WEBSHARE_API_KEY = "your_webshare_api_key_here"
WORDLIST_FILES = [
    "Brandable.txt",
    "Culture.txt",
    "Gaming.txt",
    "Mythology.txt",
    "Nature.txt",
    "Philosophy.txt",
    "Tech.txt"
]

CHECK_DELAY = 1.2  # seconds delay between username checks
PROXY_FETCH_COUNT = 100

# Load usernames from all wordlists
def load_usernames():
    usernames = []
    for file in WORDLIST_FILES:
        if os.path.isfile(file):
            with open(file, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
                usernames.extend(lines)
    return list(set(usernames))  # deduplicate

# Fetch proxies from Webshare
async def fetch_proxies():
    url = f"https://proxy.webshare.io/api/proxy/list/?page_size={PROXY_FETCH_COUNT}"
    headers = {"Authorization": f"Token {WEBSHARE_API_KEY}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            data = await resp.json()
            proxies = []
            for item in data.get("results", []):
                ip = item.get("proxy_address")
                port = item.get("proxy_port")
                user = item.get("proxy_username")
                password = item.get("proxy_password")
                proxy_str = f"http://{user}:{password}@{ip}:{port}"
                proxies.append(proxy_str)
            return proxies

# Send message to Discord webhook
async def send_discord_message(content):
    async with aiohttp.ClientSession() as session:
        json = {"content": content}
        async with session.post(WEBHOOK_URL, json=json) as resp:
            if resp.status != 204:
                print(f"Failed to send webhook message: {resp.status}")

# Dummy username availability check (replace with actual API call)
async def check_username(username, proxy):
    # Simulate a request with proxy here, real logic depends on target API
    await asyncio.sleep(0.1)  # simulate network delay
    # Randomly simulate availability
    available = random.choice([True, False, False])
    return available

async def worker(queue, proxies):
    while not queue.empty():
        username = await queue.get()
        proxy = random.choice(proxies) if proxies else None
        try:
            available = await check_username(username, proxy)
            if available:
                await send_discord_message(f"Username available: {username}")
                print(f"Available: {username}")
            else:
                print(f"Taken: {username}")
            await asyncio.sleep(CHECK_DELAY)
        except Exception as e:
            print(f"Error checking {username}: {e}")
        queue.task_done()

async def main():
    await send_discord_message("Checker Started")

    usernames = load_usernames()
    print(f"Loaded {len(usernames)} usernames.")

    proxies = await fetch_proxies()
    print(f"Fetched {len(proxies)} proxies.")

    queue = asyncio.Queue()
    for username in usernames:
        queue.put_nowait(username)

    # Run workers (threads)
    workers = []
    for _ in range(20):  # number of concurrent workers, tweak as needed
        workers.append(asyncio.create_task(worker(queue, proxies)))

    await queue.join()

    for w in workers:
        w.cancel()

if __name__ == "__main__":
    asyncio.run(main())
