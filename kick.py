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
PROXY_BACKOFF = 10  # sec cooldown between proxy uses
USERS_FILE = "users.txt"
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

checked = 0
available_users = []
checker_running = False
checker_task = None

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

async def checker_loop():
    global checked, available_users, checker_running
    while checker_running:
        if not os.path.exists(USERS_FILE):
            await asyncio.sleep(5)
            continue
        with open(USERS_FILE, "r") as f:
            users = [u.strip() for u in f if u.strip()]
        if not users:
            print("[Checker] No users to check, generating more...")
            # Add your username generation logic here if needed
            await asyncio.sleep(5)
            continue

        batch = users[:CHECK_LIMIT]
        print(f"[Checker] Checking batch of {len(batch)} usernames...")
        for username in batch:
            if not checker_running:
                break
            is_available = await check_username(username)
            checked += 1
            if is_available:
                available_users.append(username)
                with open(HITS_FILE, "a") as f:
                    f.write(username + "\n")
                print(f"[Hit] {username} is available!")
            await asyncio.sleep(random.uniform(0.3, 1.2))

        # Remove checked users from users.txt
        with open(USERS_FILE, "w") as f:
            f.writelines(u + "\n" for u in users[CHECK_LIMIT:])

        # Refill proxies after batch
        await proxy_manager.refill_proxies()

@bot.command()
async def kickstart(ctx):
    global checker_running, checker_task
    if checker_running:
        await ctx.send("Checker is already running.")
        return
    checker_running = True
    checker_task = asyncio.create_task(checker_loop())
    await ctx.send("Checker started.")

@bot.command()
async def kickstop(ctx):
    global checker_running, checker_task
    if not checker_running:
        await ctx.send("Checker is not running.")
        return
    checker_running = False
    if checker_task:
        checker_task.cancel()
        checker_task = None
    await ctx.send("Checker stopped.")

@bot.command()
async def kickstatus(ctx):
    global checked, available_users
    total_proxies = len(proxy_manager.proxies)
    healthy_proxies = sum(1 for p in proxy_manager.proxies if p.is_good)
    unhealthy_proxies = total_proxies - healthy_proxies

    top_proxies = sorted(proxy_manager.proxies, key=lambda p: (-p.hits, p.avg_response))[:10]

    hits_count = len(available_users)

    status_msg = (
        f"**Checker Status:**\n"
        f"Checked usernames: {checked}\n"
        f"Available users found: {hits_count}\n"
        f"Total proxies: {total_proxies}\n"
        f"Healthy proxies: {healthy_proxies}\n"
        f"Unhealthy proxies: {unhealthy_proxies}\n"
        f"**Top 10 Proxies:**\n"
    )
    for i, proxy in enumerate(top_proxies, 1):
        status_msg += (
            f"{i}. {proxy.proxy_str} | Hits: {proxy.hits} | "
            f"Health: {proxy.health}% | Avg Resp: {proxy.avg_response:.2f}s\n"
        )

    await ctx.send(status_msg)

bot.run(DISCORD_TOKEN)
