import os
import asyncio
import aiohttp
import time
import random
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# ====== ENV CONFIG ======
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))
WEBSHARE_API = os.getenv("WEBSHARE_API_KEY")
PROXY_USER = os.getenv("PROXY_USER")
PROXY_PASS = os.getenv("PROXY_PASS")
PROXY_HOST = os.getenv("PROXY_HOST")
PROXY_PORT = os.getenv("PROXY_PORT")

# ====== BOT SETUP ======
intents = commands.Intents.default()
bot = commands.Bot(command_prefix=".", intents=intents)

# ====== GLOBALS ======
users_checked = 0
hits = []
stop_flag = False
proxy_lock = asyncio.Lock()
proxy_pool = []
max_proxies = 50
min_proxies = 10

class Proxy:
    def __init__(self, proxy_str):
        self.proxy_str = proxy_str
        self.successes = 0
        self.failures = 0
        self.total_response_time = 0
        self.first_seen = time.time()

    def health(self):
        total = self.successes + self.failures
        if total == 0: return 100.0
        return round((self.successes / total) * 100, 2)

    def avg_time(self):
        total = self.successes + self.failures
        return self.total_response_time / total if total else 0

    def uptime(self):
        return time.time() - self.first_seen

async def fetch_proxies():
    headers = {"Authorization": f"Token {WEBSHARE_API}"}
    async with aiohttp.ClientSession() as session:
        async with session.get("https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page_size=100", headers=headers) as r:
            data = await r.json()
            return [f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{PROXY_PORT}" for _ in data['results']]

async def restock_proxies():
    global proxy_pool
    fresh = await fetch_proxies()
    async with proxy_lock:
        for p in fresh:
            if not any(p == proxy.proxy_str for proxy in proxy_pool):
                proxy_pool.append(Proxy(p))
        proxy_pool = proxy_pool[:max_proxies]

def select_proxy():
    sorted_pool = sorted(proxy_pool, key=lambda p: (-p.health(), p.avg_time()))
    return sorted_pool[0] if sorted_pool else None

async def update_proxy(proxy_obj, response_time, success):
    proxy_obj.total_response_time += response_time
    if success:
        proxy_obj.successes += 1
    else:
        proxy_obj.failures += 1

async def check_username(username):
    global users_checked
    tries = 0
    while tries < 5:
        tries += 1
        async with proxy_lock:
            proxy = select_proxy()
        if not proxy:
            await restock_proxies()
            continue

        proxy_url = proxy.proxy_str
        start = time.time()
        success = False
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://kick.com/{username}", proxy=proxy_url, timeout=10) as resp:
                    if resp.status == 404:
                        success = True
        except: pass
        duration = time.time() - start
        await update_proxy(proxy, duration, success)

        if success:
            hits.append({
                "username": username,
                "timestamp": time.strftime('%Y-%m-%d %H:%M:%S'),
                "tries": tries
            })
            break
    users_checked += 1

async def generate_usernames(n):
    base = ["gamer", "kick", "cast", "vibe", "rage", "arcade", "tech", "titan"]
    return [f"{random.choice(base)}{random.randint(1,999)}" for _ in range(n)]

async def run_checker(ctx, limit=100):
    global users_checked, stop_flag
    stop_flag = False
    users_checked = 0
    hits.clear()

    usernames = await generate_usernames(limit)
    for username in usernames:
        if stop_flag: break
        await check_username(username)
        await asyncio.sleep(0.2)

    await ctx.send(f"✅ Done! {users_checked} checked, {len(hits)} available.")

# ====== COMMANDS ======
@bot.event
async def on_ready():
    await restock_proxies()
    print(f"[Bot] Logged in as {bot.user}")

@bot.command()
async def kickstart(ctx):
    await ctx.send("🚀 Starting Kick username checker...")
    asyncio.create_task(run_checker(ctx))

@bot.command()
async def kickstop(ctx):
    global stop_flag
    stop_flag = True
    await ctx.send("🛑 Checker stopped. It will resume from next username if restarted.")

@bot.command()
async def kickstatus(ctx):
    health_stats = ""
    async with proxy_lock:
        for proxy in proxy_pool[:5]:
            health_stats += f"{proxy.proxy_str[:30]}... | {proxy.health()}% | {proxy.avg_time():.2f}s | {int(proxy.uptime())}s\n"

    recent_hits = "\n".join(
        f"• {h['username']} | {h['timestamp']} | Tries: {h['tries']}" for h in hits[-5:]
    ) or "No hits yet."

    await ctx.send(
        f"📊 **Status**:\n"
        f"Checked: {users_checked} | Hits: {len(hits)}\n"
        f"🧠 Recent Hits:\n{recent_hits}\n"
        f"⚙️ Proxy Health:\n{health_stats}"
    )

# ====== START BOT ======
if __name__ == "__main__":
    bot.run(TOKEN)
