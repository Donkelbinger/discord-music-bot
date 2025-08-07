import os
import sys
import asyncio
import logging
from pathlib import Path
import discord
from discord.ext import commands
from dotenv import load_dotenv
from modules.config_validator import ConfigValidator, ConfigValidationError
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

# Configuration will be loaded and validated during startup
AUTHORIZED_GUILDS = set()
VALIDATED_CONFIG = {}

class MusicBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix='/', intents=intents)
        
    async def setup_hook(self):
        await self.add_cog(MusicCog(self))
        
        # Sync commands only to authorized guilds
        synced_count = 0
        for guild_id in AUTHORIZED_GUILDS:
            try:
                guild = discord.Object(id=guild_id)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                synced_count += 1
                logger.info(f"Slash commands synced to authorized guild ID: {guild_id}")
            except Exception as e:
                logger.error(f"Failed to sync commands to guild {guild_id}: {e}")
        
        logger.info(f"Commands successfully synced to {synced_count}/{len(AUTHORIZED_GUILDS)} authorized guilds")

bot = MusicBot()

@bot.event
async def on_ready():
    logger.info(f'Bot is ready! Logged in as {bot.user.name} (ID: {bot.user.id})')
    logger.info(f'Connected to {len(bot.guilds)} guilds')
    
    # Check all current guilds for authorization
    unauthorized_guilds = []
    for guild in bot.guilds:
        if guild.id in AUTHORIZED_GUILDS:
            logger.info(f'✅ Authorized guild: {guild.name} (ID: {guild.id})')
        else:
            logger.warning(f'❌ UNAUTHORIZED guild detected: {guild.name} (ID: {guild.id}) - will leave')
            unauthorized_guilds.append(guild)
    
    # Leave unauthorized guilds
    for guild in unauthorized_guilds:
        try:
            await guild.leave()
            logger.warning(f'Left unauthorized guild: {guild.name} (ID: {guild.id})')
        except Exception as e:
            logger.error(f'Failed to leave guild {guild.name} (ID: {guild.id}): {e}')
    
    if unauthorized_guilds:
        logger.warning(f'Removed bot from {len(unauthorized_guilds)} unauthorized guilds')
    
    logger.info(f'Bot is now active on {len(bot.guilds) - len(unauthorized_guilds)} authorized guilds')

@bot.event
async def on_guild_join(guild):
    """Event triggered when bot joins a new guild - check authorization"""
    if guild.id not in AUTHORIZED_GUILDS:
        logger.warning(f"❌ UNAUTHORIZED guild join attempt: {guild.name} (ID: {guild.id}) - leaving immediately")
        logger.warning(f"Guild owner: {guild.owner} (ID: {guild.owner_id})")
        logger.warning(f"Guild member count: {guild.member_count}")
        
        try:
            # Try to send a message to the guild owner before leaving (if possible)
            if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
                embed = discord.Embed(
                    title="❌ Access Denied",
                    description="This bot is restricted to authorized servers only. Contact the bot administrator for access.",
                    color=0xff0000
                )
                await guild.system_channel.send(embed=embed)
            
            await guild.leave()
            logger.warning(f"Successfully left unauthorized guild: {guild.name} (ID: {guild.id})")
        except Exception as e:
            logger.error(f"Failed to leave unauthorized guild {guild.name} (ID: {guild.id}): {e}")
    else:
        logger.info(f"✅ Authorized guild join: {guild.name} (ID: {guild.id})")
        logger.info(f"Guild member count: {guild.member_count}")
        
        # Sync commands to the new authorized guild
        try:
            guild_obj = discord.Object(id=guild.id)
            bot.tree.copy_global_to(guild=guild_obj)
            await bot.tree.sync(guild=guild_obj)
            logger.info(f"Slash commands synced to new authorized guild: {guild.name}")
        except Exception as e:
            logger.error(f"Failed to sync commands to new guild {guild.name}: {e}")

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
    global AUTHORIZED_GUILDS, VALIDATED_CONFIG
    
    # Comprehensive configuration validation
    logger.info("🔧 Starting Discord Music Bot...")
    logger.info("📋 Validating configuration...")
    
    validator = ConfigValidator()
    try:
        VALIDATED_CONFIG = validator.validate_all_config()
        AUTHORIZED_GUILDS = VALIDATED_CONFIG.get('AUTHORIZED_GUILDS', set())
        
        # Display configuration summary
        config_summary = validator.get_config_summary()
        logger.info(f"\n{config_summary}")
        
    except ConfigValidationError as e:
        logger.critical(f"\n❌ Configuration validation failed:\n{str(e)}")
        logger.critical("\n💡 Bot startup aborted. Please fix configuration issues and try again.")
        return
    except Exception as e:
        logger.critical(f"\n❌ Unexpected error during configuration validation: {e}")
        logger.critical("\n💡 Bot startup aborted. Please check your configuration and try again.")
        return
    
    # Extract validated Discord token
    token = VALIDATED_CONFIG['DISCORD_TOKEN']
    
    # Start bot with retry logic
    retry_count = 0
    while True:
        try:
            logger.info("🔗 Attempting to connect to Discord...")
            bot.run(token, reconnect=True)
        except discord.LoginFailure:
            logger.critical("Failed to login. Invalid token provided.")
            return  # Exit if token is invalid
        except Exception as e:
            retry_count += 1
            # CONFIG: Connection retry backoff - Exponential backoff with 5s multiplier, max 60s wait
            # This prevents overwhelming Discord servers during connection issues
            wait_time = min(retry_count * 5, 60)  # Exponential backoff, max 60 seconds
            logger.error(f"Error occurred: {str(e)}", exc_info=True)
            logger.info(f"Attempting to restart in {wait_time} seconds...")
            time.sleep(wait_time)
            # CONFIG: Maximum connection retry attempts - Bot gives up after 5 failed connection attempts
            # This prevents infinite retry loops while allowing for temporary network issues
            if retry_count >= 5:  # Max 5 retries
                logger.critical("Max retry attempts reached. Exiting...")
                return
            continue

if __name__ == "__main__":
    logger.info("Starting Discord Music Bot... xd")
    main() 