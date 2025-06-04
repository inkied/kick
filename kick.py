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
intents.message_content = True

bot = commands.Bot(command_prefix="/", intents=intents)

proxies = []
proxy_index = 0
proxy_lock = asyncio.Lock()

wordlist = []
checked_count = 0
available_count = 0
checking = False
check_task = None
current_index = 0

proxy_response_times = []
proxy_response_lock = asyncio.Lock()

check_start_time = None

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

async def check_username(session, username):
    global available_count
    proxy = await get_next_proxy()
    start = datetime.utcnow()
    try:
        url = f"https://kick.com/api/v1/channels/{username}"
        async with session.get(url, proxy=proxy, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            elapsed = (datetime.utcnow() - start).total_seconds()
            # Track proxy response time
            async with proxy_response_lock:
                proxy_response_times.append(elapsed)
                if len(proxy_response_times) > 50:
                    proxy_response_times.pop(0)
            if resp.status == 404:
                available_count += 1
                await send_available(username)
    except Exception:
        pass

async def send_available(username):
    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    if channel:
        embed = discord.Embed(
            title=f"Check @{username}",
            description=f"Username `{username}` is available on Kick.com!",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text="By Kick")
        await channel.send(embed=embed)
        await asyncio.sleep(DISCORD_MESSAGE_DELAY)

async def send_progress():
    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    if channel:
        embed = discord.Embed(
            title="Checker Progress",
            description=f"Checked {checked_count}/{len(wordlist)} usernames.",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text="By Kick")
        await channel.send(embed=embed)

async def checker_loop():
    global checked_count, checking, current_index, check_start_time
    check_start_time = datetime.utcnow()
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
            await asyncio.sleep(1)
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
    await ctx.send(f"Checker started! Total usernames: {len(wordlist)}")
    check_task = asyncio.create_task(checker_loop())

@bot.command()
async def stop(ctx):
    global checking
    if not checking:
        await ctx.send("Checker is not running.")
        return
    checking = False
    await ctx.send("Checker stopped. You can resume with `/start`.")

@bot.command()
async def status(ctx):
    if not wordlist:
        await ctx.send("Wordlist not loaded yet.")
        return
    elapsed = (datetime.utcnow() - check_start_time).total_seconds() if check_start_time else 0
    rate = checked_count / elapsed if elapsed > 0 else 0
    remaining = len(wordlist) - checked_count
    eta_seconds = remaining / rate if rate > 0 else -1

    # Average proxy response time
    async with proxy_response_lock:
        if proxy_response_times:
            avg_response = sum(proxy_response_times) / len(proxy_response_times)
        else:
            avg_response = 0

    # Proxy health status based on avg response time
    if avg_response == 0:
        health = "No proxy data yet"
    elif avg_response < 1:
        health = "Fast"
    elif avg_response < 3:
        health = "Okay"
    else:
        health = "Slow"

    eta_str = str(timedelta(seconds=int(eta_seconds))) if eta_seconds > 0 else "Unknown"

    embed = discord.Embed(
        title="Checker Status",
        color=discord.Color.gold(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Checked", value=f"{checked_count} / {len(wordlist)}", inline=True)
    embed.add_field(name="Available Found", value=str(available_count), inline=True)
    embed.add_field(name="Proxy Health", value=health, inline=True)
    embed.add_field(name="Avg Proxy Response", value=f"{avg_response:.2f} sec", inline=True)
    embed.add_field(name="Estimated Time Left", value=eta_str, inline=True)
    await ctx.send(embed=embed)

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
        await channel.send("Checker bot is online and ready! Use `/start` to begin.")

if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)
