import asyncio
import aiohttp
import discord
from discord.ext import commands
import logging
import random
import os
from datetime import datetime

# -------- CONFIG FROM ENVIRONMENT VARIABLES --------
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
KICK_CATEGORY_ID = int(os.getenv("KICK_CATEGORY_ID", "0"))
KICK_LOGS_CHANNEL_ID = int(os.getenv("KICK_LOGS_CHANNEL_ID", "1381571071542558761"))
PROXY_DASHBOARD_CHANNEL_ID = int(os.getenv("PROXY_DASHBOARD_CHANNEL_ID", "0"))
PROXY_USER = os.getenv("PROXY_USER")
PROXY_PASS = os.getenv("PROXY_PASS")
WEBSHARE_KEY = os.getenv("WEBSHARE_KEY")
USERNAME_FILE = os.getenv("USERNAME_FILE", "usernames.txt")
PROXY_FILE = os.getenv("PROXY_FILE", "proxies.txt")
HITS_FILE = os.getenv("HITS_FILE", "hits.txt")
DEBUG_LOG = os.getenv("DEBUG_LOG", "True").lower() == "true"

async def fetch_and_save_proxies():
    url = "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&limit=100"
    headers = {"Authorization": f"Token {WEBSHARE_KEY}"}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                print(f"Failed to fetch proxies, status: {resp.status}")
                return
            data = await resp.json()
            proxies_list = []
            for proxy_data in data.get("results", []):
                ip = proxy_data.get("proxy_address")
                port = proxy_data.get("port") or proxy_data.get("proxy_port")
                if ip and port:
                    proxy_url = f"http://{PROXY_USER}:{PROXY_PASS}@{ip}:{port}"
                    proxies_list.append(proxy_url)
            with open(PROXY_FILE, "w") as f:
                for proxy in proxies_list:
                    f.write(proxy + "\n")
            print(f"Saved {len(proxies_list)} proxies to {PROXY_FILE}")

asyncio.run(fetch_and_save_proxies())

# -------- SETUP LOGGING --------
logging.basicConfig(level=logging.DEBUG if DEBUG_LOG else logging.INFO)
logger = logging.getLogger("kick_checker")

# -------- DISCORD SETUP --------
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
                        logger.error(f"Proxy fetch failed with status: {r.status}")
                        return []

                    data = await r.json()
                    proxies_list = []

                    for proxy_data in data.get("results", []):
                        ip = proxy_data.get("proxy_address")
                        port = proxy_data.get("port") or proxy_data.get("proxy_port")
                        if ip and port:
                            proxy_url = f"http://{PROXY_USER}:{PROXY_PASS}@{ip}:{port}"
                            proxies_list.append(proxy_url)

                    logger.debug(f"Fetched {len(proxies_list)} proxies.")
                    return proxies_list

        except Exception as e:
            logger.error(f"Exception while fetching proxies: {e}")
            return []

    async def load_proxies_from_file(self):
        try:
            async with self.lock:
                with open(PROXY_FILE, "r") as f:
                    lines = [line.strip() for line in f if line.strip()]
                self.proxies = lines
                logger.info(f"Loaded {len(lines)} proxies from {PROXY_FILE}")
        except FileNotFoundError:
            logger.warning(f"{PROXY_FILE} not found. No proxies loaded.")

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
            logger.info(f"Validated proxies: {len(valid)} good out of {len(self.proxies)}")

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

async def send_kick_log(message: str):
    channel = bot.get_channel(KICK_LOGS_CHANNEL_ID)
    if channel:
        try:
            await channel.send(message)
        except Exception as e:
            logger.error(f"Failed to send log message: {e}")


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
            logger.info(f"Loaded {len(self.usernames)} usernames from {USERNAME_FILE}")
        except FileNotFoundError:
            logger.error(f"{USERNAME_FILE} not found. No usernames loaded.")

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
                            logger.debug(f"Checked {username} with proxy {proxy}: {available}")
                        return available
                    else:
                        if DEBUG_LOG:
                            logger.debug(f"Failed to check {username}: HTTP {resp.status}")
                        return False
        except Exception as e:
            if DEBUG_LOG:
                logger.debug(f"Exception checking {username} with proxy {proxy}: {e}")
            return False

    async def start(self):
        self.running = True
        self.paused = False
        logger.info("KickUsernameChecker started.")
        while self.running and self.username_index < len(self.usernames):
            if self.paused:
                await asyncio.sleep(1)
                continue

            username = self.usernames[self.username_index]
            proxy = self.proxy_manager.get_proxy()

            if proxy is None:
                logger.warning("No valid proxies available, waiting for refresh...")
                await asyncio.sleep(5)
                continue

            available = await self.check_username(username, proxy)
            if available:
                await save_hit(username)
                await send_kick_log(f"Username available: `{username}`")
            self.username_index += 1
            await asyncio.sleep(random.uniform(0.5, 1.5))

        logger.info("KickUsernameChecker finished or stopped.")

    async def stop(self):
        self.running = False
        logger.info("KickUsernameChecker stopped.")

    def pause(self):
        self.paused = True
        logger.info("KickUsernameChecker paused.")

    def resume(self):
        self.paused = False
        logger.info("KickUsernameChecker resumed.")

    async def start(self):
        self.running = True
        self.paused = False
        logger.info("KickUsernameChecker started.")
        while self.running and self.username_index < len(self.usernames):
            if self.paused:
                await asyncio.sleep(1)
                continue

            username = self.usernames[self.username_index]
            proxy = self.proxy_manager.get_proxy()

            if proxy is None:
                logger.warning("No valid proxies available, waiting for refresh...")
                await asyncio.sleep(5)
                continue

            available = await self.check_username(username, proxy)
            if available:
                await save_hit(username)
                await send_kick_log(f"Username available: `{username}`")

            self.username_index += 1
            await asyncio.sleep(random.uniform(0.4, 1.2))  # Adjust delay to be respectful

        logger.info("KickUsernameChecker finished or stopped.")

    def stop(self):
        self.running = False

async def send_category_start_messages():
    if KICK_CATEGORY_ID == 0:
        logger.warning("KICK_CATEGORY_ID is not set, skipping category channel messages.")
        return

    category = discord.utils.get(bot.guilds[0].categories, id=KICK_CATEGORY_ID)
    if not category:
        logger.warning("Kick category not found.")
        return

    for channel in category.channels:
        try:
            embed = discord.Embed(
                title="Checker Started",
                description=f"Checker Started in {channel.mention}",
                color=discord.Color.green(),
                timestamp=datetime.utcnow(),
            )
            await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Failed to send start message in {channel.name}: {e}")

async def send_proxy_dashboard_status(online=True):
    channel = bot.get_channel(PROXY_DASHBOARD_CHANNEL_ID)
    if not channel:
        logger.warning("Proxy Dashboard channel not found.")
        return

    status_text = "Proxy Dashboard is Online" if online else "Proxy Dashboard is Offline"
    embed = discord.Embed(
        title="Proxy Dashboard Status",
        description=status_text,
        color=discord.Color.blue() if online else discord.Color.red(),
        timestamp=datetime.utcnow(),
    )
    try:
        await channel.send(embed=embed)
    except Exception as e:
        logger.error(f"Failed to send proxy dashboard status: {e}")

async def send_logs_status():
    channel = bot.get_channel(KICK_LOGS_CHANNEL_ID)
    if not channel:
        logger.warning("Kick logs channel not found.")
        return

    embed = discord.Embed(
        title="Logging Checker",
        description="Checker is logging activity and errors.",
        color=discord.Color.orange(),
        timestamp=datetime.utcnow(),
    )
    try:
        await channel.send(embed=embed)
    except Exception as e:
        logger.error(f"Failed to send logs status: {e}")

@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")

    # Send start messages to category channels
    await send_category_start_messages()

    # Send proxy dashboard online status
    await send_proxy_dashboard_status(online=True)

    # Send logs status
    await send_logs_status()

    # Refresh proxies at startup
    await proxy_manager.refresh_proxies()

    # Start the username checker
    global checker, checker_task
    checker = KickUsernameChecker(proxy_manager)
    if checker_task is None or checker_task.done():
        checker_task = asyncio.create_task(checker.start())

bot.run(TOKEN)
