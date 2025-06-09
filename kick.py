import asyncio
import aiohttp
import discord
from discord.ext import commands
import logging
import random
import os

# -------- CONFIG FROM ENVIRONMENT VARIABLES --------
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
KICK_LOGS_CHANNEL_ID = int(os.getenv("KICK_LOGS_CHANNEL_ID", "1381571071542558761"))
PROXY_USER = os.getenv("PROXY_USER")
PROXY_PASS = os.getenv("PROXY_PASS")
WEBSHARE_KEY = os.getenv("WEBSHARE_KEY")
USERNAME_FILE = os.getenv("USERNAME_FILE", "usernames.txt")
PROXY_FILE = os.getenv("PROXY_FILE", "proxies.txt")
HITS_FILE = os.getenv("HITS_FILE", "hits.txt")
DEBUG_LOG = os.getenv("DEBUG_LOG", "True").lower() == "true"

# -------- SETUP LOGGING --------
logging.basicConfig(level=logging.DEBUG if DEBUG_LOG else logging.INFO)

# -------- GLOBALS --------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=".", intents=intents)

checker_task = None
checker = None

class ProxyManager:
    def __init__(self):
        self.proxies = []
        self.valid_proxies = []
        self.lock = asyncio.Lock()

    async def fetch_new_proxies(self):
        url = "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&limit=100"
        headers = {"Authorization": f"Token {WEBSHARE_KEY}"}
        timeout = aiohttp.ClientTimeout(total=10)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as r:
                    if r.status != 200:
                        logging.error(f"Proxy fetch failed with status: {r.status}")
                        return []

                    data = await r.json()
                    proxies_list = []

                    for proxy_data in data.get("results", []):
                        ip = proxy_data.get("proxy_address")
                        port = proxy_data.get("port") or proxy_data.get("proxy_port")
                        if ip and port:
                            proxy_url = f"http://{PROXY_USER}:{PROXY_PASS}@{ip}:{port}"
                            proxies_list.append(proxy_url)

                    logging.debug(f"Fetched {len(proxies_list)} proxies.")
                    return proxies_list

        except Exception as e:
            logging.error(f"Exception while fetching proxies: {e}")
            return []

    async def load_proxies_from_file(self):
        try:
            async with self.lock:
                with open(PROXY_FILE, "r") as f:
                    lines = [line.strip() for line in f if line.strip()]
                self.proxies = lines
                logging.info(f"Loaded {len(lines)} proxies from {PROXY_FILE}")
        except FileNotFoundError:
            logging.warning(f"{PROXY_FILE} not found. No proxies loaded.")

    async def validate_proxy(self, proxy):
        test_url = "https://kick.com"
        timeout = aiohttp.ClientTimeout(total=5)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(test_url, proxy=proxy) as r:
                    return r.status == 200
        except:
            return False

    async def validate_proxies(self):
        valid = []
        async with self.lock:
            tasks = [self.validate_proxy(p) for p in self.proxies]
            results = await asyncio.gather(*tasks)
            for p, ok in zip(self.proxies, results):
                if ok:
                    valid.append(p)
            self.valid_proxies = valid
            logging.info(f"Validated proxies: {len(valid)} good out of {len(self.proxies)}")

    async def refresh_proxies(self):
        new_proxies = await self.fetch_new_proxies()
        if new_proxies:
            async with self.lock:
                self.proxies = new_proxies
            await self.validate_proxies()
            with open(PROXY_FILE, "w") as f:
                for p in new_proxies:
                    f.write(p + "\n")
        else:
            await self.load_proxies_from_file()
            await self.validate_proxies()

    def get_proxy(self):
        if not self.valid_proxies:
            return None
        return random.choice(self.valid_proxies)

proxy_manager = ProxyManager()

_save_hit_lock = asyncio.Lock()

async def save_hit(username: str):
    async with _save_hit_lock:
        with open(HITS_FILE, "a") as f:
            f.write(username + "\n")

class KickUsernameChecker:
    def __init__(self, proxy_manager):
        self.proxy_manager = proxy_manager
        self.running = False
        self.paused = False
        self.usernames = []
        self.username_index = 0
        self.load_usernames()

    def load_usernames(self):
        try:
            with open(USERNAME_FILE, "r") as f:
                self.usernames = [line.strip() for line in f if line.strip()]
            logging.info(f"Loaded {len(self.usernames)} usernames from {USERNAME_FILE}")
        except FileNotFoundError:
            logging.error(f"{USERNAME_FILE} not found. No usernames loaded.")

    async def check_username(self, username, proxy):
        url = f"https://kick.com/api/username/check?username={username}"  # Adjust to real API if needed
        timeout = aiohttp.ClientTimeout(total=7)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, proxy=proxy) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        available = data.get("available", False)
                        if DEBUG_LOG:
                            logging.debug(f"Checked {username} with proxy {proxy}: {available}")
                        return available
                    else:
                        if DEBUG_LOG:
                            logging.debug(f"Failed to check {username}: HTTP {resp.status}")
                        return False
        except Exception as e:
            if DEBUG_LOG:
                logging.debug(f"Exception checking {username} with proxy {proxy}: {e}")
            return False

    async def start(self):
        self.running = True
        self.paused = False
        logging.info("KickUsernameChecker started.")
        while self.running and self.username_index < len(self.usernames):
            if self.paused:
                await asyncio.sleep(1)
                continue

            username = self.usernames[self.username_index]
            proxy = self.proxy_manager.get_proxy()

            if proxy is None:
                logging.warning("No valid proxies available, waiting for refresh...")
                await asyncio.sleep(5)
                continue

            available = await self.check_username(username, proxy)
            if available:
                await save_hit(username)
                await send_kick_log(f"Username available: `{username}`")
            self.username_index += 1
            await asyncio.sleep(random.uniform(0.4, 1.2))  # Rate limit between checks

        logging.info("KickUsernameChecker stopped or finished all usernames.")

    async def stop(self):
        self.running = False
        logging.info("KickUsernameChecker stopping.")

async def send_kick_log(message: str):
    channel = bot.get_channel(KICK_LOGS_CHANNEL_ID)
    if channel:
        try:
            await channel.send(message)
        except Exception as e:
            logging.error(f"Failed to send log message: {e}")
    else:
        logging.warning("Kick logs channel not found.")

async def run_checker_loop():
    global checker_task, checker
    while True:
        try:
            await proxy_manager.refresh_proxies()
            await send_kick_log("Proxies refreshed and validated.")

            if checker is None:
                checker = KickUsernameChecker(proxy_manager)

            await checker.start()
            await send_kick_log("Checker stopped. Restarting in 5 seconds...")
            checker = None
            await asyncio.sleep(5)

        except asyncio.CancelledError:
            await send_kick_log("Checker task cancelled.")
            break
        except Exception as e:
            await send_kick_log(f"Checker crashed: {e}. Restarting in 5 seconds...")
            checker = None
            await asyncio.sleep(5)

@bot.command()
async def kickstart(ctx):
    global checker_task
    if checker_task and not checker_task.done():
        await ctx.send("Checker already running!")
        return
    await ctx.send("Starting Kick checker...")
    checker_task = bot.loop.create_task(run_checker_loop())

@bot.command()
async def kickstop(ctx):
    global checker_task, checker
    if checker_task:
        if checker:
            await checker.stop()
        checker_task.cancel()
        checker_task = None
        checker = None
        await ctx.send("Checker stopped.")
        await send_kick_log("Checker stopped by command.")
    else:
        await ctx.send("Checker is not running.")

@bot.event
async def on_ready():
    logging.info(f"Bot logged in as {bot.user} (ID: {bot.user.id})")
    # Optional: Auto-start checker on bot start
    # await kickstart(None)

if __name__ == "__main__":
    bot.run(TOKEN)

