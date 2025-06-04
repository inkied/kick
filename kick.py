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
MAX_ATTEMPTS = 1
CHECK_LIMIT = 100
GOOD_PROXIES_FILE = "proxies.txt"
USERS_FILE = "users.txt"
PROXY_HEALTH_THRESHOLD = 100
PROXY_BACKOFF = 10
PROXY_MAX = 50

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)

# ========== Proxy Handling ==========
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
        if self.total >= 10 and self.health < PROXY_HEALTH_THRESHOLD:
            self.is_good = False

class ProxyManager:
    def __init__(self):
        self.proxies = []
        self.lock = asyncio.Lock()

    async def fetch(self):
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Token {WEBSHARE_KEY}"}
            async with session.get("https://proxy.webshare.io/api/v2/proxy/list/?mode=direct", headers=headers) as r:
                data = await r.json()
                proxies = []
                for proxy_data in data.get("results", []):
                    ip = proxy_data.get("proxy_address")
                    port = proxy_data.get("port") or proxy_data.get("proxy_port")
                    if ip and port:
                        url = f"http://{PROXY_USER}:{PROXY_PASS}@{ip}:{port}"
                        proxies.append(Proxy(url))
                return proxies

    async def load(self):
        good = self.load_good_proxies()
        fresh = await self.fetch()
        async with self.lock:
            seen = {p.proxy_str for p in good}
            for p in fresh:
                if p.proxy_str not in seen:
                    good.append(p)
            self.proxies = good[-PROXY_MAX:]
        print(f"[ProxyManager] Loaded {len(self.proxies)} proxies")

    def save_good_proxies(self):
        good = [p.proxy_str for p in self.proxies if p.is_good]
        with open(GOOD_PROXIES_FILE, "w") as f:
            f.write('\n'.join(good))
        print(f"[ProxyManager] Saved {len(good)} proxies")

    def load_good_proxies(self):
        if not os.path.exists(GOOD_PROXIES_FILE):
            return []
        with open(GOOD_PROXIES_FILE) as f:
            return [Proxy(line.strip()) for line in f if line.strip()]

    async def get(self):
        async with self.lock:
            now = time.time()
            valid = [p for p in self.proxies if p.is_good and now - p.last_used > PROXY_BACKOFF]
            valid.sort(key=lambda x: x.avg_response)
            if not valid:
                await self.load()
                valid = [p for p in self.proxies if p.is_good]
            return random.choice(valid) if valid else None

proxy_manager = ProxyManager()

# ========== Username Generation ==========
def generate_usernames(n):
    base = ["zone", "vibe", "byte", "cult", "moda", "luno", "vexa", "kine", "silo", "echo", "meta", "bloom", "realm"]
    eng = ["drift", "mild", "bolt", "maze", "sage", "holo", "grip", "flair", "lurk", "sprint", "swipe"]
    result = set()
    while len(result) < n:
        name = random.choice(base + eng) + str(random.randint(1, 9999))
        if 4 <= len(name) <= 12:
            result.add(name)
    with open(USERS_FILE, "w") as f:
        f.write('\n'.join(result))
    return list(result)

def load_usernames():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE) as f:
            return [line.strip() for line in f if line.strip()]
    return []

# ========== Username Checking ==========
checked, available, checker_running = 0, [], False

async def check_username(username):
    proxy_obj = await proxy_manager.get()
    if not proxy_obj:
        return False
    start = time.time()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://kick.com/{username}", proxy=proxy_obj.proxy_str, timeout=10) as r:
                success = r.status == 404
    except Exception:
        success = False
    proxy_obj.update(time.time() - start, success)
    return success

async def run_checker(channel):
    global checked, available, checker_running
    while True:
        if not os.path.exists(USERS_FILE) or os.stat(USERS_FILE).st_size == 0:
            usernames = generate_usernames(CHECK_LIMIT)
            print("[UserGen] New batch generated.")
        else:
            usernames = load_usernames()
            print("[UserGen] Loaded users.txt")

        checked = 0
        available = []
        checker_running = True
        start_time = time.time()
        for name in usernames:
            if not checker_running:
                break
            if await check_username(name):
                available.append(name)
                await channel.send(f"✅ Available: `{name}`")
            checked += 1
            await asyncio.sleep(0.3)
        elapsed = round(time.time() - start_time, 2)
        checker_running = False

        # Save hits, wipe file
        with open(USERS_FILE, "w") as f:
            f.write('\n'.join(available))
        proxy_manager.save_good_proxies()
        await channel.send(f"*Batch complete in {elapsed}s — {len(available)} hits*")

# ========== Discord Bot Commands ==========
@bot.event
async def on_ready():
    print(f"[Discord] Logged in as {bot.user}")
    await proxy_manager.load()

@bot.command(name="kickstart")
async def kickstart(ctx):
    global checker_running
    if checker_running:
        await ctx.send("⚠️ Already running.")
        return
    await ctx.send("🎯 Starting Kick username checker...")
    usernames = load_usernames() or generate_usernames(CHECK_LIMIT)
    est_time = round(len(usernames) * 0.3, 1)
    await ctx.send(f"⏱ Estimated check time: {est_time}s for {len(usernames)} users")
    await run_checker(bot.get_channel(DISCORD_CHANNEL_ID))

@bot.command(name="kickstop")
async def kickstop(ctx):
    global checker_running
    if not checker_running:
        await ctx.send("🛑 Not running.")
        return
    checker_running = False
    await ctx.send("*Checker manually stopped.*")

@bot.command(name="kickstatus")
async def kickstatus(ctx):
    sample = proxy_manager.proxies[:5]
    proxy_report = "\n".join(
        f"{p.proxy_str.split('@')[-1]} | Health: {p.health:.1f}% | Good: {p.is_good}" for p in sample
    )
    await ctx.send(
        f"✅ Checked: {checked}/{CHECK_LIMIT}\n🎯 Hits: {len(available)}\n🧠 Proxies Sample:\n{proxy_report}"
    )

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
