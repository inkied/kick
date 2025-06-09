# ======= PART 1: Imports, env vars, Proxy and ProxyManager classes =======

import os
import asyncio
import aiohttp
import discord
import time
import random
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))
WEBSHARE_KEY = os.getenv("WEBSHARE_API_KEY")
PROXY_USER = os.getenv("PROXY_USER")
PROXY_PASS = os.getenv("PROXY_PASS")

COMMAND_PREFIX = '.'
CHECK_LIMIT = 100
PROXY_MIN = 10
PROXY_MAX = 50
GOOD_PROXIES_FILE = "proxies.txt"
PROXY_HEALTH_THRESHOLD = 50  # %
PROXY_RESPONSE_THRESHOLD = 5  # seconds
PROXY_BACKOFF = 10  # seconds cooldown between proxy uses
USERS_FILE = "users.txt"
THEMES_FILE = "themes.txt"
HITS_FILE = "hits.txt"

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
        if self.total >= 10:
            self.is_good = (self.health >= PROXY_HEALTH_THRESHOLD and self.avg_response <= PROXY_RESPONSE_THRESHOLD)

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

            self.proxies.sort(key=lambda x: (-x.hits, x.avg_response))
            self.proxies = self.proxies[:PROXY_MAX]

            self.save_good_proxies()

    async def get_proxy(self):
        async with self.lock:
            now = time.time()
            available = [p for p in self.proxies if p.is_good and (now - p.last_used) > PROXY_BACKOFF]
            if not available:
                await self.refill_proxies()
                now = time.time()
                available = [p for p in self.proxies if p.is_good and (now - p.last_used) > PROXY_BACKOFF]
            if not available:
                print("[ProxyManager] No good proxies available!")
                return None
            available.sort(key=lambda p: p.avg_response)
            return random.choice(available)

proxy_manager = ProxyManager()
proxy_manager.load_good_proxies()

# ======= PART 2: Checker logic, loading usernames, checking, and saving hits =======

checked = 0
available_users = []
checker_running = False
checker_paused = False

async def check_username(username):
    proxy_obj = await proxy_manager.get_proxy()
    if not proxy_obj:
        return False

    start = time.time()
    success = False
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://kick.com/{username}", proxy=proxy_obj.proxy_str, timeout=10) as r:
                if r.status == 404:
                    success = True
    except:
        success = False

    response_time = time.time() - start
    proxy_obj.update(response_time, success)
    return success

def load_user_list(filename):
    if not os.path.exists(filename):
        return []
    with open(filename, "r") as f:
        return [line.strip() for line in f if line.strip()]

def save_hit(username):
    with open(HITS_FILE, "a") as f:
        f.write(username + "\n")

async def generate_pronounceable_4():
    # Simplified basic pronounceable 4-char generator: consonant-vowel-consonant-vowel
    consonants = "bcdfghjklmnpqrstvwxyz"
    vowels = "aeiou"
    return ''.join([
        random.choice(consonants),
        random.choice(vowels),
        random.choice(consonants),
        random.choice(vowels),
    ])

async def checker_loop():
    global checked, available_users, checker_running, checker_paused

    while checker_running:
        if checker_paused:
            await asyncio.sleep(2)
            continue

        # Load users.txt first
        users = load_user_list(USERS_FILE)
        if not users:
            # fallback: generate usernames from themes.txt by combining themes with random suffix
            themes = load_user_list(THEMES_FILE)
            if not themes:
                # generate pronounceable 4-letter usernames as fallback
                users = [await generate_pronounceable_4() for _ in range(CHECK_LIMIT)]
            else:
                # generate themed usernames (theme + 2 random letters)
                users = []
                for theme in themes:
                    for _ in range(3):  # try 3 variants per theme
                        suffix = ''.join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=2))
                        users.append(theme + suffix)
                        if len(users) >= CHECK_LIMIT:
                            break
                    if len(users) >= CHECK_LIMIT:
                        break

        for username in users:
            if not checker_running or checker_paused:
                break

            checked += 1
            success = await check_username(username)
            if success:
                available_users.append(username)
                save_hit(username)
                channel = bot.get_channel(DISCORD_CHANNEL_ID)
                if channel:
                    try:
                        await channel.send(f"✅ Available: `{username}`")
                    except Exception as e:
                        print(f"[Discord] Send message failed: {e}")

        # After checking this batch, save and refill proxies if needed
        await proxy_manager.refill_proxies()
        await asyncio.sleep(1)  # small delay before next batch

# ======= PART 3: Discord bot commands, stats, start/pause/resume =======

@bot.command()
async def start(ctx):
    global checker_running, checker_task, checker_paused
    if checker_running:
        await ctx.send("Checker already running.")
        return
    checker_running = True
    checker_paused = False
    checker_task = asyncio.create_task(checker_loop())
    await ctx.send("Checker started.")

@bot.command()
async def pause(ctx):
    global checker_paused
    if not checker_running:
        await ctx.send("Checker is not running.")
        return
    if checker_paused:
        await ctx.send("Checker is already paused.")
        return
    checker_paused = True
    await ctx.send("Checker paused.")

@bot.command()
async def resume(ctx):
    global checker_paused
    if not checker_running:
        await ctx.send("Checker is not running.")
        return
    if not checker_paused:
        await ctx.send("Checker is not paused.")
        return
    checker_paused = False
    await ctx.send("Checker resumed.")

@bot.command()
async def stop(ctx):
    global checker_running, checker_task, checker_paused
    if not checker_running:
        await ctx.send("Checker is not running.")
        return
    checker_running = False
    checker_paused = False
    if checker_task:
        checker_task.cancel()
    await ctx.send("Checker stopped.")

@bot.command()
async def stats(ctx):
    total_proxies = len(proxy_manager.proxies)
    healthy_proxies = len([p for p in proxy_manager.proxies if p.is_good])
    unhealthy_proxies = total_proxies - healthy_proxies
    hit_rate = len(available_users)

    top_proxies = sorted(proxy_manager.proxies, key=lambda p: (-p.hits, p.avg_response))[:10]
    leaderboard = ""
    for i, p in enumerate(top_proxies, 1):
        leaderboard += f"{i}. Hits: {p.hits}, AvgResp: {p.avg_response:.2f}s, Health: {p.health}%\n"

    msg = (
        f"**Checker Status:**\n"
        f"Checked usernames: {checked}\n"
        f"Available users found: {hit_rate}\n"
        f"Total proxies: {total_proxies}\n"
        f"Healthy proxies: {healthy_proxies}\n"
        f"Unhealthy proxies: {unhealthy_proxies}\n"
        f"**Top 10 Proxies:**\n{leaderboard}"
    )
    await ctx.send(msg)

@bot.event
async def on_ready():
    print(f"[Discord] Logged in as {bot.user} (ID: {bot.user.id})")
    await proxy_manager.refill_proxies()

# ======= PART 4: Main bot runner =======

if __name__ == "__main__":
    try:
        bot.run(DISCORD_TOKEN)
    except KeyboardInterrupt:
        print("Bot stopped manually")
