import os
import asyncio
import aiohttp
import random
import time
from discord.ext import commands
from dotenv import load_dotenv
from datetime import datetime
import signal

# Load env vars
load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHECKER_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))
KICK_LOGS_CHANNEL_ID = int(os.getenv("KICK_LOGS_CHANNEL_ID"))
USERNAME_STATUS_CHANNEL_ID = int(os.getenv("USERNAME_STATUS_CHANNEL_ID"))
PROXY_DASHBOARD_CHANNEL_ID = int(os.getenv("PROXY_DASHBOARD_CHANNEL_ID"))
USERNAME_FILE = os.getenv("USERNAME_FILE", "usernames.txt")
HITS_FILE = os.getenv("HITS_FILE", "hits.txt")
DEBUG_LOG = os.getenv("DEBUG_LOG", "debug.log")

WEBSHARE_API = os.getenv("WEBSHARE_API")
PROXY_USER = os.getenv("PROXY_USER")
PROXY_PASS = os.getenv("PROXY_PASS")
PROXY_HOST = os.getenv("PROXY_HOST")
PROXY_PORT = os.getenv("PROXY_PORT", "10000")

intents = commands.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=".", intents=intents)

class ProxyFailureException(Exception):
    pass

class ProxyStats:
    def __init__(self):
        self.total_checks = 0
        self.successes = 0
        self.failures = 0
        self.response_times = []
        self.last_used = 0
        self.cooldown_until = 0

    def record_success(self, resp_time):
        self.total_checks += 1
        self.successes += 1
        self.response_times.append(resp_time)
        if len(self.response_times) > 100:
            self.response_times.pop(0)
        self.last_used = time.time()

    def record_failure(self):
        self.total_checks += 1
        self.failures += 1
        self.last_used = time.time()
        if self.failures >= 5:
            self.cooldown_until = time.time() + 120

    def is_on_cooldown(self):
        return time.time() < self.cooldown_until

    def health_score(self):
        if self.total_checks == 0:
            return 1.0
        return max(0.0, (self.successes / self.total_checks) - 0.1 * self.failures)

    def avg_response_time(self):
        if not self.response_times:
            return 1.0
        return sum(self.response_times) / len(self.response_times)


class ProxyManager:
    def __init__(self):
        self.proxies = []
        self.proxy_stats = {}

    async def fetch_proxies(self):
        url = f"https://proxy.webshare.io/api/v2/proxy/list/download/{WEBSHARE_API}/?mode=txt"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                txt = await resp.text()
                self.proxies = list(set(txt.strip().splitlines()))
                for proxy in self.proxies:
                    if proxy not in self.proxy_stats:
                        self.proxy_stats[proxy] = ProxyStats()

    def get_proxy(self):
        sorted_proxies = sorted(
            [p for p in self.proxies if not self.proxy_stats[p].is_on_cooldown()],
            key=lambda p: (self.proxy_stats[p].avg_response_time(), -self.proxy_stats[p].health_score())
        )
        if sorted_proxies:
            return random.choice(sorted_proxies[:10])
        return None

    async def validate_proxies(self):
        self.proxies = [p for p in self.proxies if self.proxy_stats[p].health_score() > 0.3]

    def get_top_proxies(self, count=10):
        return sorted(
            self.proxy_stats.items(),
            key=lambda x: (-x[1].health_score(), x[1].avg_response_time())
        )[:count]

    def get_bad_proxies(self, count=10):
        return sorted(
            self.proxy_stats.items(),
            key=lambda x: (x[1].health_score(), -x[1].avg_response_time())
        )[:count]


async def send_log(message):
    channel = bot.get_channel(KICK_LOGS_CHANNEL_ID)
    if channel:
        await channel.send(f"[{datetime.utcnow().strftime('%H:%M:%S')}] {message}")
    with open(DEBUG_LOG, "a") as f:
        f.write(f"[{datetime.utcnow().isoformat()}] {message}\n")

class ProxyFailureException(Exception):
    pass

class KickUsernameChecker:
    def __init__(self, proxy_manager, usernames, batch_size=100):
        # ... your existing __init__ ...
        self.last_stats_sent = 0  # Timestamp of last sent stats message

    # ... other methods unchanged ...

   async def run(self):
    self.is_running = True
    total_usernames = len(self.usernames)

    # Start progress ETA logger task
    progress_task = asyncio.create_task(self.log_progress_eta(interval_seconds=300))

    await self.update_status("Waiting For Proxy From Dashboard...")

    # Your existing checking loop here...

    # At the end of run:
    self.is_running = False

    # Cancel progress logger task if still running
    if not progress_task.done():
        progress_task.cancel()
        try:
            await progress_task
        except asyncio.CancelledError:
            pass

    await self.update_status("✅ Checker finished or stopped.")

        while self.is_running and self.checked_count < total_usernames:
            proxy = self.proxy_manager.get_proxy()
            if proxy is None:
                await asyncio.sleep(5)
                continue

            stats = self.proxy_manager.proxy_stats.get(proxy)
            proxy_health = stats.health_score() if stats else 1.0
            avg_resp = stats.avg_response_time() if stats else 0
            cooldown = stats.is_on_cooldown() if stats else False

            status_msg = (
                f"Current Username: `{self.current_username or 'N/A'}`\n"
                f"Checked: {self.checked_count}/{total_usernames}\n"
                f"Available Found: {self.available_count}\n"
                f"Failed Proxies: {self.failed_proxy_count}\n"
                f"Other Failures: {self.failed_other_count}\n"
                f"Using Proxy: {proxy}\n"
                f"Proxy Health: {proxy_health:.2f}\n"
                f"Avg Proxy Resp Time: {avg_resp:.2f}s\n"
                f"Proxy Cooldown: {'Yes' if cooldown else 'No'}"
            )   f"Estimated Time Remaining: {eta_seconds // 60}m {eta_seconds % 60}s"

            now = time.time()
            if now - self.last_stats_sent > 30:  # only update every 30 seconds
                await self.update_status(status_msg)
                await self.send_log(f"📊 Stats update:\n{status_msg}")
                self.last_stats_sent = now

            eta_seconds = int((total_usernames - self.checked_count) * (avg_resp + 0.4))
            eta_msg = f"ETA: {eta_seconds // 60}m {eta_seconds % 60}s"
            await self.send_checker_channel_message(eta_msg)

            batch_end = min(self.checked_count + self.batch_size, total_usernames)
            for username in self.usernames[self.checked_count:batch_end]:
                self.current_username = username
                start_time = time.time()
                try:
                    is_available = await self.check_username(username, proxy)
                    latency = time.time() - start_time
                    self.checked_count += 1
                    if is_available:
                        self.available_count += 1
                        await self.send_checker_channel_message(f"✅ `{username}` is available!")
                        with open(HITS_FILE, 'a') as hitlog:
                            hitlog.write(f"{username} - Available @ {time.ctime()}\n")
                    if stats:
                        stats.record_success(latency)
                except ProxyFailureException:
                    self.failed_proxy_count += 1
                    await self.send_log(f"⚠️ Proxy failure on `{proxy}` for `{username}`")
                    if stats:
                        stats.record_failure()
                    break  # drop this proxy
                except Exception as e:
                    self.failed_other_count += 1
                    await self.send_log(f"❌ Error checking `{username}`: {e}")
                    if stats:
                        stats.record_failure()

            await self.proxy_manager.validate_proxies()

        self.is_running = False
        await self.update_status("✅ Checker finished or stopped.")
        await self.send_log("✅ Checker finished or stopped.")

@bot.command(name=".stats")
async def stats_command(ctx):
    if not checker:
        await ctx.send("❌ Checker is not running.")
        return

    total = checker.checked_count
    avail = checker.available_count
    fails = checker.failed_other_count
    proxy_fails = checker.failed_proxy_count
    current = checker.current_username or "N/A"
    working = len(checker.working_proxies)

    await ctx.send(
        f"📊 **Username Checker Stats**\n"
        f"• Current: `{current}`\n"
        f"• Checked: `{total}`\n"
        f"• Available: `{avail}`\n"
        f"• Proxy Fails: `{proxy_fails}`\n"
        f"• Other Errors: `{fails}`\n"
        f"• Working Proxies: `{working}`"
    )

@bot.command(name=".proxies")
async def proxies_command(ctx):
    sorted_proxies = sorted(
        proxy_manager.proxy_stats.items(),
        key=lambda item: (
            -item[1].successes,
            item[1].avg_response_time() or float('inf')
        )
    )
    leaderboard = "\n".join(
        f"`{proxy}` • Health: {stat.health_score():.2f} • AvgRT: {stat.avg_response_time():.2f}s"
        for proxy, stat in sorted_proxies[:10]
    )
    await ctx.send(f"🏁 **Top Proxies**:\n{leaderboard}")

@bot.command(name=".stats")
async def stats(ctx):
    if not checker or not checker.is_running:
        await checker.send_log("Checker is not currently running.")
        return

    total_usernames = len(checker.usernames)
    message = (
        f"📊 **Checker Stats** 📊\n"
        f"• Current Username: `{checker.current_username or 'N/A'}`\n"
        f"• Checked: `{checker.checked_count}/{total_usernames}`\n"
        f"• Available: `{checker.available_count}`\n"
        f"• Failed Proxies: `{checker.failed_proxy_count}`\n"
        f"• Other Failures: `{checker.failed_other_count}`\n"
        f"• Working Proxies: `{checker.proxy_manager.count_working_proxies()}`"
    )
    await checker.send_log(message)

@bot.command(name=".gen")
async def gen_command(ctx):
    import random
    import string

    vowels = 'aeiou'
    consonants = ''.join(set(string.ascii_lowercase) - set(vowels))
    
    def gen_name():
        return ''.join(random.choice(consonants if i % 2 == 0 else vowels) for i in range(4))

    usernames = [gen_name() for _ in range(5)]
    await ctx.send("🎲 Generated Usernames:\n" + "\n".join(usernames))

@bot.command(name=".check")
async def check_command(ctx):
    if not checker or not checker.is_running:
        await ctx.send("⚠️ Checker not running.")
        return
    await ctx.send("✅ Checker is live and processing usernames.")

from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

bot = commands.Bot(command_prefix=".", intents=discord.Intents.all())

proxy_manager = None
checker = None

def graceful_shutdown():
    loop = asyncio.get_event_loop()
    if checker and checker.is_running:
        loop.create_task(checker.update_status("🔴 Gracefully shutting down..."))
    loop.stop()

signal.signal(signal.SIGINT, lambda s, f: graceful_shutdown())
signal.signal(signal.SIGTERM, lambda s, f: graceful_shutdown())

@bot.event
async def on_ready():
    global proxy_manager, checker

    print(f"[+] Logged in as {bot.user.name}")

    # Load usernames
    if not os.path.exists(USERNAME_FILE):
        print(f"[!] Username file {USERNAME_FILE} not found.")
        return

    with open(USERNAME_FILE, 'r') as f:
        usernames = [u.strip() for u in f if u.strip()]
    
    if not usernames:
        print("[!] No usernames to check.")
        return

    # Init proxy manager
    proxy_manager = ProxyManager(
        webshare_api_key=WEBSHARE_API,
        proxy_auth=(PROXY_USER, PROXY_PASS),
        proxy_host=PROXY_HOST,
        proxy_port=PROXY_PORT
    )
    await proxy_manager.refresh_proxies()

    # Start checker
    checker = KickUsernameChecker(proxy_manager, usernames)
    await checker.run()

    # Background: Periodic proxy validation
    async def validate_loop():
        while True:
            await asyncio.sleep(300)  # every 5 minutes
            await proxy_manager.validate_proxies()
    bot.loop.create_task(validate_loop())

bot.run(DISCORD_BOT_TOKEN)
