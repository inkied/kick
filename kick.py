import os
import asyncio
import aiohttp
import time
import random
from discord.ext import commands
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))
WEBSHARE_API_KEY = os.getenv("WEBSHARE_API_KEY")
PROXY_HOST = os.getenv("PROXY_HOST")
PROXY_USER = os.getenv("PROXY_USER")
PROXY_PASS = os.getenv("PROXY_PASS")
PROXY_PORT = int(os.getenv("PROXY_PORT"))

COMMAND_PREFIX = "."
USERS_TO_CHECK = 100
MAX_ATTEMPTS_PER_USERNAME = 5
MIN_PROXIES = 10
MAX_PROXIES = 50

bot = commands.Bot(command_prefix=COMMAND_PREFIX)

class Proxy:
    def __init__(self, proxy_str):
        self.proxy_str = proxy_str
        self.hits = 0
        self.requests = 0
        self.total_response_time = 0
        self.first_used = time.time()
        self.last_used = time.time()
        self.status = "Fast"  # Fast, Slow, Bad

    @property
    def hit_rate(self):
        return self.hits / self.requests if self.requests > 0 else 0

    @property
    def avg_response_time(self):
        return self.total_response_time / self.requests if self.requests > 0 else float('inf')

    @property
    def life_time(self):
        return time.time() - self.first_used

    def update_stats(self, response_time, success):
        self.requests += 1
        self.last_used = time.time()
        self.total_response_time += response_time
        if success:
            self.hits += 1
        self.evaluate_status()

    def evaluate_status(self):
        if self.requests < 5:
            self.status = "Fast"
            return
        if self.avg_response_time > 3:
            self.status = "Bad"
        elif self.avg_response_time > 1.5:
            self.status = "Slow" if self.hit_rate >= 0.6 else "Bad"
        else:
            self.status = "Fast"

class ProxyManager:
    def __init__(self, min_proxies=MIN_PROXIES, max_proxies=MAX_PROXIES):
        self.proxies = []
        self.min_proxies = min_proxies
        self.max_proxies = max_proxies
        self.lock = asyncio.Lock()

    async def fetch_webshare_proxies(self):
        url = "https://proxy.webshare.io/api/proxy/list/"
        headers = {"Authorization": f"ApiKey {WEBSHARE_API_KEY}"}
        proxies = []
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for item in data.get("results", []):
                        ip = item.get("proxy_address")
                        port = item.get("ports", {}).get("http") or item.get("ports", {}).get("https")
                        if ip and port:
                            proxy_url = f"http://{PROXY_USER}:{PROXY_PASS}@{ip}:{port}"
                            proxies.append(proxy_url)
        return proxies

    async def load_proxies(self):
        fresh_proxy_strs = await self.fetch_webshare_proxies()
        async with self.lock:
            added = 0
            for p_str in fresh_proxy_strs:
                if not any(p.proxy_str == p_str for p in self.proxies):
                    self.proxies.append(Proxy(p_str))
                    added += 1
            # Trim excess
            if len(self.proxies) > self.max_proxies:
                self.proxies = self.proxies[-self.max_proxies:]
        print(f"[ProxyManager] Loaded {added} fresh proxies. Total now: {len(self.proxies)}")

    async def get_best_proxy(self):
        async with self.lock:
            fast = [p for p in self.proxies if p.status == "Fast"]
            slow = [p for p in self.proxies if p.status == "Slow" and p.hit_rate >= 0.6]
            candidates = fast + slow

            if not candidates:
                await self.restock_proxies()
                fast = [p for p in self.proxies if p.status == "Fast"]
                if not fast:
                    raise Exception("No valid proxies available after restock")
                candidates = fast

            candidates.sort(key=lambda p: (p.life_time, p.hit_rate), reverse=True)
            return candidates[0]

    async def restock_proxies(self):
        async with self.lock:
            if len(self.proxies) >= self.min_proxies:
                return
            print("[ProxyManager] Restocking proxies...")
            fresh_proxy_strs = await self.fetch_webshare_proxies()
            fresh_proxies = [Proxy(p) for p in fresh_proxy_strs if p not in [x.proxy_str for x in self.proxies]]
            space_left = self.max_proxies - len(self.proxies)
            self.proxies.extend(fresh_proxies[:space_left])
            print(f"[ProxyManager] Restocked with {len(fresh_proxies[:space_left])} proxies.")

    async def cleanup_bad_proxies(self):
        async with self.lock:
            before = len(self.proxies)
            self.proxies = [p for p in self.proxies if p.status != "Bad"]
            after = len(self.proxies)
            if before != after:
                print(f"[ProxyManager] Removed {before - after} bad proxies.")

    async def update_proxy_stats(self, proxy_str, response_time, success):
        async with self.lock:
            for proxy in self.proxies:
                if proxy.proxy_str == proxy_str:
                    proxy.update_stats(response_time, success)
                    break
        await self.cleanup_bad_proxies()
        if len(self.proxies) < self.min_proxies:
            await self.restock_proxies()

proxy_manager = ProxyManager()

users_checked = 0
hits = []
checker_paused = False
checker_lock = asyncio.Lock()

def generate_usernames(n):
    # Simple themed list, no digits or underscores
    base_words = [
        "gamer", "tech", "stream", "pro", "kick", "live", "zone", "chat", "play", "cast",
        "pixel", "nova", "alpha", "bravo", "delta", "echo", "foxtrot", "gamma", "zenith"
    ]
    usernames = []
    while len(usernames) < n:
        word = random.choice(base_words)
        if any(c.isdigit() or c == '_' for c in word):
            continue
        usernames.append(word)
    return usernames

async def check_username(username):
    global users_checked
    tries = 0
    while tries < MAX_ATTEMPTS_PER_USERNAME:
        tries += 1
        proxy = await proxy_manager.get_best_proxy()
        proxy_url = proxy.proxy_str
        start = time.time()
        success = False
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://kick.com/{username}", proxy=proxy_url, timeout=10) as resp:
                    if resp.status == 404:  # Available
                        success = True
        except Exception:
            pass
        response_time = time.time() - start
        await proxy_manager.update_proxy_stats(proxy_url, response_time, success)
        if success:
            hits.append({'username': username, 'timestamp': time.time(), 'tries': tries})
            await send_discord_message(f"â Available: `{username}` (tries: {tries})")
            break
    users_checked += 1
    return success

async def run_checker():
    global users_checked, hits, checker_paused
    users_checked = 0
    hits.clear()
    usernames = generate_usernames(USERS_TO_CHECK)
    for username in usernames:
        # Pause handling
        while True:
            async with checker_lock:
                if not checker_paused:
                    break
            await asyncio.sleep(1)
        await check_username(username)
        await asyncio.sleep(0.1)
    await send_discord_message(f"â Checker finished. Checked {users_checked} usernames. Hits: {len(hits)}")

# Rate limiting discord messages to 5 seconds apart
discord_message_lock = asyncio.Lock()
last_discord_message_time = 0

async def send_discord_message(content):
    global last_discord_message_time
    async with discord_message_lock:
        now = time.time()
        wait = 5 - (now - last_discord_message_time)
        if wait > 0:
            await asyncio.sleep(wait)
        async with aiohttp.ClientSession() as session:
            url = f"https://discord.com/api/channels/{DISCORD_CHANNEL_ID}/messages"
            headers = {
                "Authorization": f"Bot {DISCORD_TOKEN}",
                "Content-Type": "application/json"
            }
            json = {"content": content}
            try:
                async with session.post(url, headers=headers, json=json) as resp:
                    if resp.status != 200 and resp.status != 201:
                        print(f"[Discord] Failed to send message: {resp.status}")
            except Exception as e:
                print(f"[Discord] Exception sending message: {e}")
        last_discord_message_time = time.time()

check_task = None

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await proxy_manager.load_proxies()
    await send_discord_message(f"ð¤ Kick username checker bot started.")

@bot.command()
async def start(ctx):
    global check_task, checker_paused
    if check_task and not check_task.done():
        await ctx.send("Checker already running.")
        return
    checker_paused = False
    check_task = asyncio.create_task(run_checker())
    await ctx.send("â Checker started.")

@bot.command()
async def stop(ctx):
    global checker_paused
    if check_task is None:
        await ctx.send("Checker is not running.")
        return
    checker_paused = True
    await ctx.send("ð Checker paused.")

@bot.command()
async def resume(ctx):
    global checker_paused
    if check_task is None:
        await ctx.send("Checker is not running.")
        return
    if not checker_paused:
        await ctx.send("Checker is already running.")
        return
    checker_paused = False
    await ctx.send("â¶ï¸ Checker resumed.")

@bot.command()
async def status(ctx):
    msg = (f"Users checked: {users_checked}\n"
           f"Hits found: {len(hits)}\n"
           f"Proxies loaded: {len(proxy_manager.proxies)}")
    await ctx.send(msg)

bot.run(DISCORD_TOKEN)
