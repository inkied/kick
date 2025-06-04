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
intents.message_content = True  # Required to read user messages for commands

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)

# ========== CONFIG ==========
MAX_ATTEMPTS = 4
CHECK_LIMIT = 100
PROXY_MIN = 10
PROXY_MAX = 50
CONCURRENT_CHECKS = 10  # Max parallel username checks

class Proxy:
    def __init__(self, proxy_str):
        self.proxy_str = proxy_str
        self.hits = 0
        self.total = 0
        self.response_time = 0
        self.last_used = time.time()
        self.failed_attempts = 0  # track repeated failures

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
            self.failed_attempts = 0
        else:
            self.failed_attempts += 1

        # Proxy health logging:
        print(
            f"[Proxy Health] {self.proxy_str.split('@')[-1]} | "
            f"Health: {self.health:.1f}% | "
            f"Hits: {self.hits} | "
            f"Total: {self.total} | "
            f"Failed: {self.failed_attempts} | "
            f"Avg RT: {self.avg_response:.2f}s"
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
                proxies_list = []
                for proxy_data in data.get("results", []):
                    ip = proxy_data.get("proxy_address")
                    port = proxy_data.get("port") or proxy_data.get("proxy_port")  # fallback key check
                    if ip and port:
                        proxy_url = f"http://{PROXY_USER}:{PROXY_PASS}@{ip}:{port}"
                        proxies_list.append(proxy_url)
                return proxies_list

    async def load(self):
        fresh = await self.fetch()
        async with self.lock:
            for p in fresh:
                if p not in [x.proxy_str for x in self.proxies]:
                    self.proxies.append(Proxy(p))
            # Keep only the last PROXY_MAX proxies (trim oldest if too many)
            self.proxies = self.proxies[-PROXY_MAX:]

    async def get(self):
        async with self.lock:
            # Filter proxies: health > 50%, failed_attempts < 3, not used too recently
            valid = [
                p for p in self.proxies
                if p.health > 50 and p.failed_attempts < 3 and (time.time() - p.last_used) > 1
            ]
            if len(valid) < PROXY_MIN:
                await self.restock()
                valid = [
                    p for p in self.proxies
                    if p.health > 50 and p.failed_attempts < 3 and (time.time() - p.last_used) > 1
                ]
            if not valid:
                return None
            # Sort by avg response time, pick fastest proxy
            valid = sorted(valid, key=lambda x: x.avg_response)
            return valid[0]

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

async def check_username(username):
    for attempt in range(MAX_ATTEMPTS):
        proxy_obj = await proxy_manager.get()
        if not proxy_obj:
            await asyncio.sleep(5)  # wait before retrying if no proxies
            continue

        start = time.time()
        success = False
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"https://kick.com/{username}",
                    proxy=proxy_obj.proxy_str,
                    timeout=10
                ) as r:
                    if r.status == 404:
                        success = True
        except Exception:
            pass
        elapsed = time.time() - start
        proxy_obj.update(elapsed, success)

        if success:
            return True
        else:
            # If repeated fails, back off a bit before retrying
            await asyncio.sleep(0.5)
    return False

async def check_username_semaphore(username, channel):
    global checked, available, checker_running
    async with semaphore:
        if not checker_running:
            return
        if await check_username(username):
            available.append(username)
            await channel.send(f"✅ Available: `{username}`")
        checked += 1

async def run_checker(channel):
    global checked, available, checker_running
    checked, available = 0, []
    checker_running = True
    usernames = generate_usernames(CHECK_LIMIT)

    tasks = []
    for name in usernames:
        if not checker_running:
            break
        task = asyncio.create_task(check_username_semaphore(name, channel))
        tasks.append(task)
    await asyncio.gather(*tasks)

    checker_running = False
    await channel.send("*Checking complete*")

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
    await ctx.send("*Checking Kick Users*")
    channel = bot.get_channel(DISCORD_CHANNEL_ID) or ctx.channel
    await channel.send("*Checking Kick Users*")
    await run_checker(channel)

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
        f"{p.proxy_str.split('@')[-1]} | Health: {p.health:.1f}% | Hits: {p.hits} | Total: {p.total} | Failed: {p.failed_attempts} | Avg RT: {p.avg_response:.2f}s"
        for p in sample
    )
    await ctx.send(
        f"✅ Checked: {checked}/{CHECK_LIMIT}\n"
        f"🎯 Hits: {len(available)}\n"
        f"🧠 Proxy Sample:\n{text}"
    )

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
