import os
import asyncio
import aiohttp
import discord
import time
import random
from discord.ext import commands
from dotenv import load_dotenv

# ========== Load .env ==========
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))
WEBSHARE_KEY = os.getenv("WEBSHARE_API_KEY")
PROXY_USER = os.getenv("PROXY_USER")
PROXY_PASS = os.getenv("PROXY_PASS")
PROXY_HOST = os.getenv("PROXY_HOST")
PROXY_PORT = os.getenv("PROXY_PORT")

# ========== Bot Setup ==========
COMMAND_PREFIX = '.'
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)

# ========== Config ==========
CHECK_LIMIT = 100
MAX_ATTEMPTS = 2
PROXY_MIN_HEALTH = 30
DELAY_RANGE = (0.3, 0.5)

# ========== Proxy System ==========
class Proxy:
    def __init__(self, proxy_str):
        self.proxy_str = proxy_str
        self.hits = 0
        self.total = 0
        self.response_time = 0
        self.last_used = time.time()

    @property
    def health(self):
        return round((self.hits / self.total) * 100, 2) if self.total else 100

    @property
    def avg_response(self):
        return self.response_time / self.total if self.total else 0

    def update(self, rt, success):
        self.total += 1
        self.response_time += rt
        if success:
            self.hits += 1
        print(
            f"[Proxy Health] {self.proxy_str.split('@')[-1]} | "
            f"Health: {self.health:.1f}% | Hits: {self.hits} | Total: {self.total} | Avg RT: {self.avg_response:.2f}s"
        )

class ProxyManager:
    def __init__(self):
        self.proxies = []
        self.lock = asyncio.Lock()

    async def fetch(self):
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Token {WEBSHARE_KEY}"}
            async with session.get("https://proxy.webshare.io/api/v2/proxy/list/?mode=direct", headers=headers) as r:
                data = await r.json()
                return [
                    f"http://{PROXY_USER}:{PROXY_PASS}@{p['proxy_address']}:{p['port']}"
                    for p in data.get("results", [])
                ]

    async def load(self):
        raw_proxies = await self.fetch()
        async with self.lock:
            for p in raw_proxies:
                if p not in [x.proxy_str for x in self.proxies]:
                    self.proxies.append(Proxy(p))

    async def get(self):
        async with self.lock:
            healthy = [p for p in self.proxies if p.health >= PROXY_MIN_HEALTH]
            if not healthy:
                print("[ProxyManager] No healthy proxies. Reloading...")
                await self.load()
                healthy = [p for p in self.proxies if p.health >= PROXY_MIN_HEALTH]
            return random.choice(healthy) if healthy else None

proxy_manager = ProxyManager()

# ========== Username Logic ==========
def generate_usernames(n):
    base = ["kick", "live", "chat", "game", "play", "zone"]
    return [random.choice(base) + str(random.randint(100, 9999)) for _ in range(n)]

checked = 0
available = []
checker_running = False

async def check_username(username):
    proxy_obj = await proxy_manager.get()
    if not proxy_obj:
        return False

    success = False
    start = time.time()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://kick.com/{username}", proxy=proxy_obj.proxy_str, timeout=10) as r:
                if r.status == 404:
                    success = True
    except:
        pass
    elapsed = time.time() - start
    proxy_obj.update(elapsed, success)
    return success

async def run_checker(channel):
    global checked, available, checker_running
    checked = 0
    available = []
    checker_running = True

    usernames = generate_usernames(CHECK_LIMIT)
    for username in usernames:
        if not checker_running:
            break
        result = await check_username(username)
        checked += 1
        if result:
            available.append(username)
            await channel.send(f"✅ Available: `{username}`")
        await asyncio.sleep(random.uniform(*DELAY_RANGE))

    checker_running = False
    await channel.send("*✅ Checking Complete*")

# ========== Discord Events ==========
@bot.event
async def on_ready():
    print(f"[Discord] Bot online as {bot.user}")
    await proxy_manager.load()

@bot.command(name="kickstart")
async def kickstart(ctx):
    global checker_running
    if checker_running:
        await ctx.send("Checker is already running.")
        return
    await ctx.send("*🔍 Starting Kick Username Checker*")
    await run_checker(ctx.channel)

@bot.command(name="kickstop")
async def kickstop(ctx):
    global checker_running
    if not checker_running:
        await ctx.send("Checker is not running.")
        return
    checker_running = False
    await ctx.send("*🛑 Checker stopped.*")

@bot.command(name="kickstatus")
async def kickstatus(ctx):
    sample = proxy_manager.proxies[:5]
    stats = "\n".join(
        f"{p.proxy_str.split('@')[-1]} | Health: {p.health:.1f}% | Hits: {p.hits} | Total: {p.total} | Avg RT: {p.avg_response:.2f}s"
        for p in sample
    )
    await ctx.send(
        f"📊 Checked: {checked}/{CHECK_LIMIT}\n🎯 Hits: {len(available)}\n🔁 Proxies Sample:\n{stats}"
    )

# ========== Run ==========
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
