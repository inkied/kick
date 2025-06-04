import os
import asyncio
import aiohttp
import discord
import time
import random
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# ========== ENV VARS ==========
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))
WEBSHARE_KEY = os.getenv("WEBSHARE_API_KEY")
PROXY_USER = os.getenv("PROXY_USER")
PROXY_PASS = os.getenv("PROXY_PASS")
PROXY_HOST = os.getenv("PROXY_HOST")
PROXY_PORT = os.getenv("PROXY_PORT")

import discord
from discord.ext import commands

COMMAND_PREFIX = '.'

intents = discord.Intents.default()
intents.message_content = True  # ✅ Enable this line

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)

# ========== CONFIG ==========
MAX_ATTEMPTS = 4
CHECK_LIMIT = 100
PROXY_MIN = 10
PROXY_MAX = 50

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

    def update(self, response_time, success):
        self.total += 1
        self.last_used = time.time()
        self.response_time += response_time
        if success:
            self.hits += 1

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
                    f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{PROXY_PORT}"
                    for _ in data["results"]
                ]

    async def load(self):
        fresh = await self.fetch()
        async with self.lock:
            for p in fresh:
                if p not in [x.proxy_str for x in self.proxies]:
                    self.proxies.append(Proxy(p))

    async def get(self):
        async with self.lock:
            valid = sorted([p for p in self.proxies if p.health > 50], key=lambda x: x.avg_response)
            if not valid:
                await self.restock()
                valid = [p for p in self.proxies if p.health > 50]
            return random.choice(valid) if valid else None

    async def restock(self):
        print("[ProxyManager] Restocking proxies...")
        await self.load()
        self.proxies = self.proxies[-PROXY_MAX:]

proxy_manager = ProxyManager()
checked, available = 0, []

def generate_usernames(n):
    base = ["live", "chat", "play", "stream", "kick", "zone", "cult", "digi"]
    return [random.choice(base) + str(random.randint(1, 9999)) for _ in range(n)]

async def check_username(username):
    for attempt in range(MAX_ATTEMPTS):
        proxy_obj = await proxy_manager.get()
        if not proxy_obj:
            return False

        start = time.time()
        success = False
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://kick.com/{username}", proxy=proxy_obj.proxy_str, timeout=10) as r:
                    if r.status == 404:
                        success = True
        except:
            pass
        elapsed = time.time() - start
        proxy_obj.update(elapsed, success)
        if success:
            return True
    return False

async def run_checker(channel):
    global checked, available
    checked, available = 0, []
    usernames = generate_usernames(CHECK_LIMIT)
    for name in usernames:
        if await check_username(name):
            available.append(name)
            await channel.send(f"✅ Available: `{name}`")
        checked += 1
        await asyncio.sleep(0.3)

@bot.event
async def on_ready():
    print(f"[Discord] Bot connected as {bot.user}")
    await proxy_manager.load()

@bot.command(name="kickstart")
async def kickstart(ctx):
    await ctx.send("🚀 Starting Kick checker...")
    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    await run_checker(channel)

@bot.command(name="kickstatus")
async def kickstatus(ctx):
    text = "\n".join(
        f"{p.proxy_str[-10:]} | Health: {p.health:.1f}% | Avg RT: {p.avg_response:.2f}s"
        for p in proxy_manager.proxies[:5]
    )
    await ctx.send(
        f"✅ Checked: {checked}/{CHECK_LIMIT}\n"
        f"🎯 Hits: {len(available)}\n"
        f"🧠 Proxy Sample:\n{text}"
    )

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
