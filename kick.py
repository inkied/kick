import discord
from discord.ext import commands
import asyncio
import aiohttp
import time
import random

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# ======= Config =======
USERS_TO_CHECK = 100  # Adjust this to your full username list length
MAX_ATTEMPTS_PER_USERNAME = 5
MIN_PROXIES = 10
MAX_PROXIES = 50

# Control variables for running/stopping
checker_task = None
stop_flag = False

# Dummy proxy fetch function — replace with your real proxy API or file logic
async def fetch_proxies():
    await asyncio.sleep(1)
    return [
        "http://proxy1:port",
        "http://proxy2:port",
        # Add your proxies here
    ]

# ======= Proxy management =======
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

    async def load_proxies(self):
        fresh_proxy_strs = await fetch_proxies()
        async with self.lock:
            for p_str in fresh_proxy_strs:
                if not any(p.proxy_str == p_str for p in self.proxies):
                    self.proxies.append(Proxy(p_str))
        print(f"[ProxyManager] Loaded {len(fresh_proxy_strs)} fresh proxies.")

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

            # Prioritize by life_time and hit_rate
            candidates.sort(key=lambda p: (p.life_time, p.hit_rate), reverse=True)
            return candidates[0]

    async def restock_proxies(self):
        async with self.lock:
            if len(self.proxies) >= self.min_proxies:
                return
            print("[ProxyManager] Restocking proxies...")
            fresh_proxy_strs = await fetch_proxies()
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

# ======= Username checker =======
users_checked = 0
hits = []  # list of dicts: {'username', 'timestamp', 'tries'}

# Sample Kick username generator (replace with your real one)
def generate_usernames(n):
    base_words = ["gamer", "tech", "stream", "pro", "kick", "live", "zone", "chat", "play", "cast"]
    usernames = []
    while len(usernames) < n:
        word = random.choice(base_words)
        if any(c.isdigit() or c == '_' for c in word):
            continue
        usernames.append(word + str(random.randint(1,999)))
    return usernames

async def check_username(username):
    global users_checked
    tries = 0
    while tries < MAX_ATTEMPTS_PER_USERNAME:
        if stop_flag:
            break
        tries += 1
        proxy = await proxy_manager.get_best_proxy()
        proxy_url = proxy.proxy_str
        start = time.time()
        success = False
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://kick.com/{username}", proxy=proxy_url, timeout=10) as resp:
                    if resp.status == 404:  # Username available
                        success = True
        except Exception:
            pass
        response_time = time.time() - start
        await proxy_manager.update_proxy_stats(proxy_url, response_time, success)
        if success:
            hits.append({'username': username, 'timestamp': time.time(), 'tries': tries})
            break
    users_checked += 1
    return success

async def run_checker():
    global users_checked, stop_flag
    users_checked = 0
    hits.clear()
    usernames = generate_usernames(USERS_TO_CHECK)
    stop_flag = False
    for username in usernames:
        if stop_flag:
            print("[Checker] Stopped by user.")
            break
        await check_username(username)
        await asyncio.sleep(0.1)  # small delay to prevent hammering

# ======= Discord commands =======
@bot.event
async def on_ready():
    print(f"[Discord] Logged in as {bot.user} (ID: {bot.user.id})")
    await proxy_manager.load_proxies()

@bot.slash_command(name="kickstart", description="Start the Kick username checker")
async def kickstart(ctx):
    global checker_task, stop_flag
    if checker_task and not checker_task.done():
        await ctx.respond("Checker is already running.")
        return
    stop_flag = False
    await ctx.respond("Starting Kick username checker...")
    checker_task = asyncio.create_task(run_checker())

@bot.slash_command(name="kickstop", description="Stop the Kick username checker")
async def kickstop(ctx):
    global stop_flag
    if not checker_task or checker_task.done():
        await ctx.respond("Checker is not running.")
        return
    stop_flag = True
    await ctx.respond("Stopping Kick username checker...")

@bot.slash_command(name="kickstatus", description="Show current status of Kick checker")
async def kickstatus(ctx):
    fast_count = slow_count = bad_count = 0
    total_requests = total_response = 0
    async with proxy_manager.lock:
        for p in proxy_manager.proxies:
            total_requests += p.requests
            total_response += p.total_response_time
            if p.status == "Fast":
                fast_count += 1
            elif p.status == "Slow":
                slow_count += 1
            elif p.status == "Bad":
                bad_count += 1

    avg_response_time = (total_response / total_requests) if total_requests else 0
    remaining = max(USERS_TO_CHECK - users_checked, 0)
    est_seconds_left = remaining * (avg_response_time + 0.1)  # rough estimate including delay
    est_minutes, est_seconds = divmod(est_seconds_left, 60)

    hits_text = ""
    for hit in hits[-10:]:  # Show last 10 hits
        ts = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(hit['timestamp']))
        hits_text += f"• {hit['username']} | Available at {ts} | Attempts: {hit['tries']}\n"

    await ctx.respond(
        f"**Kick Username Checker Status**\n"
        f"Users checked: {users_checked} of {USERS_TO_CHECK}\n"
        f"Hits found: {len(hits)}\n"
        f"Recent hits:\n{hits_text or 'No hits yet.'}\n"
        f"Proxy health:\n"
        f"Fast: {fast_count}\n"
        f"Slow: {slow_count}\n"
        f"Bad: {bad_count}\n"
        f"Average proxy response time: {avg_response_time:.2f} sec\n"
        f"Estimated time left: {int(est_minutes)}m {int(est_seconds)}s"
    )

# ======= Run bot =======
if __name__ == "__main__":
    TOKEN = "YOUR_DISCORD_BOT_TOKEN_HERE"
    bot.run(TOKEN)
