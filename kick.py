import os
import time
import asyncio
import aiohttp
import random
import discord
import json
import string
import signal
from discord.ext import commands, tasks
from dotenv import load_dotenv
from datetime import datetime
from itertools import product

bot = commands.Bot(command_prefix='.', intents=intents)

# Load environment variables
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))
WEBSHARE_KEY = os.getenv("WEBSHARE_API_KEY")
PROXY_USER = os.getenv("PROXY_USER")
PROXY_PASS = os.getenv("PROXY_PASS")

intents = discord.Intents.default()
intents.message_content = True  # Needed if you're reading message content

GOOD_PROXIES_FILE = "proxies.txt"
PROXY_MIN = 10
PROXY_MAX = 50
PROXY_HEALTH_THRESHOLD = 50
PROXY_RESPONSE_THRESHOLD = 5
PROXY_BACKOFF = 10
PROXY_POOL_USE_FILE_THRESHOLD = 20  # Threshold to switch to proxies.txt pool

class Proxy:
    def __init__(self, proxy_str):
        self.proxy_str = proxy_str
        self.hits = 0
        self.fails = 0
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
            if self.fails > 0:
                self.fails -= 1
        else:
            self.fails += 1
        if self.total >= 10:
            self.is_good = (
                self.health >= PROXY_HEALTH_THRESHOLD and
                self.avg_response <= PROXY_RESPONSE_THRESHOLD and
                self.fails < 5
            )

    def get_indicator(self):
        if self.total == self.hits and self.total > 0:
            return "🥇"
        elif self.is_good:
            return "⚠️"
        else:
            return "❌"

class ProxyManager:
    def __init__(self):
        self.proxies = []
        self.lock = asyncio.Lock()
        self.use_file_pool = False

    def load_good_proxies(self):
        if not os.path.exists(GOOD_PROXIES_FILE):
            return
        with open(GOOD_PROXIES_FILE, "r") as f:
            lines = [line.strip() for line in f if line.strip()]
        self.proxies = [Proxy(p) for p in lines]
        print(f"[ProxyManager] Loaded {len(self.proxies)} proxies from {GOOD_PROXIES_FILE}")
        if len(self.proxies) >= PROXY_POOL_USE_FILE_THRESHOLD:
            self.use_file_pool = True

    def save_good_proxies(self):
        good = [p.proxy_str for p in self.proxies if p.is_good]
        with open(GOOD_PROXIES_FILE, "w") as f:
            for proxy in good:
                f.write(proxy + "\n")
        print(f"[ProxyManager] Saved {len(good)} good proxies to {GOOD_PROXIES_FILE}")

    async def fetch_new_proxies(self):
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

    async def refill_proxies(self):
        async with self.lock:
            before = len(self.proxies)
            self.proxies = [p for p in self.proxies if p.is_good]
            removed = before - len(self.proxies)
            if removed:
                print(f"[ProxyManager] Removed {removed} bad/slow proxies")

            if len(self.proxies) < PROXY_MIN:
                print("[ProxyManager] Proxy pool low, fetching new proxies...")
                await send_discord_message("Grabbing Best Proxies...")
                fresh = await self.fetch_new_proxies()
                existing = {p.proxy_str for p in self.proxies}
                added = 0
                for p_str in fresh:
                    if p_str not in existing and len(self.proxies) < PROXY_MAX:
                        self.proxies.append(Proxy(p_str))
                        added += 1
                print(f"[ProxyManager] Added {added} new proxies")
                await send_discord_message("Grabbed Best Proxies!")

            # Sort proxies: best health first, then fastest response time
            self.proxies.sort(key=lambda p: (-p.health, p.avg_response))
            self.proxies = self.proxies[:PROXY_MAX]

            self.save_good_proxies()

    async def get_proxy(self):
        async with self.lock:
            now = time.time()
            for proxy in self.proxies:
                if now - proxy.last_used >= PROXY_BACKOFF and proxy.is_good:
                    return proxy
            return None

# Discord messaging helper for announcements
async def send_discord_message(content: str):
    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    if channel:
        await channel.send(content)
    else:
        print(f"[Discord] Channel {DISCORD_CHANNEL_ID} not found. Message: {content}")

class UsernameGenerator:
    def __init__(self):
        self.static_users = []
        self.themes = []
        self.generated = set()
        self.use_themes = True
        self.load_lists()

    def load_lists(self):
        if os.path.exists("users.txt"):
            with open("users.txt", "r") as f:
                self.static_users = [line.strip() for line in f if line.strip()]
        if os.path.exists("themes.txt"):
            with open("themes.txt", "r") as f:
                self.themes = [line.strip() for line in f if line.strip()]

    def get_next_username(self):
        if self.static_users:
            return self.static_users.pop(0)

        # fallback: themed names
        if self.use_themes and self.themes:
            theme = random.choice(self.themes)
            suffix = ''.join(random.choices(string.ascii_lowercase, k=2))
            return f"{theme[:2]}{suffix}"

        # fallback: pronounceable 4-char generation
        while True:
            name = self.generate_pronounceable()
            if name not in self.generated:
                self.generated.add(name)
                return name

    def generate_pronounceable(self):
        vowels = "aeiou"
        consonants = ''.join(set(string.ascii_lowercase) - set(vowels))
        return random.choice(consonants) + random.choice(vowels) + random.choice(consonants) + random.choice(vowels)

# Global control variables
checker_running = False
pause_event = asyncio.Event()
pause_event.set()  # Start as not paused
last_available_username = None
last_checked_username = None
last_proxy_used = None
proxy_manager = ProxyManager()
username_generator = UsernameGenerator()

@bot.command()
async def start(ctx):
    global checker_running
    if checker_running:
        await ctx.send("Checker is already running.")
        return
    checker_running = True
    await ctx.send("Starting username checker...")
    await proxy_manager.load_good_proxies()
    await proxy_manager.refill_proxies()
    await username_checker_loop(ctx)

@bot.command()
async def stop(ctx):
    global checker_running
    if not checker_running:
        await ctx.send("Checker is not running.")
        return
    checker_running = False
    await ctx.send("Checker stopped.")

@bot.command()
async def pause(ctx):
    if not pause_event.is_set():
        await ctx.send("Checker is already paused.")
        return
    pause_event.clear()
    await ctx.send("Checker paused.")

@bot.command()
async def resume(ctx):
    if pause_event.is_set():
        await ctx.send("Checker is not paused.")
        return
    pause_event.set()
    await ctx.send("Checker resumed.")

@bot.command()
async def status(ctx):
    total_proxies = len(proxy_manager.proxies)
    good_proxies = sum(1 for p in proxy_manager.proxies if p.is_good)
    avg_health = round(sum(p.health for p in proxy_manager.proxies) / total_proxies, 2) if total_proxies else 0
    avg_response = round(sum(p.avg_response for p in proxy_manager.proxies) / total_proxies, 2) if total_proxies else 0
    global last_available_username, last_checked_username, last_proxy_used
    msg = (
        f"**Checker Status:**\n"
        f"Running: {checker_running}\n"
        f"Paused: {not pause_event.is_set()}\n"
        f"Last Available Username: {last_available_username}\n"
        f"Last Checked Username: {last_checked_username}\n"
        f"Last Proxy Used: {last_proxy_used.proxy_str if last_proxy_used else 'None'}\n"
        f"Total Proxies: {total_proxies}\n"
        f"Good Proxies: {good_proxies}\n"
        f"Average Proxy Health: {avg_health}%\n"
        f"Average Proxy Response: {avg_response:.2f}s\n"
    )
    await ctx.send(msg)

async def username_checker_loop(ctx):
    global last_available_username, last_checked_username, last_proxy_used, checker_running

    while checker_running:
        await pause_event.wait()
        username = username_generator.get_next_username()
        last_checked_username = username

        proxy = await proxy_manager.get_proxy()
        if proxy is None:
            await ctx.send("No good proxies available, refilling...")
            await proxy_manager.refill_proxies()
            await asyncio.sleep(5)
            continue
        last_proxy_used = proxy

        # Example check logic here (replace with actual API check)
        start_time = time.time()
        # Simulate request with proxy
        success = random.choice([True, False])  # Fake availability result
        response_time = time.time() - start_time
        proxy.update(response_time, success)

        if success:
            last_available_username = username
            await ctx.send(f"Available username found: {username}")

        # Periodically save good proxies and refresh pool
        if random.random() < 0.1:
            proxy_manager.save_good_proxies()
            await proxy_manager.refill_proxies()

        await asyncio.sleep(random.uniform(0.4, 1.2))

    # After stopping, save proxies
    proxy_manager.save_good_proxies()

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

def shutdown():
    print("Shutting down...")
    asyncio.create_task(bot.close())

signal.signal(signal.SIGINT, lambda s, f: shutdown())
signal.signal(signal.SIGTERM, lambda s, f: shutdown())

bot.run(DISCORD_TOKEN)
