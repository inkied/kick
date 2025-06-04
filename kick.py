import os
import aiohttp
import asyncio
import discord
import random
import time
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))
PROXIES_CHANNEL_ID = int(os.getenv("PROXIES_CHANNEL_ID"))
WEBSHARE_KEY = os.getenv("WEBSHARE_API_KEY")
PROXY_USER = os.getenv("PROXY_USER")
PROXY_PASS = os.getenv("PROXY_PASS")

COMMAND_PREFIX = '.'
MAX_ATTEMPTS = 3
CHECK_LIMIT = 100
PROXY_MAX = 50
HEALTH_THRESHOLD = 30
CONCURRENCY_LIMIT = 10

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)

class Proxy:
    def __init__(self, proxy_str):
        self.proxy_str = proxy_str
        self.hits = 0
        self.total = 0
        self.response_time = 0.0
        self.last_used = 0

    @property
    def health(self):
        return round((self.hits / self.total) * 100, 2) if self.total else 100

    @property
    def avg_rt(self):
        return round(self.response_time / self.total, 2) if self.total else 0.0

    def update(self, rt, success):
        self.total += 1
        self.response_time += rt
        if success:
            self.hits += 1
        self.last_used = time.time()

    def is_healthy(self):
        return self.health >= HEALTH_THRESHOLD

class ProxyManager:
    def __init__(self):
        self.proxies = []
        self.lock = asyncio.Lock()

    async def fetch(self):
        headers = {"Authorization": f"Token {WEBSHARE_KEY}"}
        url = "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as r:
                data = await r.json()
                return [
                    f"http://{PROXY_USER}:{PROXY_PASS}@{p['proxy_address']}:{p['port']}"
                    for p in data.get("results", [])
                ]

    async def load(self):
        fresh = await self.fetch()
        async with self.lock:
            for p in fresh:
                if p not in [x.proxy_str for x in self.proxies]:
                    self.proxies.append(Proxy(p))
            self.proxies = self.proxies[-PROXY_MAX:]

    async def get(self):
        async with self.lock:
            healthy = [p for p in self.proxies if p.is_healthy()]
            if not healthy:
                await self.load()
                healthy = [p for p in self.proxies if p.is_healthy()]
            return random.choice(healthy) if healthy else None

    def sample_health(self, limit=5):
        return sorted(self.proxies, key=lambda p: -p.health)[:limit]

proxy_manager = ProxyManager()
checked = 0
available = []
checker_running = False
semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

def generate_usernames(n):
    base = ["live", "chat", "kick", "play", "stream", "cult", "digi", "zero", "core", "cube"]
    return [random.choice(base) + str(random.randint(100, 9999)) for _ in range(n)]

async def check_username(username, channel):
    global checked
    for _ in range(MAX_ATTEMPTS):
        proxy_obj = await proxy_manager.get()
        if not proxy_obj:
            await asyncio.sleep(2)
            continue
        start = time.time()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://kick.com/{username}", proxy=proxy_obj.proxy_str, timeout=10) as r:
                    elapsed = time.time() - start
                    found = (r.status == 404)
                    proxy_obj.update(elapsed, found)
                    if found:
                        available.append(username)
                        await channel.send(f"✅ Available: `{username}`")
                        break
        except:
            proxy_obj.update(0.5, False)
        await asyncio.sleep(0.1)
    checked += 1

async def run_checker(channel, proxy_log_channel):
    global checked, available, checker_running
    checked, available = 0, []
    checker_running = True
    await proxy_manager.load()

    usernames = generate_usernames(CHECK_LIMIT)
    tasks = []

    for name in usernames:
        if not checker_running:
            break
        await semaphore.acquire()
        task = asyncio.create_task(worker(name, channel))
        task.add_done_callback(lambda t: semaphore.release())
        tasks.append(task)

    await asyncio.gather(*tasks)
    checker_running = False
    await channel.send("*✅ Username check complete!*")

    # Log sample proxy health
    sample = proxy_manager.sample_health()
    log = "\n".join(
        f"{p.proxy_str.split('@')[-1]} | Health: {p.health:.1f}% | RT: {p.avg_rt}s"
        for p in sample
    )
    await proxy_log_channel.send(f"🧠 Proxy Stats:\n```\n{log}\n```")

async def worker(username, channel):
    try:
        await check_username(username, channel)
    except:
        pass

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    await proxy_manager.load()

@bot.command(name="kickstart")
async def kickstart(ctx):
    global checker_running
    if checker_running:
        await ctx.send("Already running.")
        return
    await ctx.send("🟢 Starting Kick checker...")
    main_channel = bot.get_channel(DISCORD_CHANNEL_ID)
    proxy_log_channel = bot.get_channel(PROXIES_CHANNEL_ID)
    await run_checker(main_channel or ctx.channel, proxy_log_channel or ctx.channel)

@bot.command(name="kickstop")
async def kickstop(ctx):
    global checker_running
    checker_running = False
    await ctx.send("🛑 Checker stopped.")

@bot.command(name="kickstatus")
async def kickstatus(ctx):
    text = f"✅ Checked: {checked}/{CHECK_LIMIT}\n🎯 Hits: {len(available)}"
    await ctx.send(text)

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
