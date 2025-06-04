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

# ========== CONFIG ==========
COMMAND_PREFIX = '.'
MAX_ATTEMPTS = 1  # No retry on username, skip if fail once
CHECK_LIMIT = 100
PROXY_MIN = 10
PROXY_MAX = 50
GOOD_PROXIES_FILE = "proxies.txt"
PROXY_HEALTH_THRESHOLD = 50  # Only use proxies with health > 50%
PROXY_BACKOFF = 10  # seconds to cool down a proxy with low health

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)

class Proxy:
    def __init__(self, proxy_str):
        self.proxy_str = proxy_str
        self.hits = 0
        self.total = 0
        self.response_time = 0
        self.last_used = 0
        self.is_good = True

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

        # Mark proxy as bad if health < threshold and total >= PROXY_MIN usage
        if self.total >= PROXY_MIN and self.health < PROXY_HEALTH_THRESHOLD:
            self.is_good = False

        print(
            f"[Proxy Health] {self.proxy_str.split('@')[-1]} | "
            f"Health: {self.health:.1f}% | Hits: {self.hits} | Total: {self.total} | "
            f"Avg RT: {self.avg_response:.2f}s | Good: {self.is_good}"
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
                    port = proxy_data.get("port") or proxy_data.get("proxy_port")
                    if ip and port:
                        proxy_url = f"http://{PROXY_USER}:{PROXY_PASS}@{ip}:{port}"
                        proxies_list.append(proxy_url)
                return proxies_list

    async def load(self):
        # Load saved good proxies first
        loaded = self.load_good_proxies()
        async with self.lock:
            self.proxies = loaded
            # Add fresh proxies from webshare
            fresh = await self.fetch()
            for p in fresh:
                if p not in [x.proxy_str for x in self.proxies]:
                    self.proxies.append(Proxy(p))
            # Trim proxies to max size
            self.proxies = self.proxies[-PROXY_MAX:]
        print(f"[ProxyManager] Loaded {len(self.proxies)} proxies total.")

    def save_good_proxies(self):
        good = [p.proxy_str for p in self.proxies if p.is_good]
        with open(GOOD_PROXIES_FILE, "w") as f:
            for proxy in good:
                f.write(proxy + "\n")
        print(f"[ProxyManager] Saved {len(good)} good proxies to {GOOD_PROXIES_FILE}")

    def load_good_proxies(self):
        if not os.path.exists(GOOD_PROXIES_FILE):
            return []
        with open(GOOD_PROXIES_FILE, "r") as f:
            lines = [line.strip() for line in f if line.strip()]
        print(f"[ProxyManager] Loaded {len(lines)} proxies from {GOOD_PROXIES_FILE}")
        return [Proxy(p) for p in lines]

    async def get(self):
        async with self.lock:
            now = time.time()
            valid = [p for p in self.proxies if p.is_good and (now - p.last_used) > PROXY_BACKOFF]
            valid = sorted(valid, key=lambda x: x.avg_response)
            if not valid:
                print("[ProxyManager] No healthy proxies available, restocking...")
                await self.load()
                valid = [p for p in self.proxies if p.is_good]
            if not valid:
                print("[ProxyManager] No proxies available at all!")
                return None
            return random.choice(valid)

proxy_manager = ProxyManager()
checked, available = 0, []
checker_running = False

def generate_usernames(n):
    base = ["live", "chat", "play", "stream", "kick", "zone", "cult", "digi"]
    return [random.choice(base) + str(random.randint(1, 9999)) for _ in range(n)]

async def check_username(username):
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
    except Exception:
        pass
    elapsed = time.time() - start
    proxy_obj.update(elapsed, success)
    return success

async def run_checker(channel):
    global checked, available, checker_running
    checked, available = 0, []
    checker_running = True
    usernames = generate_usernames(CHECK_LIMIT)
    for name in usernames:
        if not checker_running:
            break
        if await check_username(name):
            available.append(name)
            await channel.send(f"✅ Available: `{name}`")
        checked += 1
        await asyncio.sleep(0.3)  # Speed control, tweak if needed
    checker_running = False
    proxy_manager.save_good_proxies()
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
        f"{p.proxy_str.split('@')[-1]} | Health: {p.health:.1f}% | Hits: {p.hits} | Total: {p.total} | Avg RT: {p.avg_response:.2f}s | Good: {p.is_good}"
        for p in sample
    )
    await ctx.send(
        f"✅ Checked: {checked}/{CHECK_LIMIT}\n"
        f"🎯 Hits: {len(available)}\n"
        f"🧠 Proxy Sample:\n{text}"
    )

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
