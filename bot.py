import discord
from discord.ext import commands
import os
from music_cog import MusicCog
from dotenv import load_dotenv
import time
import logging
import sys

# Set up logging with more detailed configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('discord_bot.log')
    ]
)
logger = logging.getLogger('DiscordBot')

# Load environment variables
load_dotenv()
logger.info("Environment variables loaded")

# Bot configuration with all required intents
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True
intents.guild_messages = True

GUILD_ID = 123681420123176960  # Your guild ID

class MusicBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix='/', intents=intents)
        
    async def setup_hook(self):
        await self.add_cog(MusicCog(self))
        # Sync commands to your specific guild
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        logger.info(f"Slash commands synced to guild ID: {GUILD_ID}")

bot = MusicBot()

@bot.event
async def on_ready():
    logger.info(f'Bot is ready! Logged in as {bot.user.name} (ID: {bot.user.id})')
    logger.info(f'Connected to {len(bot.guilds)} guilds')
    for guild in bot.guilds:
        logger.info(f'Connected to guild: {guild.name} (ID: {guild.id})')
        logger.info(f'Syncing commands with guild: {guild.name} (ID: {guild.id})')
        await bot.tree.sync(guild=discord.Object(id=guild.id))

@bot.event
async def on_connect():
    logger.info("Bot connected to Discord")

@bot.event
async def on_disconnect():
    logger.warning("Bot disconnected from Discord")

@bot.event
async def on_error(event, *args, **kwargs):
    logger.error(f"Error in {event}: {sys.exc_info()}")

# Run the bot
def main():
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        logger.critical("No Discord token found. Please check your .env file.")
        return

    retry_count = 0
    while True:
        try:
            logger.info("Attempting to connect to Discord...")
            bot.run(token, reconnect=True)
        except discord.LoginFailure:
            logger.critical("Failed to login. Invalid token provided.")
            return  # Exit if token is invalid
        except Exception as e:
            retry_count += 1
            wait_time = min(retry_count * 5, 60)  # Exponential backoff, max 60 seconds
            logger.error(f"Error occurred: {str(e)}", exc_info=True)
            logger.info(f"Attempting to restart in {wait_time} seconds...")
            time.sleep(wait_time)
            if retry_count >= 5:  # Max 5 retries
                logger.critical("Max retry attempts reached. Exiting...")
                return
            continue

if __name__ == "__main__":
    logger.info("Starting Discord Music Bot... xd")

    main() 
