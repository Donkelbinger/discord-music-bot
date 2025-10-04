"""
Refactored Music Cog

This is the new slim MusicCog class that coordinates all the specialized modules:
- MediaExtractor: Handles media processing and extraction
- QueueManager: Manages queue operations and persistence  
- AudioPlayerManager: Handles voice states and audio playback
- ErrorHandler: Provides standardized error handling

This replaces the original 828-line monolithic MusicCog class.
"""

import asyncio
import discord
import logging
import re
from discord.ext import commands
from discord import app_commands
from typing import Optional

from .media_extractor import MediaExtractor
from .queue_manager import QueueManager  
from .audio_player import AudioPlayerManager
from .error_handler import ErrorHandler, ErrorType

logger = logging.getLogger('MusicCogRefactored')


class MusicCog(commands.Cog):
    """Refactored Music Cog that coordinates specialized modules for clean separation of concerns."""
    
    def __init__(self, bot):
        """Initialize the MusicCog with all specialized modules."""
        self.bot = bot
        
        # Initialize specialized modules
        self.error_handler = ErrorHandler(bot)
        self.media_extractor = MediaExtractor()
        self.queue_manager = QueueManager(bot)
        self.audio_player_manager = AudioPlayerManager(bot, self.queue_manager, self.error_handler)
        
        logger.info("Refactored MusicCog initialized with modular architecture")
        
        # Start background tasks
        if self.queue_manager.ENABLE_QUEUE_PERSISTENCE:
            # Restore queues on startup
            asyncio.create_task(self.queue_manager.restore_queues_on_startup())
            # Start periodic saving
            asyncio.create_task(self.queue_manager.start_periodic_save(self.audio_player_manager.voice_states))
    
    # ==================================================================================
    # DISCORD COMMAND HANDLERS
    # ==================================================================================
    
    @app_commands.command(name='play', description='Play a song by URL or search query')
    @app_commands.describe(query="Song name, YouTube/SoundCloud URL, or playlist URL to play")
    @commands.cooldown(rate=3, per=10, type=commands.BucketType.user)  
    async def play(self, interaction: discord.Interaction, *, query: str):
        """Play a song from YouTube or SoundCloud, or search for a song."""
        await interaction.response.defer()
        
        try:
            # Validate and sanitize input
            sanitized_query = await self._validate_and_sanitize_query(query)
            
            # Ensure voice connection
            voice_client = await self._ensure_voice_client(interaction)
            if not voice_client:
                return
            
            # Extract audio information
            try:
                # Get voice state
                ctx = await self.bot.get_context(interaction)
                voice_state = self.audio_player_manager.get_voice_state(ctx)
                
                # Check if it's a playlist URL
                if self.media_extractor._is_playlist_url(sanitized_query):
                    await self._handle_playlist_request(interaction, ctx, sanitized_query, voice_state)
                else:
                    await self._handle_single_song_request(interaction, ctx, sanitized_query, voice_state)
            except ValueError as audio_error:
                # Handle audio processing errors with standardized error handling
                error_type, user_message = self.error_handler.extract_error_details(audio_error)
                await self.error_handler.handle_error(
                    error=audio_error,
                    error_type=error_type,
                    context_info=f"play command audio processing for query '{sanitized_query}' in guild: {interaction.guild.name}",
                    interaction=interaction,
                    user_message=f"Failed to process: `{sanitized_query}`\n{user_message}"
                )
                return
            
            # Signal audio player that queue has new items
            await self.audio_player_manager.signal_queue_ready(interaction.guild.id)
            
            # Save queue state after adding song
            if self.queue_manager.ENABLE_QUEUE_PERSISTENCE:
                asyncio.create_task(self.queue_manager.save_queue_state(self.audio_player_manager.voice_states))
            
            # Send success response
            logger.info(f"Added to queue: {title} from {platform} in {interaction.guild.name} (position {queue_position})")
            await interaction.followup.send(
                f'✅ **Added to Queue**\n'
                f'🎵 {title} ({platform})\n'
                f'👤 Requested by {interaction.user.display_name}\n'
                f'📊 Queue position: {queue_position} | Total songs: {total_songs}'
            )
            
        except Exception as e:
            # Handle unexpected errors with standardized error handling
            error_type, user_message = self.error_handler.extract_error_details(e)
            await self.error_handler.handle_error(
                error=e,
                error_type=error_type,
                context_info=f"play command execution for query '{query}' in guild: {interaction.guild.name}",
                interaction=interaction,
                user_message=f"Unexpected error occurred while processing: `{query}`\n{user_message}"
            )
    
    @app_commands.command(name='skip', description='Skip the current song')
    @commands.cooldown(rate=5, per=10, type=commands.BucketType.guild)  # 5 skips per 10 seconds per guild
    async def skip(self, interaction: discord.Interaction):
        """Skip the current song."""
        try:
            if interaction.guild.voice_client is None:
                return await interaction.response.send_message(
                    f"🔌 **Not Connected**\n"
                    f"❌ Bot is not connected to any voice channel\n"
                    f"💡 **Try:** Use `/play` to start playing music first"
                )

            ctx = await self.bot.get_context(interaction)
            state = self.audio_player_manager.get_voice_state(ctx)
            
            if state.is_playing():
                current_song = state.current_title or "current song"
                if state.skip():
                    logger.info(f"Skipped song '{current_song}' in {interaction.guild.name}")
                    await interaction.response.send_message(
                        f"⏭️ **Song Skipped**\n"
                        f"🎵 Skipped: {current_song}\n"
                        f"👤 Requested by {interaction.user.display_name}"
                    )
                else:
                    await interaction.response.send_message(
                        f"⚠️ **Skip Failed**\n"
                        f"❌ Could not skip the current song\n"
                        f"💡 **Try:** The song may have just finished naturally"
                    )
            else:
                await interaction.response.send_message(
                    f"⏸️ **Nothing Playing**\n"
                    f"❌ No music is currently playing\n"
                    f"💡 **Try:** Use `/play` to add songs to the queue"
                )
                
        except Exception as e:
            error_type, user_message = self.error_handler.extract_error_details(e)
            await self.error_handler.handle_error(
                error=e,
                error_type=error_type,
                context_info=f"skip command in guild: {interaction.guild.name}",
                interaction=interaction,
                user_message=f"Failed to skip the current song\n{user_message}"
            )
    
    @app_commands.command(name='queue', description='Show the current queue')
    async def queue(self, interaction: discord.Interaction):
        """Show the current queue."""
        try:
            queue_info = self.queue_manager.get_queue_info(interaction.guild.id)
            
            if queue_info['total_songs'] == 0:
                await interaction.response.send_message(
                    f"📭 **Queue Empty**\n"
                    f"❌ No songs currently in queue\n"
                    f"💡 **Try:** Use `/play` to add some music!"
                )
                return
            
            # Build queue display
            queue_display = f"📊 **Queue ({queue_info['total_songs']}/{queue_info['max_queue_size']})**\n\n"
            
            # Show first 10 songs
            songs_to_show = queue_info['songs'][:10]
            for i, (title, requester_name) in enumerate(songs_to_show, 1):
                queue_display += f"`{i:2}.` {title} - *{requester_name}*\n"
            
            # Show if there are more songs
            if queue_info['total_songs'] > 10:
                remaining = queue_info['total_songs'] - 10
                queue_display += f"\n*... and {remaining} more song(s)*\n"
            
            # Show user counts
            queue_display += f"\n**👥 Songs per user:**\n"
            for user_id, count in queue_info['user_counts'].items():
                user = interaction.guild.get_member(user_id)
                username = user.name if user else "Unknown User"
                queue_display += f"• {username}: {count}/{queue_info['user_queue_limit']}\n"
            
            await interaction.response.send_message(queue_display)
            
        except Exception as e:
            await self.error_handler.handle_command_error(interaction, e)
    
    @app_commands.command(name='clear', description='Clear the queue')
    @commands.cooldown(rate=2, per=30, type=commands.BucketType.guild)  # 2 clears per 30 seconds per guild
    async def clear(self, interaction: discord.Interaction):
        """Clear the queue."""
        try:
            queue_size = self.queue_manager.clear_queue(interaction.guild.id)
            
            # Save queue state after clearing
            if self.queue_manager.ENABLE_QUEUE_PERSISTENCE:
                asyncio.create_task(self.queue_manager.save_queue_state(self.audio_player_manager.voice_states))
            
            logger.info(f"Queue cleared in {interaction.guild.name}")
            await interaction.response.send_message(
                f'✅ **Queue Cleared**\n'
                f'🗑️ Removed {queue_size} song(s) from queue\n'
                f'👤 Requested by {interaction.user.display_name}'
            )
            
        except Exception as e:
            await self.error_handler.handle_command_error(interaction, e)
    
    @app_commands.command(name='remove', description='Remove a specific song from the queue by its position number')
    @app_commands.describe(position="Position number of the song to remove (1-based)")
    @commands.cooldown(rate=3, per=5, type=commands.BucketType.user)  # 3 removes per 5 seconds per user
    async def remove(self, interaction: discord.Interaction, position: int):
        """Remove a specific song from the queue."""
        try:
            removed_song = self.queue_manager.remove_from_queue(interaction.guild.id, position)
            _, title, requester = removed_song
            
            # Save queue state after removing song
            if self.queue_manager.ENABLE_QUEUE_PERSISTENCE:
                asyncio.create_task(self.queue_manager.save_queue_state(self.audio_player_manager.voice_states))
            
            logger.info(f"Removed song '{title}' at position {position} from queue in {interaction.guild.name}")
            await interaction.response.send_message(
                f"✅ **Song Removed**\n"
                f"🗑️ Removed: {title}\n"
                f"👤 Originally requested by {requester.name}\n"
                f"🔢 Position: {position}"
            )
            
        except ValueError as e:
            await self.error_handler.handle_error(
                error=e,
                error_type=ErrorType.USER_INPUT,
                context_info=f"remove command (position {position}) in guild: {interaction.guild.name}",
                interaction=interaction,
                user_message=str(e)
            )
        except Exception as e:
            error_type, user_message = self.error_handler.extract_error_details(e)
            await self.error_handler.handle_error(
                error=e,
                error_type=error_type,
                context_info=f"remove command (position {position}) in guild: {interaction.guild.name}",
                interaction=interaction,
                user_message=f"Failed to remove song at position {position}\n{user_message}"
            )
    
    @app_commands.command(name='help', description='Show information about bot commands')
    async def help(self, interaction: discord.Interaction):
        """Display comprehensive help information about bot commands."""
        try:
            help_embed = discord.Embed(
                title="🎵 **Discord Music Bot - Command Help**",
                description="A comprehensive music bot for playing YouTube and SoundCloud content.",
                color=0x3498db  # Nice blue color
            )
            
            # Add command information
            commands_info = [
                {
                    "name": "🎵 `/play <query>`",
                    "value": (
                        "**Play music from YouTube or SoundCloud**\n"
                        "• **URL:** Direct YouTube or SoundCloud links\n"
                        "• **Search:** Search terms to find music\n"
                        "• **Examples:**\n"
                        "  - `/play Never Gonna Give You Up`\n"
                        "  - `/play https://youtube.com/watch?v=dQw4w9WgXcQ`\n"
                        "• **Rate Limit:** 3 commands per 10 seconds per user"
                    ),
                    "inline": False
                },
                {
                    "name": "⏭️ `/skip`",
                    "value": (
                        "**Skip the currently playing song**\n"
                        "• Immediately moves to the next song in queue\n"
                        "• **Rate Limit:** 5 skips per 10 seconds per server"
                    ),
                    "inline": False
                },
                {
                    "name": "📊 `/queue`",
                    "value": (
                        "**Show the current music queue**\n"
                        "• Displays up to 10 upcoming songs\n"
                        "• Shows who requested each song\n"
                        "• Displays queue limits and user counts"
                    ),
                    "inline": False
                },
                {
                    "name": "🗑️ `/clear`",
                    "value": (
                        "**Clear the entire music queue**\n"
                        "• Removes all queued songs\n"
                        "• Does not stop currently playing song\n"
                        "• **Rate Limit:** 2 clears per 30 seconds per server"
                    ),
                    "inline": False
                },
                {
                    "name": "❌ `/remove <position>`",
                    "value": (
                        "**Remove a specific song from the queue**\n"
                        "• **Position:** Number from 1 to queue size\n"
                        "• **Example:** `/remove 3` removes the 3rd song\n"
                        "• **Rate Limit:** 3 removes per 5 seconds per user"
                    ),
                    "inline": False
                },
                {
                    "name": "👋 `/leave`",
                    "value": (
                        "**Make the bot leave the voice channel**\n"
                        "• Stops all music and clears the queue\n"
                        "• Bot will also auto-leave after 3 minutes of inactivity"
                    ),
                    "inline": False
                }
            ]
            
            for cmd in commands_info:
                help_embed.add_field(name=cmd["name"], value=cmd["value"], inline=cmd["inline"])
            
            # Add additional information
            help_embed.add_field(
                name="📋 **Queue Limits**",
                value=(
                    f"• **Max Queue Size:** {self.queue_manager.MAX_QUEUE_SIZE} songs total\n"
                    f"• **Per User Limit:** {self.queue_manager.USER_QUEUE_LIMIT} songs per user\n"
                    "• Limits prevent spam and ensure fair usage"
                ),
                inline=False
            )
            
            help_embed.add_field(
                name="🎯 **Supported Sources**",
                value=(
                    "• **YouTube:** Regular videos, age-restricted content\n"
                    "• **SoundCloud:** Tracks and playlists\n"
                    "• **Search:** Finds content across both platforms"
                ),
                inline=False
            )
            
            help_embed.add_field(
                name="⚡ **Features**",
                value=(
                    "• **Queue Persistence:** Saves queue across bot restarts\n"
                    "• **Auto-Disconnect:** Leaves empty channels after 3 minutes\n"
                    "• **Error Recovery:** Continues playing if individual songs fail\n"
                    "• **Rate Limiting:** Prevents spam and abuse"
                ),
                inline=False
            )
            
            help_embed.add_field(
                name="💡 **Tips**",
                value=(
                    "• Join a voice channel before using `/play`\n"
                    "• Use specific search terms for better results\n"
                    "• Check `/queue` to see your position\n"
                    "• Bot requires appropriate permissions in voice channels"
                ),
                inline=False
            )
            
            help_embed.set_footer(
                text="Discord Music Bot • For internal use • Need help? Contact bot administrator"
            )
            
            await interaction.response.send_message(embed=help_embed, ephemeral=True)
            logger.info(f"Help command used by {interaction.user.name} in {interaction.guild.name}")
            
        except Exception as e:
            await self.error_handler.handle_command_error(interaction, e)
    
    @app_commands.command(name='leave', description='Leave the voice channel')
    async def leave(self, interaction: discord.Interaction):
        """Leave the voice channel."""
        try:
            if not interaction.guild.voice_client:
                await interaction.response.send_message(
                    f"🔌 **Not Connected**\n"
                    f"❌ Bot is not connected to any voice channel\n"
                    f"💡 **Info:** Nothing to disconnect from"
                )
                return
            
            ctx = await self.bot.get_context(interaction)
            state = self.audio_player_manager.get_voice_state(ctx)
            
            # Stop the audio player and disconnect
            await state.stop()
            
            # Remove voice state
            self.audio_player_manager.remove_voice_state(interaction.guild.id)
            
            # Clear the guild's queue
            self.queue_manager.clear_queue(interaction.guild.id)
            
            response = (
                f"👋 **Disconnected**\n"
                f"✅ Left voice channel and cleared queue\n"
                f"👤 Requested by {interaction.user.display_name}"
            )
            
            await interaction.response.send_message(response)
            logger.info(f"Left voice channel in {interaction.guild.name} (requested by {interaction.user.name})")
            
        except Exception as e:
            error_type, user_message = self.error_handler.extract_error_details(e)
            await self.error_handler.handle_error(
                error=e,
                error_type=error_type,
                context_info=f"leave command in guild: {interaction.guild.name}",
                interaction=interaction,
                user_message=f"Error occurred while leaving voice channel\n{user_message}"
            )
    
    # ==================================================================================
    # HELPER METHODS
    # ==================================================================================
    
    async def _ensure_voice_client(self, interaction: discord.Interaction) -> Optional[discord.VoiceClient]:
        """Ensure we have a valid voice client, joining if necessary."""
        if not interaction.guild.voice_client:
            if interaction.user.voice:
                try:
                    voice_client = await interaction.user.voice.channel.connect()
                    logger.info(f"Connected to voice channel: {interaction.user.voice.channel.name}")
                    return voice_client
                except discord.HTTPException as e:
                    await self.error_handler.handle_error(
                        error=e,
                        error_type=ErrorType.PERMISSION,
                        context_info=f"voice channel connection in guild: {interaction.guild.name}",
                        interaction=interaction,
                        user_message="Failed to connect to voice channel\nCheck bot permissions"
                    )
                    return None
            else:
                await interaction.followup.send(
                    f"🔌 **Voice Channel Required**\n"
                    f"❌ You need to be in a voice channel to use this command\n"
                    f"💡 **Try:** Join a voice channel first, then use `/play`"
                )
                return None
        return interaction.guild.voice_client
    
    def _validate_and_sanitize_query(self, query: str) -> tuple[bool, str, str]:
        """Validate and sanitize user input query."""
        if not query or not query.strip():
            return False, "", "Query cannot be empty"
        
        # Trim whitespace
        sanitized = query.strip()
        
        # Check length limits
        if self._is_url(sanitized):
            if len(sanitized) > 2000:
                return False, "", "URL too long (maximum 2000 characters)"
        else:
            if len(sanitized) > 500:
                return False, "", "Search query too long (maximum 500 characters)"
        
        # Remove control characters and normalize
        sanitized = ''.join(char for char in sanitized if ord(char) >= 32 or char in ['\n', '\t'])
        sanitized = re.sub(r'\s+', ' ', sanitized).strip()
        
        # Basic security checks
        suspicious_patterns = ['<script', 'javascript:', 'data:', 'vbscript:', 'onload=', 'onerror=']
        if any(pattern in sanitized.lower() for pattern in suspicious_patterns):
            return False, "", "Query contains potentially unsafe content"
        
        return True, sanitized, ""
    
    def _is_url(self, text: str) -> bool:
        """Check if the text is a valid URL."""
        url_patterns = [
            r'^https?://(www\.)?(youtube\.com|youtu\.be)/',
            r'^https?://(www\.)?soundcloud\.com/',
        ]
        return any(re.match(pattern, text, re.IGNORECASE) for pattern in url_patterns)
    
    # ==================================================================================
    # EVENT HANDLERS
    # ==================================================================================
    
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Handle voice state changes for automatic empty channel leaving."""
        try:
            # Only process if we have an active voice state for this guild
            guild_id = member.guild.id
            if guild_id not in self.audio_player_manager.voice_states:
                return
            
            voice_state = self.audio_player_manager.voice_states[guild_id]
            
            # Only care about changes involving our voice channel
            if not voice_state.voice or not voice_state.voice.channel:
                return
            
            bot_channel = voice_state.voice.channel
            
            # Check if the change involves our channel (user left or joined our channel)
            channel_affected = False
            if before.channel == bot_channel or after.channel == bot_channel:
                channel_affected = True
            
            if channel_affected and not member.bot:  # Only care about human users
                # Reset empty channel timer since there was activity
                voice_state.empty_channel_detected_time = None
                
                # If user left our channel, check if it's now empty
                if before.channel == bot_channel and after.channel != bot_channel:
                    logger.debug(f"User {member.display_name} left voice channel in {member.guild.name}")
                    
                    # Check if channel is now empty (only bot remains)
                    if voice_state._is_voice_channel_empty():
                        logger.info(f"Voice channel became empty after {member.display_name} left, starting leave timer")
                        # The cleanup monitor will handle the actual leaving
                
                # If user joined our channel, reset any empty channel detection
                elif after.channel == bot_channel and before.channel != bot_channel:
                    logger.debug(f"User {member.display_name} joined voice channel in {member.guild.name}")
                    voice_state.empty_channel_detected_time = None
                    # Update activity timestamp to show the channel is active
                    voice_state._update_activity_timestamp()
                    
        except Exception as e:
            logger.error(f"Error in voice state update handler: {e}", exc_info=True)
    
    @commands.Cog.listener()
    async def on_app_command_error(self, interaction: discord.Interaction, error):
        """Handle application command errors, including rate limiting."""
        if isinstance(error, commands.CommandOnCooldown):
            # Handle cooldown errors with user-friendly messages
            remaining = int(error.retry_after)
            minutes = remaining // 60
            seconds = remaining % 60
            
            if minutes > 0:
                time_str = f"{minutes}m {seconds}s"
            else:
                time_str = f"{seconds}s"
            
            command_name = interaction.command.name if interaction.command else 'command'
            
            cooldown_message = (
                f"⏱️ **Rate Limited**\n"
                f"❌ `/{command_name}` is on cooldown\n"
                f"⏰ **Try again in:** {time_str}\n"
                f"💡 **Why:** This prevents spam and ensures smooth bot operation"
            )
            
            try:
                await interaction.response.send_message(cooldown_message, ephemeral=True)
            except discord.HTTPException as e:
                logger.error(f"Failed to send cooldown message: {e}")
                
            logger.info(f"Rate limit hit for {command_name} command by {interaction.user.name} in {interaction.guild.name}. Retry after: {time_str}")
        
        else:
            # Handle other errors with standardized system
            await self.error_handler.handle_command_error(interaction, error)
    
    def cog_unload(self):
        """Called when the cog is unloaded - cleanup tasks."""
        if self.queue_manager.ENABLE_QUEUE_PERSISTENCE:
            # Save queue state on unload
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self.queue_manager.cleanup_on_shutdown(self.audio_player_manager.voice_states))
            else:
                loop.run_until_complete(self.queue_manager.cleanup_on_shutdown(self.audio_player_manager.voice_states))
        
        # Stop all voice states
        asyncio.create_task(self.audio_player_manager.stop_all_voice_states())
        
        logger.info("MusicCog unloaded with proper cleanup")


# Setup function for loading the cog
async def setup(bot):
    """Setup function to add the cog to the bot."""
    await bot.add_cog(MusicCog(bot))
