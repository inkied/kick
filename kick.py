import os
import asyncio
import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))
WEBSHARE_API_KEY = os.getenv("WEBSHARE_API_KEY")

PROXY_USER = os.getenv("PROXY_USER")
PROXY_PASS = os.getenv("PROXY_PASS")
PROXY_HOST = os.getenv("PROXY_HOST")
PROXY_PORT = int(os.getenv("PROXY_PORT"))

WORDLIST_FILES = ["Brandable.txt", "Culture.txt", "Gaming.txt", "Mythology.txt", "Nature.txt", "Philosophy.txt", "Tech.txt"]

MAX_CONCURRENT_CHECKS = 20
DISCORD_MESSAGE_DELAY = 5  # seconds delay between Discord messages to avoid spam

intents = discord.Intents.default()
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

async def load_wordlist():
    usernames = set()
    for file in WORDLIST_FILES:
        if os.path.exists(file):
            with open(file, "r", encoding="utf-8") as f:
                usernames.update(line.strip().lower() for line in f if line.strip())
    return list(usernames)

def get_proxy_url():
    return f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{PROXY_PORT}"

async def check_username(session, username):
    global available_count
    proxy_url = get_proxy_url()
    try:
        async with session.get(f"https://kick.com/api/v1/channels/{username}", proxy=proxy_url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 404:
                available_count += 1
                await send_available(username)
            # 200 means taken, do nothing
    except Exception:
        # silently ignore errors or you can log if you want
        pass

async def send_available(username):
    channel = bot.get_channel(DISCORD_CHANNEL_ID)
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
    embed = discord.Embed(
        title="Checker Progress",
        description=f"Checked {checked_count}/{len(wordlist)} usernames.",
        color=discord.Color.blue(),
        timestamp=datetime.utcnow()
    )
    embed.set_footer(text="By Kick")
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
            await asyncio.sleep(1)  # small delay to keep proxies healthy
    checking = False

@bot.command()
async def start(ctx):
    global checking, check_task, checked_count, available_count, current_index
    if checking:
        await ctx.send("Checker is already running.")
        return
    # If finished previously, reset to start fresh or resume from current_index
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

@bot.event
async def on_ready():
    global wordlist
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    wordlist = await load_wordlist()
    print(f"Loaded {len(wordlist)} usernames from wordlists.")
    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    if channel:
        await channel.send("Checker bot is online and ready! Use `/start` to begin.")

if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)
