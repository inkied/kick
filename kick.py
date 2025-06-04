import os
import asyncio
import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))
WEBSHARE_API_KEY = os.getenv("WEBSHARE_API_KEY")

MAX_CONCURRENT_CHECKS = 20
DISCORD_MESSAGE_DELAY = 2  # seconds between Discord messages

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

proxies = []
proxy_index = 0
proxy_lock = asyncio.Lock()
proxy_response_times = {}  # Store recent proxy response times
proxy_creation_times = {}  # Store proxy creation time to estimate lifetime

wordlist = []
checked_count = 0
available_count = 0
checking = False
check_task = None
current_index = 0

async def fetch_proxies():
    url = "https://proxy.webshare.io/api/proxy/list/"
    headers = {"Authorization": f"ApiKey {WEBSHARE_API_KEY}"}
    proxies_list = []
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                for item in data.get("results", []):
                    proxy = f"http://{item['username']}:{item['password']}@{item['proxy_address']}:{item['ports']['http']}"
                    proxies_list.append(proxy)
                    # record creation time from API data if available
                    created_str = item.get('created_at') or None
                    if created_str:
                        created_dt = datetime.strptime(created_str, "%Y-%m-%dT%H:%M:%S.%fZ")
                        proxy_creation_times[proxy] = created_dt
    return proxies_list

async def load_wordlist():
    usernames = set()
    if os.path.exists("users.txt"):
        with open("users.txt", "r", encoding="utf-8") as f:
            usernames.update(line.strip().lower() for line in f if line.strip())
    return list(usernames)

async def get_next_proxy():
    global proxy_index
    async with proxy_lock:
        proxy = proxies[proxy_index]
        proxy_index = (proxy_index + 1) % len(proxies)
        return proxy

def get_proxy_speed_status():
    if not proxy_response_times:
        return "Unknown (no data)"
    avg_response = sum(proxy_response_times.values()) / len(proxy_response_times)
    if avg_response < 0.5:
        return "Fast"
    elif avg_response < 1.5:
        return "Okay"
    else:
        return "Slow"

def get_proxy_lifetime_status():
    if not proxy_creation_times:
        return "Unknown (no creation data)"
    now = datetime.utcnow()
    lifetimes = []
    for proxy, created_at in proxy_creation_times.items():
        # Webshare proxies usually have lifetime limits — estimate time left
        # Assume proxies live 7 days for example
        lifetime_days = 7
        expire_at = created_at + timedelta(days=lifetime_days)
        time_left = expire_at - now
        lifetimes.append(time_left.total_seconds())
    if not lifetimes:
        return "Unknown (no lifetime data)"
    avg_seconds_left = sum(lifetimes) / len(lifetimes)
    if avg_seconds_left <= 0:
        return "Proxies expired or about to expire"
    else:
        hours_left = avg_seconds_left / 3600
        return f"Approx. {hours_left:.1f} hours proxy lifetime remaining"

async def check_username(session, username):
    global available_count
    proxy = await get_next_proxy()
    start_time = datetime.utcnow()
    try:
        url = f"https://kick.com/api/v1/channels/{username}"
        async with session.get(url, proxy=proxy, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            elapsed = (datetime.utcnow() - start_time).total_seconds()
            proxy_response_times[proxy] = elapsed
            if resp.status == 404:
                available_count += 1
                await send_available(username)
            # 200 = taken, do nothing
    except Exception:
        proxy_response_times[proxy] = 3.0  # Timeout or failure treated as slow

async def send_available(username):
    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    if channel:
        embed = discord.Embed(
            title=f"Kick Checker",
            description=f"Username `{username}` is available on Kick.com!",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text="By Kick Checker Bot")
        await channel.send(embed=embed)
        await asyncio.sleep(DISCORD_MESSAGE_DELAY)

async def send_progress():
    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    if channel:
        embed = discord.Embed(
            title="Checker Progress",
            description=f"Checked {checked_count}/{len(wordlist)} usernames.\n"
                        f"Proxies speed: {get_proxy_speed_status()}\n"
                        f"{get_proxy_lifetime_status()}",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text="By Kick Checker Bot")
        await channel.send(embed=embed)

async def checker_loop():
    global checked_count, checking, current_index
    connector = aiohttp.TCPConnector(limit_per_host=MAX_CONCURRENT_CHECKS)
    async with aiohttp.ClientSession(connector=connector) as session:
        while checking and current_index < len(wordlist):
            batch = wordlist[current_index:current_index + MAX_CONCURRENT_CHECKS]
            tasks = [check_username(session, username) for username in batch]
            await asyncio.gather(*tasks)
            checked_count += len(batch)
            current_index += len(batch)
            if checked_count % 50 == 0 or current_index >= len(wordlist):
                await send_progress()
            await asyncio.sleep(1)  # small delay to avoid proxy stress
    checking = False

@bot.command()
async def start(ctx):
    global checking, check_task, checked_count, available_count, current_index
    if checking:
        await ctx.send("Checker is already running.")
        return
    if current_index >= len(wordlist):
        checked_count = 0
        available_count = 0
        current_index = 0
    checking = True
    check_task = asyncio.create_task(checker_loop())
    await ctx.send(f"Checker started! Total usernames: {len(wordlist)}")

@bot.command()
async def stop(ctx):
    global checking
    if not checking:
        await ctx.send("Checker is not running.")
        return
    checking = False
    await ctx.send("Checker stopped. You can resume with !start.")

@bot.command()
async def status(ctx):
    status_msg = (
        f"Checked: {checked_count}/{len(wordlist)} usernames.\n"
        f"Available: {available_count}\n"
        f"Proxies speed: {get_proxy_speed_status()}\n"
        f"{get_proxy_lifetime_status()}\n"
        f"Checker running: {'Yes' if checking else 'No'}"
    )
    await ctx.send(status_msg)

@bot.event
async def on_ready():
    global wordlist, proxies
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    proxies = await fetch_proxies()
    if not proxies:
        print("No proxies loaded! Please check your Webshare API key.")
        return
    print(f"Loaded {len(proxies)} proxies from Webshare.")
    wordlist = await load_wordlist()
    print(f"Loaded {len(wordlist)} usernames from users.txt.")
    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    if channel:
        await channel.send("Checker bot is online and ready! Use `!start` to begin.")

if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)
