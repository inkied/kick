import asyncio
import aiohttp
import os

DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1373466041342099507/GMkMgWO6DXx6ULaDvCx1kfM5MzluC4v1DbKSBEyz5fp39-qCB2VN142Uj8ptiQM_re7"
PROXY_USER = "trdwseke-rotate"
PROXY_PASS = "n0vc7b0ev31y"
PROXY_HOST = "proxy.webshare.io"
PROXY_PORT = 80

WORDLIST_FILES = ["Brandable.txt", "Culture.txt", "Gaming.txt", "Mythology.txt", "Nature.txt", "Philosophy.txt", "Tech.txt"]
MAX_CONCURRENT_CHECKS = 20

proxy_auth = f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{PROXY_PORT}"

lock = asyncio.Lock()
proxy_index = 0

async def get_next_proxy():
    global proxy_index
    async with lock:
        proxy_index += 1
        return proxy_auth  # single rotating proxy endpoint

async def send_to_discord(message):
    payload = {"content": message}
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(DISCORD_WEBHOOK, json=payload)
        except Exception:
            pass

async def check_username(session, username):
    proxy = await get_next_proxy()
    url = f"https://kick.com/api/v1/channels/{username}"
    try:
        async with session.get(url, proxy=proxy, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 404:
                print(f"[AVAILABLE] {username}")
                await send_to_discord(f"`{username}` is available on Kick.com!")
            elif resp.status == 200:
                print(f"[TAKEN] {username}")
    except Exception:
        pass

async def load_wordlist(filename):
    usernames = []
    if os.path.exists(filename):
        with open(filename, "r") as f:
            usernames = [line.strip().lower() for line in f if line.strip()]
    return usernames

async def main():
    print("Checker Started")
    await send_to_discord("✅ Checker started")

    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit_per_host=MAX_CONCURRENT_CHECKS)) as session:
        for file in WORDLIST_FILES:
            usernames = await load_wordlist(file)
            total = len(usernames)
            checked_count = 0
            available_count = 0

            await send_to_discord(f"📝 Checking usernames from **{file}** ({total} usernames)")

            tasks = []
            for username in usernames:
                tasks.append(check_username(session, username))
                checked_count += 1

                if checked_count % 50 == 0:
                    await send_to_discord(f"⏳ Checked {checked_count}/{total} usernames in **{file}**")

                # To avoid spawning too many tasks at once, batch every 50 usernames
                if len(tasks) >= 50:
                    results = await asyncio.gather(*tasks)
                    tasks = []

            # Run remaining tasks if any
            if tasks:
                await asyncio.gather(*tasks)

            await send_to_discord(f"✅ Finished checking **{file}**")

    await send_to_discord("🎉 All wordlists checked. Script finished.")

if __name__ == "__main__":
    asyncio.run(main())
