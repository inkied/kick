import os
import asyncio
import aiohttp
import discord
import time
import random
from discord.ext import commands
from dotenv import load_dotenv

# Load .env variables
load_dotenv()

# ========== ENV VARS ==========
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))
WEBSHARE_KEY = os.getenv("WEBSHARE_API_KEY")
PROXY_USER = os.getenv("PROXY_USER")
PROXY_PASS = os.getenv("PROXY_PASS")

# ========== BOT SETUP ==========
COMMAND_PREFIX = '.'
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)

# ========== CONFIG ==========
MAX_ATTEMPTS = 4
CHECK_LIMIT = 100
PROXY_MIN = 10
PROXY_MAX = 50
CONCURRENT_CHECKS = 5  # Limit concurrency to avoid hammering

class Proxy:
    def __init__(self, proxy_str):
        self.proxy_str = proxy_str
        self.hits = 0
        self.total = 0
        self.failures = 0
        self.response_time = 0
        self.last_used = time.time()

    @property
    def health(self):
        # Penalize failures: subtract failure rate from hit rate percentage
        if self.total == 0:
            return 100
        success_rate = (self.hits / self.total) * 100
        failure_rate = (self.failures / self.total) * 100
        health_score = success_rate - (failure_rate * 1.5)
        return max(health_score, 0)

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
        else:
            self.failures += 1

        print(
            f"[Proxy Health] {self.proxy_str.split('@')[-1]} | "
            f"Health: {self.health:.1f}% | Hits: {self.hits} | Failures: {self.failures} | "
            f"Total: {self.total} | Avg RT: {self.avg_response:.2f}s"
        )

class ProxyManager:
    def __init__(self):
        self.proxies = []
        self.lock = asyncio.Lock()

    async def fetch(self):
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Token {WEBSHARE_KEY}"}
            try:
                async with session.get("https://proxy.webshare.io/api/v2/proxy/list/?mode=direct", headers=headers, timeout=15) as r:
                    data = await r.json()
                    proxies_list = []
                    for proxy_data in data.get("results", []):
                        ip = proxy_data.get("proxy_address")
                        port = proxy_data.get("port")  # FIXED port key
                        if ip and port:
                            proxy_url = f"http://{PROXY_USER}:{PROXY_PASS}@{ip}:{port}"
                            proxies_list.append(proxy_url)
                    return proxies_list
            except Exception as e:
                print(f"[ProxyManager] Failed to fetch proxies: {e}")
                return []

    async def load(self):
        fresh = await self.fetch()
        async with self.lock:
            for p in fresh:
                if p not in [x.proxy_str for x in self.proxies]:
                    self.proxies.append(Proxy(p))
            # Keep only last PROXY_MAX proxies
            self.proxies = self.proxies[-PROXY_MAX:]
        print(f"[ProxyManager] Loaded {len(self.proxies)} proxies.")

    async def get(self):
        async with self.lock:
            # Remove bad proxies with health < 30 immediately
            before = len(self.proxies)
            self.proxies = [p for p in self.proxies if p.health >= 30]
            removed = before - len(self.proxies)
            if removed > 0:
                print(f"[ProxyManager] Removed {removed} bad proxies.")

            valid = sorted([p for p in self.proxies if p.health > 50], key=lambda x: x.avg_response)
            if not valid:
                print("[ProxyManager] No good proxies, restocking...")
                await self.restock()
                valid = [p for p in self.proxies if p.health > 50]
            if not valid:
                # fallback to any proxy with health > 30
                valid = [p for p in self.proxies if p.health > 30]
            if not valid:
                print("[ProxyManager] No proxies available at all!")
                return None
            return random.choice(valid)

    async def restock(self):
        print("[ProxyManager] Restocking proxies...")
        await self.load()

proxy_manager = ProxyManager()
checked, available = 0, []
checker_running = False
semaphore = asyncio.Semaphore(CONCURRENT_CHECKS)

def generate_usernames(n):
    base = ["live", "chat", "play", "stream", "kick", "zone", "cult", "digi"]
    return [random.choice(base) + str(random.randint(1, 9999)) for _ in range(n)]

async def check_username(username, session):
    global checker_running
    async with semaphore:
        for attempt in range(MAX_ATTEMPTS):
            if not checker_running:
                return False
            proxy_obj = await proxy_manager.get()
            if not proxy_obj:
                await asyncio.sleep(5)
                continue

            start = time.time()
            success = False
            try:
                async with session.get(f"https://kick.com/{username}", proxy=proxy_obj.proxy_str, timeout=10) as r:
                    if r.status == 404:
                        success = True
                    elif r.status in [429, 403]:
                        # Rate limited or forbidden: backoff exponentially
                        wait_time = 5 * (attempt + 1)
                        print(f"[Rate Limit] Status {r.status} for {username}, backing off {wait_time}s")
                        await asyncio.sleep(wait_time)
                        proxy_obj.update(time.time() - start, False)
                        continue
            except Exception as e:
                print(f"[Error] Checking {username} with proxy {proxy_obj.proxy_str.split('@')[-1]}: {e}")
                proxy_obj.update(0, False)
                await asyncio.sleep(1)
                continue

            elapsed = time.time() - start
            proxy_obj.update(elapsed, success)
            if success:
                return True
            await asyncio.sleep(random.uniform(0.3, 0.6))  # jitter delay between attempts

        return False

async def run_checker(channel):
    global checked, available, checker_running
    checked, available = 0, []
    checker_running = True
    usernames = generate_usernames(CHECK_LIMIT)

    async with aiohttp.ClientSession() as session:
        tasks = []
        for name in usernames:
            if not checker_running:
                break
            task = asyncio.create_task(check_and_report(name, session, channel))
            tasks.append(task)

        await asyncio.gather(*tasks)

    checker_running = False
    await channel.send("*Checking complete*")

async def check_and_report(name, session, channel):
    global checked, available
    if await check_username(name, session):
        available.append(name)
        await channel.send(f"✅ Available: `{name}`")
    checked += 1

@bot.event
async def on_ready():
    print(f"[Discord] Bot connected as {bot.user}")
    await proxy_manager.load()

@bot.command(name="kickstart")
async def kickstart(ctx):
    global checker_running
    if checker_running:
        await ctx.send("Checker is already running.")
        return
    await ctx.send("*Starting Kick username check...*")
    channel = bot.get_channel(DISCORD_CHANNEL_ID) or ctx.channel
    await channel.send("*Checking Kick Users*")
    asyncio.create_task(run_checker(channel))

@bot.command(name='kickstop')
async def kickstop(ctx):
    global checker_running
    if not checker_running:
        await ctx.send("Checker is not running.")
        return
    checker_running = False
    channel = bot.get_channel(DISCORD_CHANNEL_ID) or ctx.channel
    await channel.send("*Checker Stopped*")

@bot.command(name="kickstatus")
async def kickstatus(ctx):
    sample = proxy_manager.proxies[:5]
    text = "\n".join(
        f"{p.proxy_str.split('@')[-1]} | Health: {p.health:.1f}% | Hits: {p.hits} | Failures: {p.failures} | Total: {p.total} | Avg RT: {p.avg_response:.2f}s"
        for p in sample
    )
    await ctx.send(
        f"✅ Checked: {checked}/{CHECK_LIMIT}\n"
        f"🎯 Hits: {len(available)}\n"
        f"🧠 Proxy Sample:\n{text}"
    )

bot.run(DISCORD_TOKEN)
