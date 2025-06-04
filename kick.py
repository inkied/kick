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
MAX_ATTEMPTS = 1  # No retry on username, skip if fail once
CHECK_LIMIT = 100  # Batch size
PROXY_MIN = 10
PROXY_MAX = 50
GOOD_PROXIES_FILE = "proxies.txt"
PROXY_HEALTH_THRESHOLD = 50  # Only use proxies with health > 50%
PROXY_BACKOFF = 10  # seconds cooldown for proxy reuse
AVG_DELAY = 0.85  # Average delay per check for estimated time calculation

# Local timezone for timestamping
LOCAL_TIMEZONE = pytz.timezone("America/New_York")  # Eastern Time for Tennessee

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

        # Mark proxy as bad if health < threshold and used enough times
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
            fresh = await self.fetch()
            for p in fresh:
                if p not in [x.proxy_str for x in self.proxies]:
                    self.proxies.append(Proxy(p))
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
            print(f"[ProxyManager] No proxies.txt found to load.")
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

def load_usernames_from_file(filename="users.txt"):
    if not os.path.exists(filename):
        print(f"[Usernames] {filename} not found. Creating new file.")
        open(filename, "w").close()
        return []
    with open(filename, "r") as f:
        users = [line.strip() for line in f if line.strip()]
    print(f"[Usernames] Loaded {len(users)} usernames from {filename}")
    return users

def append_usernames_to_file(usernames, filename="users.txt"):
    with open(filename, "a") as f:
        for u in usernames:
            f.write(u + "\n")

def save_hits(usernames, filename="hits.txt"):
    with open(filename, "a") as f:
        for u in usernames:
            f.write(u + "\n")

def generate_usernames(n):
    # Mix of semi-OG words, short users, brandables (customize your list here)
    base_words = [
        "stream", "kick", "zone", "live", "play", "cult", "digi", "vibe", "wave", "flux",
        "nova", "pulse", "echo", "drift", "luxe", "glow", "rise", "prime", "core", "shift"
    ]
    usernames = []
    for _ in range(n):
        word = random.choice(base_words)
        suffix = str(random.randint(1, 9999))
        usernames.append(word + suffix)
    return usernames

async def check_username(username):
    proxy_obj = await proxy_manager.get()
    if not proxy_obj:
        print("[Checker] No proxy available for checking.")
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
    checker_running = True
    total_checked = 0
    total_hits = 0

    while checker_running:
        # Load usernames batch
        usernames = load_usernames_from_file()
        if len(usernames) < CHECK_LIMIT:
            # Generate more usernames and append
            new_users = generate_usernames(CHECK_LIMIT - len(usernames))
            append_usernames_to_file(new_users)
            usernames += new_users

        batch = usernames[:CHECK_LIMIT]

        # Remove batch from users.txt (keep rest)
        rest = usernames[CHECK_LIMIT:]
        with open("users.txt", "w") as f:
            for u in rest:
                f.write(u + "\n")

        # Print batch start info
        now_str = datetime.now(LOCAL_TIMEZONE).strftime("%H:%M:%S")
        est_time = CHECK_LIMIT * AVG_DELAY
        await channel.send(f"🔄 Starting new batch at {now_str} with {CHECK_LIMIT} usernames")
        await channel.send(f"⏱ Estimated time to check: {est_time:.1f} seconds")

        batch_hits = []
        batch_checked = 0

        for username in batch:
            if not checker_running:
                break
            is_available = await check_username(username)
            if is_available:
                batch_hits.append(username)
                total_hits += 1
                await channel.send(f"✅ Available: `{username}`")
            batch_checked += 1
            total_checked += 1
            checked = total_checked

            await asyncio.sleep(0.3)  # Delay between checks

        available.extend(batch_hits)
        save_hits(batch_hits)

        # Save good proxies after batch
        proxy_manager.save_good_proxies()

        # Batch summary
        success_rate = (total_hits / total_checked * 100) if total_checked else 0
        await channel.send(
            f"✅ Batch complete: Total checked: {total_checked} | Hits: {total_hits} | Success rate: {success_rate:.2f}%"
        )

@bot.event
async def on_ready():
    print(f"[Discord] Bot connected as {bot.user}")
    await proxy_manager.load()
    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    if not channel:
        print("[Discord] Could not find the channel. Please check the ID.")
        return
    await run_checker(channel)

bot.run(DISCORD_TOKEN)
