import os
import asyncio
import aiohttp
import discord
import time
import random
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))
WEBSHARE_KEY = os.getenv("WEBSHARE_API_KEY")
PROXY_USER = os.getenv("PROXY_USER")
PROXY_PASS = os.getenv("PROXY_PASS")

COMMAND_PREFIX = '.'
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)

# ========= CONFIG =========
MAX_ATTEMPTS = 3
CHECK_LIMIT = 100
MAX_PROXIES = 50
CONCURRENT_CHECKS = 15
PROXY_HEALTH_THRESHOLD = 50
MAX_PROXY_RESPONSE = 5
RESTOCK_INTERVAL = 300  # every 5 minutes
BAD_HEALTH_THRESHOLD = 25
FAILURE_BACKOFF = [0.2, 0.5, 1.0]

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
        if response_time:
            self.response_time += response_time
        if success:
            self.hits += 1
        print(
            f"[Proxy] {self.proxy_str.split('@')[-1]} | "
            f"Health: {self.health:.1f}% | Hits: {self.hits} | Total: {self.total} | AvgRT: {self.avg_response:.2f}s"
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
        fresh = await self.fetch()
        async with self.lock:
            for p in fresh:
                if p not in [x.proxy_str for x in self.proxies]:
                    self.proxies.append(Proxy(p))
            self.cleanup()
            self.proxies = self.proxies[-MAX_PROXIES:]

    def cleanup(self):
        self.proxies = [
            p for p in self.proxies
            if p.health >= BAD_HEALTH_THRESHOLD and p.avg_response < MAX_PROXY_RESPONSE
        ]

    async def get(self):
        async with self.lock:
            valid = sorted(
                [p for p in self.proxies if p.health >= PROXY_HEALTH_THRESHOLD],
                key=lambda x: x.avg_response
            )
            if not valid:
                await self.load()
                valid = [p for p in self.proxies if p.health >= PROXY_HEALTH_THRESHOLD]
            return random.choice(valid) if valid else None

proxy_manager = ProxyManager()
checked, available = 0, []
checker_running = False
semaphore = asyncio.Semaphore(CONCURRENT_CHECKS)
failures = 0

def generate_usernames(n):
    base = ["live", "chat", "play", "kick", "zone", "team"]
    return [random.choice(base) + str(random.randint(100, 9999)) for _ in range(n)]

async def check_username(username):
    global failures
    async with semaphore:
        for attempt in range(MAX_ATTEMPTS):
            proxy_obj = await proxy_manager.get()
            if not proxy_obj:
                return False
            start = time.time()
            success = False
            try:
                timeout = aiohttp.ClientTimeout(total=8)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(f"https://kick.com/{username}", proxy=proxy_obj.proxy_str) as r:
                        if r.status == 404:
                            success = True
            except Exception:
                pass

            elapsed = time.time() - start
            proxy_obj.update(elapsed, success)

            if success:
                failures = 0
                return True

            await asyncio.sleep(FAILURE_BACKOFF[min(attempt, len(FAILURE_BACKOFF)-1)])

        failures += 1
        if failures > 20:
            print("[Backoff] Too many fails. Sleeping for 5 seconds.")
            await asyncio.sleep(5)
        return False

async def run_checker(channel):
    global checked, available, checker_running
    checked = 0
    available = []
    failures = 0
    checker_running = True

    usernames = generate_usernames(CHECK_LIMIT)
    tasks = []

    for name in usernames:
        if not checker_running:
            break
        task = asyncio.create_task(check_username(name))
        tasks.append((name, task))

    for name, task in tasks:
        if not checker_running:
            break
        result = await task
        if result:
            available.append(name)
            await channel.send(f"✅ `{name}`")
        checked += 1

    checker_running = False
    await channel.send("*✅ Done checking Kick usernames*")

@bot.event
async def on_ready():
    print(f"[Discord] Logged in as {bot.user}")
    await proxy_manager.load()
    restock_loop.start()

@tasks.loop(seconds=RESTOCK_INTERVAL)
async def restock_loop():
    print("[Auto Restock] Reloading proxy list.")
    await proxy_manager.load()

@bot.command(name="kickstart")
async def kickstart(ctx):
    global checker_running
    if checker_running:
        await ctx.send("Checker already running.")
        return
    await ctx.send("*Checking Kick users...*")
    channel = bot.get_channel(DISCORD_CHANNEL_ID) or ctx.channel
    await run_checker(channel)

@bot.command(name="kickstop")
async def kickstop(ctx):
    global checker_running
    checker_running = False
    await ctx.send("*Checker stopped.*")

@bot.command(name="kickstatus")
async def kickstatus(ctx):
    sample = proxy_manager.proxies[:5]
    stats = "\n".join(
        f"{p.proxy_str.split('@')[-1]} | Health: {p.health:.1f}% | Hits: {p.hits} | Total: {p.total} | RT: {p.avg_response:.2f}s"
        for p in sample
    )
    await ctx.send(
        f"✅ Checked: {checked}/{CHECK_LIMIT}\n"
        f"🎯 Hits: {len(available)}\n"
        f"🧠 Proxies:\n{stats}"
    )

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
