import os
import asyncio
import aiohttp
import discord
import time
import random
from discord.ext import commands
from dotenv import load_dotenv
from datetime import datetime
import pytz

load_dotenv()

# ========== ENV VARS ==========
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))
WEBSHARE_KEY = os.getenv("WEBSHARE_API_KEY")
PROXY_USER = os.getenv("PROXY_USER")
PROXY_PASS = os.getenv("PROXY_PASS")

# ========== CONFIG ==========
COMMAND_PREFIX = '.'
BATCH_SIZE = 100  # usernames per batch
PROXY_MIN = 10
PROXY_MAX = 50
GOOD_PROXIES_FILE = "proxies.txt"
HITS_FILE = "hits.txt"
USERS_FILE = "users.txt"
PROXY_HEALTH_THRESHOLD = 50  # minimum proxy health percentage
PROXY_BACKOFF = 10  # seconds cooldown between proxy uses
CHECK_DELAY = 0.3  # seconds between username checks

# Tennessee timezone (Central Time)
TZ = pytz.timezone('America/Chicago')

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

        if self.total >= PROXY_MIN and self.health < PROXY_HEALTH_THRESHOLD:
            self.is_good = False

    def __str__(self):
        return f"{self.proxy_str.split('@')[-1]} | Health: {self.health:.1f}% | Hits: {self.hits} | Total: {self.total} | Avg RT: {self.avg_response:.2f}s | Good: {self.is_good}"


class ProxyManager:
    def __init__(self):
        self.proxies = []
        self.lock = asyncio.Lock()

    async def fetch(self):
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Token {WEBSHARE_KEY}"}
            async with session.get("https://proxy.webshare.io/api/v2/proxy/list/?mode=rotating", headers=headers) as r:
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
        loaded = self.load_good_proxies()
        async with self.lock:
            self.proxies = loaded
            fresh = await self.fetch()
            for p in fresh:
                if p not in [x.proxy_str for x in self.proxies]:
                    self.proxies.append(Proxy(p))
            # Limit max proxies
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

checked = 0
available = []
checker_running = False
batch_start_time = None
eta_message = None


def read_users(batch_size):
    if not os.path.exists(USERS_FILE):
        print(f"[Users] {USERS_FILE} does not exist! Creating empty file.")
        with open(USERS_FILE, "w") as f:
            pass
        return []
    with open(USERS_FILE, "r") as f:
        lines = [line.strip() for line in f if line.strip()]
    batch = lines[:batch_size]
    leftover = lines[batch_size:]
    with open(USERS_FILE, "w") as f:
        for u in leftover:
            f.write(u + "\n")
    return batch


def save_hits(usernames):
    with open(HITS_FILE, "a") as f:
        for u in usernames:
            f.write(u + "\n")


def format_seconds(seconds):
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins:02d}:{secs:02d}"


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


async def run_batch(channel):
    global checked, available, checker_running, batch_start_time, eta_message
    checked = 0
    available = []
    checker_running = True
    usernames = read_users(BATCH_SIZE)
    if not usernames:
        await channel.send("⚠️ No usernames found in users.txt to check.")
        checker_running = False
        return
    await channel.send(f"🔄 Starting new batch of {len(usernames)} usernames.")
    batch_start_time = time.time()

    eta_message = await channel.send(f"⏳ ETA: calculating...")

    total_to_check = len(usernames)
    for idx, name in enumerate(usernames, start=1):
        if not checker_running:
            break
        if await check_username(name):
            available.append(name)
            await channel.send(f"✅ Available: `{name}`")
        checked += 1
        if idx % 10 == 0 or idx == total_to_check:
            elapsed = time.time() - batch_start_time
            avg_per_check = elapsed / checked if checked else 0.3
            remaining = (total_to_check - checked) * avg_per_check
            eta_str = format_seconds(remaining)
            now_local = datetime.now(TZ).strftime("%H:%M:%S")
            try:
                await eta_message.edit(content=f"⏳ ETA: {eta_str} (Tennessee time {now_local})")
            except Exception:
                pass
        await asyncio.sleep(CHECK_DELAY)

    save_hits(available)
    checker_running = False

    success_rate = (len(available) / checked * 100) if checked else 0
    summary = (
        f"✅ Batch complete! Checked: {checked} | Hits: {len(available)} | "
        f"Success Rate: {success_rate:.2f}%"
    )
    await channel.send(summary)


async def auto_loop(channel):
    while True:
        if checker_running:
            await asyncio.sleep(5)
            continue
        await run_batch(channel)
        await asyncio.sleep(3)


@bot.event
async def on_ready():
    print(f"[Discord] Bot connected as {bot.user}")
    await proxy_manager.load()
    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    if channel:
        await channel.send("✅ Bot started and ready!")
        bot.loop.create_task(auto_loop(channel))
    else:
        print(f"[Discord] Channel ID {DISCORD_CHANNEL_ID} not found.")


@bot.command()
async def start(ctx):
    global checker_running
    if checker_running:
        await ctx.send("Checker already running.")
    else:
        checker_running = True
        await ctx.send("Starting checker...")

@bot.command()
async def stop(ctx):
    global checker_running
    if not checker_running:
        await ctx.send("Checker is not running.")
    else:
        checker_running = False
        await ctx.send("Checker stopped.")


bot.run(DISCORD_TOKEN)
