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
from intertools import product

# Load environment variables
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))
WEBSHARE_KEY = os.getenv("WEBSHARE_API_KEY")
PROXY_USER = os.getenv("PROXY_USER")
PROXY_PASS = os.getenv("PROXY_PASS")

GOOD_PROXIES_FILE = "proxies.txt"
PROXY_MIN = 10
PROXY_MAX = 50
PROXY_HEALTH_THRESHOLD = 50
PROXY_RESPONSE_THRESHOLD = 5
PROXY_BACKOFF = 10

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

    def load_good_proxies(self):
        if not os.path.exists(GOOD_PROXIES_FILE):
            return
        with open(GOOD_PROXIES_FILE, "r") as f:
            lines = [line.strip() for line in f if line.strip()]
        self.proxies = [Proxy(p) for p in lines]
        print(f"[ProxyManager] Loaded {len(self.proxies)} proxies from {GOOD_PROXIES_FILE}")

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
                fresh = await self.fetch_new_proxies()
                existing = {p.proxy_str for p in self.proxies}
                added = 0
                for p_str in fresh:
                    if p_str not in existing and len(self.proxies) < PROXY_MAX:
                        self.proxies.append(Proxy(p_str))
                        added += 1
                print(f"[ProxyManager] Added {added} new proxies")

            self.proxies.sort(key=lambda x: (x.avg_response, -x.hits))
            self.proxies = self.proxies[:PROXY_MAX]
            self.save_good_proxies()

    async def get_proxy(self):
        async with self.lock:
            now = time.time()
            for proxy in self.proxies:
                if now - proxy.last_used >= PROXY_BACKOFF and proxy.is_good:
                    return proxy
            return None

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

# Part 4: Main username checker loop, HTTP logic, Discord alert sending

class UsernameChecker:
    def __init__(self, proxy_manager, discord_bot, username_source):
        self.proxy_manager = proxy_manager
        self.discord_bot = discord_bot
        self.username_source = username_source
        self.running = True
        self.paused = False
        self.checked_count = 0
        self.start_time = time.time()

    async def check_username(self, username):
        proxy_obj = await self.proxy_manager.get_best_proxy()
        proxy = proxy_obj.proxy_str if proxy_obj else None

        url = f"https://kick.com/api/v1/users/{username}"
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; UsernameChecker/1.0)",
            "Accept": "application/json"
        }

        start = time.time()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, proxy=proxy, timeout=10) as response:
                    elapsed = time.time() - start
                    proxy_obj.update(elapsed, True)  # success update proxy health

                    if response.status == 404:
                        # Username available
                        timestamp = datetime.datetime.now().strftime("%m/%d/%Y")
                        with open("hits.txt", "a") as f:
                            f.write(f"{username} | Checked tries: {proxy_obj.total} | Time: {timestamp}\n")

                        await self.discord_bot.send_alert(username)
                        return True
                    elif response.status == 200:
                        # Username taken
                        return False
                    else:
                        # Other status codes, treat as fail for proxy health
                        proxy_obj.update(elapsed, False)
                        return False
        except Exception as e:
            elapsed = time.time() - start
            if proxy_obj:
                proxy_obj.update(elapsed, False)
            return False

    async def main_loop(self):
        while self.running:
            if self.paused:
                await asyncio.sleep(1)
                continue

            username = await self.username_source.get_next_username()
            if not username:
                # Refill list or wait
                await self.username_source.refill()
                await asyncio.sleep(1)
                continue

            self.checked_count += 1
            available = await self.check_username(username)

            # Adjust proxy priority here if needed based on health

            # You can add heartbeat or UI update here as needed

            await asyncio.sleep(0.1)  # adjust delay for rate-limiting or smoothing

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def stop(self):
        self.running = False


class DiscordBot:
    def __init__(self, token, channel_id):
        intents = discord.Intents.default()
        self.bot = commands.Bot(command_prefix=".", intents=intents)
        self.token = token
        self.channel_id = channel_id
        self.channel = None

        @self.bot.event
        async def on_ready():
            self.channel = self.bot.get_channel(self.channel_id)
            print(f"[DiscordBot] Logged in as {self.bot.user}")

        @self.bot.command()
        async def pause(ctx):
            checker.pause()
            await ctx.send("Paused checking.")

        @self.bot.command()
        async def resume(ctx):
            checker.resume()
            await ctx.send("Resumed checking.")

        @self.bot.command()
        async def stop(ctx):
            checker.stop()
            await ctx.send("Stopped checking.")

    async def send_alert(self, username):
        if not self.channel:
            print("[DiscordBot] Channel not ready.")
            return
        await self.channel.send(f"✅ Username available: {username}")

    def run(self):
        self.bot.run(self.token)

class UsernameSource:
    def __init__(self, users_file="users.txt", themes_file="themes.txt"):
        self.users_file = users_file
        self.themes_file = themes_file
        self.usernames = []
        self.themes = []
        self.lock = asyncio.Lock()
        self.index = 0

    async def load_users(self):
        if not os.path.exists(self.users_file):
            return
        async with self.lock:
            with open(self.users_file, "r") as f:
                self.usernames = [line.strip() for line in f if line.strip()]
            self.index = 0
            print(f"[UsernameSource] Loaded {len(self.usernames)} usernames from {self.users_file}")

    async def load_themes(self):
        if not os.path.exists(self.themes_file):
            return
        async with self.lock:
            with open(self.themes_file, "r") as f:
                self.themes = [line.strip() for line in f if line.strip()]
            print(f"[UsernameSource] Loaded {len(self.themes)} themes from {self.themes_file}")

    async def refill(self):
        # Reload users and append themed usernames
        await self.load_users()
        await self.load_themes()

        async with self.lock:
            # Example: combine themes with random suffixes or just append themes
            themed_usernames = []
            for theme in self.themes:
                # You can generate themed usernames here, for simplicity just use theme
                themed_usernames.append(theme)
            # Append themed usernames to usernames list avoiding duplicates
            for tuser in themed_usernames:
                if tuser not in self.usernames:
                    self.usernames.append(tuser)

            print(f"[UsernameSource] Refilled username list with themes. Total now: {len(self.usernames)}")

    async def get_next_username(self):
        async with self.lock:
            if self.index >= len(self.usernames):
                return None
            username = self.usernames[self.index]
            self.index += 1
            return username

# Graceful shutdown helper
def setup_graceful_shutdown(loop, checker, proxy_manager):

    def shutdown():
        print("[Shutdown] Stopping checker and saving proxies...")
        checker.stop()
        proxy_manager.save_good_proxies()
        loop.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown)

bot.run(DISCORD_TOKEN)
