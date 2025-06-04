import os
import aiohttp
import asyncio
import discord
from discord.ext import commands
from datetime import datetime

# --- CONFIGURATION ---
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "1373430943783718953"))

PROXY_USER = os.getenv("PROXY_USER", "trdwseke-rotate")
PROXY_PASS = os.getenv("PROXY_PASS", "n0vc7b0ev31y")
PROXY_PORT = os.getenv("PROXY_PORT", "80")
PROXY_FORMAT = f"http://{PROXY_USER}:{PROXY_PASS}@proxy.webshare.io:{PROXY_PORT}"

WORDLIST_FILES = [
    "Brandable.txt",
    "Culture.txt",
    "Gaming.txt",
    "Mythology.txt",
    "Nature.txt",
    "Philosophy.txt",
    "Tech.txt"
]

MAX_CONCURRENT_CHECKS = 20
PROGRESS_UPDATE_EVERY = 50
DISCORD_MESSAGE_DELAY = 5

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

wordlist = []
total_users = 0
checked_count = 0
available_count = 0
stop_checker = False
checking_task = None
lock = asyncio.Lock()

def load_wordlist():
    usernames = set()
    for filename in WORDLIST_FILES:
        if os.path.isfile(filename):
            with open(filename, "r", encoding="utf-8") as f:
                for line in f:
                    name = line.strip().lower()
                    if name and name.isalpha():
                        usernames.add(name)
    return list(usernames)

def get_proxy_url():
    return PROXY_FORMAT

async def check_username(session, username):
    proxy = get_proxy_url()
    url = f"https://kick.com/api/v1/channels/{username}"
    try:
        async with session.get(url, proxy=proxy, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 404:
                return True
            elif resp.status == 200:
                return False
            else:
                return None
    except:
        return None

async def send_discord_message(channel, username, checked, total):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    embed = discord.Embed(
        title=f"Check @{username}",
        description=f"Checked **{checked}/{total}** usernames\nTimestamp: {now}",
        color=0x1abc9c,
    )
    embed.set_footer(text="By Kick")
    await channel.send(embed=embed)
    await asyncio.sleep(DISCORD_MESSAGE_DELAY)

async def checker_loop(channel):
    global checked_count, available_count, stop_checker

    connector = aiohttp.TCPConnector(limit_per_host=MAX_CONCURRENT_CHECKS)
    async with aiohttp.ClientSession(connector=connector) as session:
        for username in wordlist:
            async with lock:
                if stop_checker:
                    break
                checked_count += 1
            is_available = await check_username(session, username)
            if is_available:
                available_count += 1
                await send_discord_message(channel, username, checked_count, total_users)

            if checked_count % PROGRESS_UPDATE_EVERY == 0 or checked_count == total_users:
                now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
                await channel.send(
                    f"Progress update: Checked **{checked_count}/{total_users}** usernames as of {now}"
                )

    if not stop_checker:
        await channel.send("✅ Checking completed!")

@bot.event
async def on_ready():
    global wordlist, total_users, checked_count, available_count
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    wordlist = load_wordlist()
    total_users = len(wordlist)
    checked_count = 0
    available_count = 0
    print(f"Loaded {total_users} usernames.")
    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    if channel:
        await channel.send("Checker ready. Use `!start` to begin checking, `!stop` to pause.")

@bot.command(name="start")
async def start_checker(ctx):
    global checking_task, stop_checker

    if ctx.channel.id != DISCORD_CHANNEL_ID:
        await ctx.send("Please use commands in the designated channel.")
        return

    if checking_task and not checking_task.done():
        await ctx.send("Checker is already running.")
        return

    stop_checker = False
    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    if not channel:
        await ctx.send("Error: Channel not found.")
        return

    await ctx.send("Starting the checker...")
    checking_task = bot.loop.create_task(checker_loop(channel))

@bot.command(name="stop")
async def stop_checker_cmd(ctx):
    global stop_checker
    if ctx.channel.id != DISCORD_CHANNEL_ID:
        await ctx.send("Please use commands in the designated channel.")
        return

    stop_checker = True
    await ctx.send("Stopping the checker...")

if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN:
        print("Error: DISCORD_BOT_TOKEN environment variable not set.")
        exit(1)
    if not DISCORD_CHANNEL_ID:
        print("Error: DISCORD_CHANNEL_ID environment variable not set or invalid.")
        exit(1)
    bot.run(DISCORD_BOT_TOKEN)
