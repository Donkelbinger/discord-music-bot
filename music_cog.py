import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
import asyncio
from async_timeout import timeout
from collections import deque
import logging
import gc
import os
from dotenv import load_dotenv
from typing import Optional, Tuple, Deque, Dict, List, Union, TYPE_CHECKING
from collections import deque
from discord import Member, Message
import re
import random
import aiohttp
import time
import json
from pathlib import Path
import aiofiles

load_dotenv()
logger = logging.getLogger('MusicCog')

# Error handling constants and enums
class ErrorType:
    """Standardized error types for consistent handling."""
    COMMAND = "command"  # User command errors
    AUDIO = "audio"     # Audio processing/playback errors
    NETWORK = "network" # Network/connection errors
    SYSTEM = "system"   # System/internal errors
    USER_INPUT = "user_input"  # User input validation errors
    PERMISSION = "permission"  # Permission/access errors

# Use TYPE_CHECKING and forward references to avoid circular imports
if TYPE_CHECKING:
    from .music_cog import VoiceState
else:
    VoiceState = "VoiceState"

class MusicCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.voice_states: Dict[int, 'VoiceState'] = {}
        self.cleanup_tasks = {}
        
        # Rate limiting configuration
        self.MAX_QUEUE_SIZE = int(os.getenv('MAX_QUEUE_SIZE', '50'))  # Max songs in queue
        self.USER_QUEUE_LIMIT = int(os.getenv('USER_QUEUE_LIMIT', '20'))  # Max songs per user in queue (increased for album/playlist support)
        
        # Queue persistence configuration
        self.ENABLE_QUEUE_PERSISTENCE = os.getenv('ENABLE_QUEUE_PERSISTENCE', 'true').lower() == 'true'
        self.PERSISTENCE_FILE = Path(os.getenv('QUEUE_PERSISTENCE_FILE', 'data/queue_state.json'))
        
        # Ensure data directory exists
        self.PERSISTENCE_FILE.parent.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Music Cog initialized - Max queue size: {self.MAX_QUEUE_SIZE}, User limit: {self.USER_QUEUE_LIMIT}")
        if self.ENABLE_QUEUE_PERSISTENCE:
            logger.info(f"Queue persistence enabled - File: {self.PERSISTENCE_FILE}")
            # Restore saved queues on startup
            asyncio.create_task(self._restore_queues_on_startup())
            # Start periodic saving task
            asyncio.create_task(self._periodic_queue_save())

    class VoiceState:
        def __init__(self, bot, ctx):
            self.bot = bot
            self.ctx = ctx
            self.current = None
            self.current_title: Optional[str] = None
            self.current_requester: Optional[discord.Member] = None
            self.current_message: Optional[discord.Message] = None
            self.voice = ctx.voice_client
            self.queue: Deque[Tuple] = deque()  # Will store tuples of (source, title, requester)
            self.next = asyncio.Event()
            self.queue_ready = asyncio.Event()  # Event to signal when queue has items
            self.audio_player = bot.loop.create_task(self.audio_player_task())
            self.cleanup_task = None
            self.last_activity = asyncio.get_event_loop().time()
            logger.info(f"Voice State initialized for guild: {ctx.guild.name}")

        async def audio_player_task(self) -> None:
            """Main task that handles playing songs from the queue."""
            try:
                while True:
                    self.next.clear()
                    
                    # If queue is empty, clean up and wait efficiently
                    if not self.queue:
                        await self._handle_empty_queue()
                        # Wait for new items to be added to queue instead of polling
                        try:
                            await self.queue_ready.wait()
                            self.queue_ready.clear()
                        except asyncio.CancelledError:
                            logger.info(f"Audio player task cancelled for guild: {self.ctx.guild.name}")
                            break
                        continue

                    # Cancel cleanup if it was scheduled
                    if self.cleanup_task:
                        self.cleanup_task.cancel()
                        self.cleanup_task = None

                    current_song = None
                    try:
                        # CONFIG: Audio playback timeout - 1 hour (3600s) max duration per song
                        # This prevents extremely long tracks from blocking the queue indefinitely
                        async with timeout(3600):  # Increase timeout to 1 hour
                            current_song = self.queue.popleft()
                            self.current, self.current_title, self.current_requester = current_song
                            logger.info(f"Playing next song in {self.ctx.guild.name}: {self.current_title}")
                            
                            # Delete previous now-playing message if it exists
                            await self._delete_current_message()
                            
                            # Send new now-playing message
                            await self._send_now_playing_message()

                    except asyncio.TimeoutError:
                        logger.warning(f"Player timed out in {self.ctx.guild.name}")
                        self.bot.loop.create_task(self.stop())
                        break
                    except Exception as e:
                        logger.error(f"Error in audio player task: {str(e)}", exc_info=True)
                        # Clean up current song if extraction failed
                        if current_song and current_song[0]:
                            try:
                                current_song[0].cleanup()
                            except:
                                pass
                        continue

                    # Play the song with proper error handling
                    try:
                        # Validate voice connection before playing
                        if not self.voice or not self.voice.is_connected():
                            logger.warning(f"Voice client disconnected in {self.ctx.guild.name}, attempting to reconnect")
                            # Try to reconnect to the user's voice channel
                            if hasattr(self.ctx, 'author') and self.ctx.author.voice:
                                try:
                                    self.voice = await self.ctx.author.voice.channel.connect()
                                    logger.info(f"Successfully reconnected to voice channel in {self.ctx.guild.name}")
                                except Exception as reconnect_error:
                                    logger.error(f"Failed to reconnect to voice channel: {str(reconnect_error)}")
                                    await self._send_error_to_channel(
                                        "🔌 **Connection Lost**",
                                        "❌ Lost connection to voice channel and failed to reconnect",
                                        f"Skipping song. Technical details: `{str(reconnect_error)}`"
                                    )
                                    continue
                            else:
                                await self._send_error_to_channel(
                                    "🔌 **Connection Lost**",
                                    "❌ Lost connection to voice channel",
                                    "Cannot reconnect - user not in a voice channel"
                                )
                                continue
                        
                        if self.voice and self.current:
                            self.voice.play(self.current, after=self.play_next)
                            logger.info(f"Started playing song in {self.ctx.guild.name}")
                        else:
                            logger.error("Voice client or audio source is None")
                            # Notify users of the issue
                            await self._send_error_to_channel(
                                "🔌 **Connection Issue**",
                                "❌ Lost connection to voice channel or audio source", 
                                "Bot will attempt to reconnect automatically"
                            )
                            continue
                    except Exception as e:
                        logger.error(f"Error playing song: {str(e)}", exc_info=True)
                        # Notify users of playback failure
                        song_title = self.current_title or "Unknown song"
                        await self._send_error_to_channel(
                            "🎵 **Playback Error**",
                            f"❌ Failed to play: {song_title}",
                            f"Skipping to next song. Technical details: `{str(e)}`"
                        )
                        # Clean up failed audio source
                        await self._cleanup_current_song()
                        continue

                    # Wait for song to finish with proper cancellation handling
                    try:
                        await self.next.wait()
                    except asyncio.CancelledError:
                        logger.info(f"Audio player cancelled while waiting for song to finish")
                        break
                    except Exception as e:
                        logger.error(f"Error waiting for next song: {str(e)}", exc_info=True)
                        continue
                    
                    # Clean up the current song
                    await self._cleanup_current_song()
                    
                    # Save queue state after song completion
                    if hasattr(self.bot.get_cog('MusicCog'), 'ENABLE_QUEUE_PERSISTENCE'):
                        cog = self.bot.get_cog('MusicCog')
                        if cog and cog.ENABLE_QUEUE_PERSISTENCE:
                            asyncio.create_task(cog._save_queue_state())
                    
            except asyncio.CancelledError:
                logger.info(f"Audio player task cancelled for guild: {self.ctx.guild.name}")
            except Exception as e:
                logger.error(f"Unexpected error in audio player task: {str(e)}", exc_info=True)
            finally:
                # Ensure cleanup happens even if task is cancelled or crashes
                logger.info(f"Audio player task ending for guild: {self.ctx.guild.name}")
                await self._cleanup_current_song()
                await self._cancel_cleanup_task()
                # Force garbage collection to help with resource cleanup
                gc.collect()

        async def _handle_empty_queue(self) -> None:
            """Handle when the queue is empty."""
            self.current = None
            self.current_title = None
            self.current_requester = None
            await self._delete_current_message()
            
            if not self.cleanup_task:
                self.cleanup_task = self.bot.loop.create_task(self.cleanup_check())

        async def _delete_current_message(self) -> None:
            """Delete the current playing message if it exists."""
            if self.current_message:
                try:
                    await self.current_message.delete()
                except discord.HTTPException:
                    pass
                self.current_message = None

        async def _send_now_playing_message(self) -> None:
            """Send a message with information about the current song."""
            try:
                self.current_message = await self.ctx.channel.send(
                    f"🎵 Now playing: **{self.current_title}** (requested by {self.current_requester.display_name})"
                )
            except discord.HTTPException as e:
                logger.error(f"Failed to send now playing message: {e}")

        async def _cleanup_current_song(self) -> None:
            """Clean up the current song resources."""
            if self.current:
                try:
                    self.current.cleanup()
                    logger.debug(f"Successfully cleaned up audio source")
                except Exception as e:
                    logger.error(f"Error cleaning up song: {e}")
                finally:
                    # Always clear the reference even if cleanup fails
                    self.current = None
                    
            # Clear other references
            self.current_title = None
            self.current_requester = None
            
            # Trigger garbage collection to help free memory
            gc.collect()

        def play_next(self, error=None) -> None:
            """Callback called after the current song finishes playing."""
            if error:
                logger.error(f'Player error: {error}')
                # Notify users of playback error
                asyncio.create_task(self._handle_playback_error(error))
            self.next.set()

        async def cleanup_check(self) -> None:
            """Check if bot should leave voice channel due to inactivity."""
            try:
                # CONFIG: Auto-disconnect timeout - Bot leaves voice channel after 3 minutes (180s) of inactivity
                # This prevents the bot from staying in empty voice channels indefinitely
                await asyncio.sleep(180)  # Wait 3 minutes
                if not self.queue and not self.current:
                    # Check if there are any users in the voice channel
                    # CONFIG: Minimum users in voice channel - Bot leaves if only 1 member (itself) remains
                    # This prevents the bot from playing music to empty channels
                    if self.voice and len(self.voice.channel.members) <= 1:  # Only bot is in the channel
                        await self.stop()
                        await self.ctx.channel.send("👋 Leaving voice channel due to inactivity.")
            except asyncio.CancelledError:
                logger.debug(f"Cleanup check cancelled for guild: {self.ctx.guild.name}")
            except Exception as e:
                # Use standardized error logging for cleanup failures
                logger.error(f"Error during cleanup check in {self.ctx.guild.name}: {type(e).__name__}: {str(e)}", exc_info=True)

        async def _cancel_cleanup_task(self) -> None:
            """Cancel the cleanup task if it exists."""
            if self.cleanup_task and not self.cleanup_task.cancelled():
                try:
                    self.cleanup_task.cancel()
                    await self.cleanup_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"Error cancelling cleanup task: {e}")
                finally:
                    self.cleanup_task = None
        
        async def _send_error_to_channel(self, title: str, description: str, details: str) -> None:
            """Send a formatted error message to the channel."""
            try:
                error_msg = f"{title}\n{description}\n💡 **Info:** {details}"
                await self.ctx.channel.send(error_msg)
            except discord.HTTPException as e:
                # Use standardized logging for Discord API errors
                logger.warning(f"Discord API error sending message to {self.ctx.guild.name}: {e}")
            except Exception as e:
                # Use standardized logging for unexpected errors
                logger.error(f"Unexpected error sending message to {self.ctx.guild.name}: {type(e).__name__}: {str(e)}", exc_info=True)
        
        async def _handle_playback_error(self, error) -> None:
            """Handle playback errors and notify users."""
            try:
                error_str = str(error) if error else "Unknown playback error"
                song_title = self.current_title or "Unknown song"
                
                await self._send_error_to_channel(
                    "⚠️ **Playbook Error**",
                    f"❌ Error occurred while playing: {song_title}",
                    f"Attempting to continue with next song. Technical details: `{error_str}`"
                )
            except Exception as e:
                # Use standardized error handling for notification failures
                cog = self.bot.get_cog('MusicCog')
                if cog:
                    await cog._handle_error(
                        error=e,
                        error_type="system",
                        context_info=f"playback error notification in guild: {self.ctx.guild.name}"
                    )

        async def stop(self) -> None:
            """Stop playing music and disconnect from voice channel."""
            logger.info(f"Stopping voice state for guild: {self.ctx.guild.name}")
            
            try:
                # Cancel the audio player task first to prevent new songs from starting
                if hasattr(self, 'audio_player') and self.audio_player and not self.audio_player.cancelled():
                    self.audio_player.cancel()
                    try:
                        await self.audio_player
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        logger.error(f"Error while cancelling audio player task: {e}")
                    finally:
                        self.audio_player = None
                
                # Cancel cleanup task
                await self._cancel_cleanup_task()
                
                # Clear queue and clean up current song
                self.queue.clear()
                await self._cleanup_current_song()
                
                # Stop voice client and disconnect
                if self.voice:
                    try:
                        if self.voice.is_playing():
                            self.voice.stop()
                        await self.voice.disconnect()
                    except Exception as e:
                        logger.error(f"Error disconnecting voice client: {e}")
                    finally:
                        self.voice = None
                
                # Clean up UI elements
                await self._delete_current_message()
                
            except Exception as e:
                logger.error(f"Error during voice state cleanup: {e}", exc_info=True)
            finally:
                logger.info(f"Voice state cleanup completed for guild: {self.ctx.guild.name}")
                # Force garbage collection after cleanup
                gc.collect()

    def get_voice_state(self, ctx) -> VoiceState:
        """Get or create a voice state for the guild."""
        state = self.voice_states.get(ctx.guild.id)
        if not state or not ctx.voice_client:
            state = self.VoiceState(self.bot, ctx)
            self.voice_states[ctx.guild.id] = state
        return state
    
    # ==================================================================================
    # STANDARDIZED ERROR HANDLING SYSTEM
    # ==================================================================================
    
    async def _handle_error(self, 
                           error: Exception, 
                           error_type: str, 
                           context_info: str,
                           interaction: Optional[discord.Interaction] = None,
                           user_message: Optional[str] = None,
                           technical_details: bool = True) -> None:
        """Standardized error handling with consistent logging and user feedback.
        
        Args:
            error: The exception that occurred
            error_type: Type of error from ErrorType constants
            context_info: Context information for logging (e.g., "play command in guild: GuildName")
            interaction: Discord interaction for user feedback (optional)
            user_message: Custom user-facing message (optional)
            technical_details: Whether to include technical details for internal debugging
        """
        # Generate error ID for correlation
        error_id = f"{error_type}_{int(time.time() * 1000) % 1000000}"
        
        # Standardized logging with correlation ID
        log_msg = f"[{error_id}] {context_info}: {type(error).__name__}: {str(error)}"
        
        if error_type in [ErrorType.SYSTEM, ErrorType.AUDIO]:
            logger.error(log_msg, exc_info=True)  # Full stack trace for critical errors
        elif error_type == ErrorType.NETWORK:
            logger.warning(log_msg, exc_info=False)  # Network errors are less critical
        else:
            logger.info(log_msg, exc_info=False)  # User errors are informational
        
        # Send user feedback if interaction provided
        if interaction and not interaction.response.is_done():
            try:
                await self._send_error_response(error, error_type, interaction, user_message, error_id, technical_details)
            except Exception as feedback_error:
                logger.error(f"[{error_id}] Failed to send error feedback: {feedback_error}")
    
    async def _send_error_response(self, 
                                  error: Exception,
                                  error_type: str,
                                  interaction: discord.Interaction,
                                  user_message: Optional[str],
                                  error_id: str,
                                  technical_details: bool) -> None:
        """Send standardized error response to user."""
        # Determine emoji and base message based on error type
        error_emojis = {
            ErrorType.COMMAND: "❌",
            ErrorType.AUDIO: "🎵",
            ErrorType.NETWORK: "🌐",
            ErrorType.SYSTEM: "⚙️",
            ErrorType.USER_INPUT: "📝",
            ErrorType.PERMISSION: "🔒"
        }
        
        emoji = error_emojis.get(error_type, "⚠️")
        
        if user_message:
            message = f"{emoji} **Error**\n{user_message}"
        else:
            # Generate default message based on error type
            default_messages = {
                ErrorType.COMMAND: "Command execution failed",
                ErrorType.AUDIO: "Audio processing error occurred",
                ErrorType.NETWORK: "Network connection issue",
                ErrorType.SYSTEM: "Internal system error",
                ErrorType.USER_INPUT: "Invalid input provided",
                ErrorType.PERMISSION: "Insufficient permissions"
            }
            message = f"{emoji} **{default_messages.get(error_type, 'An error occurred')}**"
        
        # Add technical details for debugging (since bot is for internal use)
        if technical_details:
            message += f"\n🛠️ **Technical Details:** `{type(error).__name__}: {str(error)}`"
            message += f"\n🆔 **Error ID:** `{error_id}`"
        
        # Add helpful suggestions based on error type
        suggestions = {
            ErrorType.COMMAND: "💡 **Try:** Check the command syntax and try again",
            ErrorType.AUDIO: "💡 **Try:** Use a different song or check the URL",
            ErrorType.NETWORK: "💡 **Try:** Check your connection and retry in a moment",
            ErrorType.SYSTEM: "💡 **Try:** Contact the bot administrator if this persists",
            ErrorType.USER_INPUT: "💡 **Try:** Check your input format and try again",
            ErrorType.PERMISSION: "💡 **Try:** Check bot permissions or contact server admin"
        }
        
        if error_type in suggestions:
            message += f"\n{suggestions[error_type]}"
        
        await interaction.followup.send(message, ephemeral=True)
    
    async def _safe_cleanup(self, 
                           cleanup_func, 
                           context_info: str,
                           *args, **kwargs) -> bool:
        """Safely execute cleanup operations with standardized error handling.
        
        Args:
            cleanup_func: The cleanup function to execute
            context_info: Context for logging
            *args, **kwargs: Arguments for the cleanup function
            
        Returns:
            bool: True if cleanup succeeded, False otherwise
        """
        try:
            if asyncio.iscoroutinefunction(cleanup_func):
                await cleanup_func(*args, **kwargs)
            else:
                cleanup_func(*args, **kwargs)
            logger.debug(f"Cleanup completed: {context_info}")
            return True
        except Exception as e:
            await self._handle_error(
                error=e,
                error_type=ErrorType.SYSTEM,
                context_info=f"cleanup operation ({context_info})"
            )
            return False
    
    def _extract_error_details(self, error: Exception) -> tuple[str, str]:
        """Extract meaningful error category and message from various exception types.
        
        Returns:
            tuple: (error_category, user_friendly_message)
        """
        # Discord-specific errors
        if isinstance(error, discord.HTTPException):
            return ErrorType.NETWORK, f"Discord API error: {error.text or str(error)}"
        elif isinstance(error, discord.Forbidden):
            return ErrorType.PERMISSION, "Bot lacks required permissions for this action"
        elif isinstance(error, discord.NotFound):
            return ErrorType.COMMAND, "Requested resource not found"
        
        # yt-dlp specific errors
        elif hasattr(error, '__module__') and 'yt_dlp' in str(error.__module__):
            if "age-restricted" in str(error).lower():
                return ErrorType.AUDIO, "Video is age-restricted and cannot be played"
            elif "private" in str(error).lower():
                return ErrorType.AUDIO, "Video is private and cannot be accessed"
            elif "not available" in str(error).lower():
                return ErrorType.AUDIO, "Video is not available in your region"
            else:
                return ErrorType.AUDIO, f"Media extraction failed: {str(error)}"
        
        # Network-related errors
        elif isinstance(error, (aiohttp.ClientError, asyncio.TimeoutError)):
            return ErrorType.NETWORK, "Network connection timeout or error"
        
        # Input validation errors
        elif isinstance(error, ValueError):
            return ErrorType.USER_INPUT, str(error)
        
        # Permission errors
        elif isinstance(error, PermissionError):
            return ErrorType.PERMISSION, "Permission denied for this operation"
        
        # System errors
        elif isinstance(error, (OSError, IOError)):
            return ErrorType.SYSTEM, "File system or I/O error occurred"
        
        # Generic errors
        else:
            return ErrorType.SYSTEM, f"Unexpected error: {type(error).__name__}"
    
    async def _save_queue_state(self) -> None:
        """Save current queue states to persistent storage."""
        if not self.ENABLE_QUEUE_PERSISTENCE:
            return
            
        try:
            queue_data = {}
            
            for guild_id, voice_state in self.voice_states.items():
                if voice_state.queue or voice_state.current:
                    # Serialize queue data
                    queue_items = []
                    for source, title, requester in voice_state.queue:
                        # We can't serialize the actual audio source, so we'll store metadata
                        queue_items.append({
                            'title': title,
                            'requester_id': requester.id,
                            'requester_name': requester.name,
                            'timestamp': time.time()
                        })
                    
                    current_song = None
                    if voice_state.current and voice_state.current_title:
                        current_song = {
                            'title': voice_state.current_title,
                            'requester_id': voice_state.current_requester.id if voice_state.current_requester else None,
                            'requester_name': voice_state.current_requester.name if voice_state.current_requester else 'Unknown',
                            'timestamp': time.time()
                        }
                    
                    if queue_items or current_song:
                        queue_data[str(guild_id)] = {
                            'guild_name': voice_state.ctx.guild.name,
                            'current_song': current_song,
                            'queue': queue_items,
                            'saved_at': time.time()
                        }
            
            # Write to file
            async with aiofiles.open(self.PERSISTENCE_FILE, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(queue_data, indent=2, ensure_ascii=False))
                
            logger.debug(f"Queue state saved for {len(queue_data)} guilds")
            
        except Exception as e:
            logger.error(f"Failed to save queue state: {str(e)}", exc_info=True)
    
    async def _restore_queues_on_startup(self) -> None:
        """Restore saved queue states on bot startup."""
        if not self.ENABLE_QUEUE_PERSISTENCE:
            return
            
        try:
            if not self.PERSISTENCE_FILE.exists():
                logger.info("No queue persistence file found, starting with empty queues")
                return
                
            async with aiofiles.open(self.PERSISTENCE_FILE, 'r', encoding='utf-8') as f:
                content = await f.read()
                queue_data = json.loads(content)
            
            if not queue_data:
                logger.info("No saved queue data found")
                return
                
            restored_count = 0
            for guild_id_str, guild_data in queue_data.items():
                try:
                    guild_id = int(guild_id_str)
                    guild = self.bot.get_guild(guild_id)
                    
                    if not guild:
                        logger.warning(f"Guild {guild_id} ({guild_data.get('guild_name', 'Unknown')}) not found, skipping queue restoration")
                        continue
                    
                    # Check if queue data is not too old (configurable threshold)
                    max_age_hours = int(os.getenv('QUEUE_PERSISTENCE_MAX_AGE_HOURS', '24'))
                    saved_at = guild_data.get('saved_at', 0)
                    age_hours = (time.time() - saved_at) / 3600
                    
                    if age_hours > max_age_hours:
                        logger.info(f"Queue data for {guild.name} is {age_hours:.1f} hours old, skipping restoration")
                        continue
                    
                    # Send restoration notification to the guild's system channel or first text channel
                    notification_channel = guild.system_channel
                    if not notification_channel:
                        # Find first text channel bot can send messages to
                        for channel in guild.text_channels:
                            if channel.permissions_for(guild.me).send_messages:
                                notification_channel = channel
                                break
                    
                    if notification_channel:
                        queue_count = len(guild_data.get('queue', []))
                        current_song = guild_data.get('current_song')
                        
                        restore_msg = (
                            f"🔄 **Queue Restoration**\n"
                            f"Bot restarted - attempting to restore music queue\n"
                            f"📊 **Found:** {queue_count} queued songs"
                        )
                        
                        if current_song:
                            restore_msg += f"\n🎵 **Was Playing:** {current_song['title']}"
                        
                        restore_msg += f"\n⚠️ **Note:** Songs will need to be re-processed, use `/queue` to see restored list"
                        
                        await notification_channel.send(restore_msg)
                        logger.info(f"Sent queue restoration notification to {guild.name}")
                        
                        restored_count += 1
                    
                except Exception as e:
                    logger.error(f"Failed to restore queue for guild {guild_id_str}: {str(e)}")
                    continue
            
            logger.info(f"Queue restoration completed for {restored_count} guilds")
            
            # Clear the persistence file after successful restoration to avoid re-restoration
            await aiofiles.open(self.PERSISTENCE_FILE, 'w').close()
            
        except Exception as e:
            logger.error(f"Failed to restore queue states: {str(e)}", exc_info=True)
    
    async def _periodic_queue_save(self) -> None:
        """Periodically save queue state to prevent data loss."""
        if not self.ENABLE_QUEUE_PERSISTENCE:
            return
            
        save_interval = int(os.getenv('QUEUE_SAVE_INTERVAL_MINUTES', '5'))  # Default: every 5 minutes
        
        while True:
            try:
                await asyncio.sleep(save_interval * 60)  # Convert minutes to seconds
                
                # Only save if there are active queues
                active_queues = sum(1 for vs in self.voice_states.values() if vs.queue or vs.current)
                if active_queues > 0:
                    await self._save_queue_state()
                    logger.debug(f"Periodic queue save completed ({active_queues} active queues)")
                    
            except asyncio.CancelledError:
                logger.info("Periodic queue save task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in periodic queue save: {str(e)}", exc_info=True)
                # Continue the loop even if one save fails
                continue
    
    async def _cleanup_on_shutdown(self) -> None:
        """Save queue state before bot shutdown."""
        if self.ENABLE_QUEUE_PERSISTENCE:
            logger.info("Saving queue state before shutdown...")
            await self._save_queue_state()
            logger.info("Queue state saved successfully")
    
    def cog_unload(self):
        """Called when the cog is unloaded - save queue state."""
        if self.ENABLE_QUEUE_PERSISTENCE:
            # Run the cleanup in a sync way since cog_unload is not async
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Schedule the task if loop is running
                asyncio.create_task(self._cleanup_on_shutdown())
            else:
                # Run directly if no loop is running
                loop.run_until_complete(self._cleanup_on_shutdown())
    
    @commands.Cog.listener()
    async def on_app_command_error(self, interaction: discord.Interaction, error):
        """Handle application command errors, including rate limiting."""
        if isinstance(error, commands.CommandOnCooldown):
            # Convert seconds to human readable format
            remaining = int(error.retry_after)
            minutes = remaining // 60
            seconds = remaining % 60
            
            if minutes > 0:
                time_str = f"{minutes}m {seconds}s"
            else:
                time_str = f"{seconds}s"
            
            # Determine command type for better messaging
            command_name = interaction.command.name if interaction.command else "command"
            
            error_messages = {
                'play': {
                    'title': '🎵 **Play Command Cooldown**',
                    'description': f'⏳ You are adding songs too quickly!\n⚙️ **Limit:** 3 songs per 10 seconds',
                    'suggestion': 'Wait a moment before adding more songs'
                },
                'skip': {
                    'title': '⏭️ **Skip Command Cooldown**',
                    'description': f'⏳ Too many skip requests from this server!\n⚙️ **Limit:** 5 skips per 10 seconds',
                    'suggestion': 'Let the current song play for a bit'
                },
                'remove': {
                    'title': '🗑️ **Remove Command Cooldown**',
                    'description': f'⏳ You are removing songs too quickly!\n⚙️ **Limit:** 3 removes per 5 seconds',
                    'suggestion': 'Wait before removing more songs'
                },
                'clear': {
                    'title': '🗑️ **Clear Command Cooldown**',
                    'description': f'⏳ Queue clearing is limited to prevent spam!\n⚙️ **Limit:** 2 clears per 30 seconds',
                    'suggestion': 'Be more selective with queue management'
                }
            }
            
            message_info = error_messages.get(command_name, {
                'title': f'⏳ **{command_name.title()} Command Cooldown**',
                'description': f'You are using this command too frequently!',
                'suggestion': 'Please wait before using this command again'
            })
            
            cooldown_message = (
                f"{message_info['title']}\n"
                f"{message_info['description']}\n"
                f"💡 **Suggestion:** {message_info['suggestion']}\n"
                f"⏰ **Try Again In:** {time_str}"
            )
            
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(cooldown_message, ephemeral=True)
                else:
                    await interaction.response.send_message(cooldown_message, ephemeral=True)
            except discord.HTTPException as e:
                logger.error(f"Failed to send cooldown message: {e}")
                
            logger.info(f"Rate limit hit for {command_name} command by {interaction.user.name} in {interaction.guild.name}. Retry after: {time_str}")
        
        elif isinstance(error, Exception):
            # Log unexpected errors but don't expose them to users
            logger.error(f"Unexpected error in {interaction.command.name if interaction.command else 'unknown'} command: {str(error)}", exc_info=True)

    async def auto_join(self, interaction: discord.Interaction):
        """Automatically join the user's voice channel"""
        if interaction.user.voice:
            channel = interaction.user.voice.channel
            try:
                if interaction.guild.voice_client is None:
                    voice_client = await channel.connect()
                else:
                    voice_client = await interaction.guild.voice_client.move_to(channel)
                logger.info(f"Auto-joined voice channel: {channel.name} in {interaction.guild.name}")
                return voice_client  # Return the voice client
            except Exception as e:
                logger.error(f"Failed to auto-join voice channel: {str(e)}", exc_info=True)
                await interaction.followup.send(f"Failed to join voice channel: {str(e)}")
                return None
        else:
            await interaction.followup.send('You need to be in a voice channel to play music!')
            return None

    async def process_url(self, query: str) -> Tuple[str, str, str]:
        """
        Process a query (YouTube or SoundCloud URL or search terms) and return audio URL, title and platform.
        
        Args:
            query: The search query or URL to process
            
        Returns:
            Tuple containing (audio_url, title, platform)
            
        Raises:
            ValueError: If processing fails or no results are found
        """
        # Check if it's a YouTube video ID that's known to be age-restricted
        youtube_id_match = re.search(r'(?:v=|\/)([0-9A-Za-z_-]{11}).*', query)
        video_id = None
        
        if youtube_id_match:
            video_id = youtube_id_match.group(1)
        elif len(query) == 11 and re.match(r'^[0-9A-Za-z_-]{11}$', query):
            video_id = query
            
        if video_id:
            # Try YouTube Music first for potentially age-restricted content
            try:
                return await self._try_youtube_music(query, video_id)
            except Exception as e:
                logger.info(f"YouTube Music attempt failed, falling back to regular process: {e}")
                # Continue with regular processing if YouTube Music fails
        
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio[ext=opus]/bestaudio/best',
            'noplaylist': True,
            'nocheckcertificate': True,
            'ignoreerrors': False,
            'quiet': True,
            'no_warnings': True,
            'default_search': 'auto',
            'source_address': '0.0.0.0',
            'extract_flat': False,
            'socket_timeout': 60,  # Increased socket timeout
            'retries': 10,  # More retries
            'extractor_retries': 10,  # More extractor retries
            'fragment_retries': 10,  # Added for handling segmented longer videos
            'skip_download': True,
            'max_downloads': 1,
            'youtube_include_dash_manifest': False,
            'cachedir': False,
            'prefer_ffmpeg': True,
            'age_limit': 99,  # Maximum age limit (more aggressive than 0)
            'cookiefile': 'cookies.txt',  # Use the provided cookie file
            'geo_bypass': True,  # Bypass geo-restrictions
            'geo_bypass_country': 'US',  # Try US IP
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'web', 'tv'],  # Try multiple clients
                    'player_skip': ['webpage', 'configs'],
                    'ssl_verify': False,
                    'innertube_key': 'AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8'  # Public Innertube API key
                }
            },
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'opus',
                'preferredquality': '128'
            }]
        }

        try:
            async with timeout(120):  # Increase extraction timeout to 2 minutes
                return await self._extract_audio_info(query, ydl_opts)
        except asyncio.TimeoutError:
            logger.error(f"Timeout while processing query: {query}")
            raise ValueError("Operation timed out while processing your request. Please try again.")
        except Exception as e:
            logger.error(f"Error processing query '{query}': {str(e)}", exc_info=True)
            raise ValueError(f"Error processing request: {str(e)}")
            
    async def _extract_audio_info(self, query: str, ydl_opts: dict) -> Tuple[str, str, str]:
        """
        Extract audio information using youtube-dl.
        
        Args:
            query: The query to process
            ydl_opts: Options for youtube-dl
            
        Returns:
            Tuple of (url, title, platform)
        """
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Handle search query vs direct URL
            if not query.startswith(('http://', 'https://')):
                return await self._handle_search_query(ydl, query)
            else:
                return await self._handle_direct_url(ydl, query)
    
    async def _handle_search_query(self, ydl, query: str) -> Tuple[str, str, str]:
        """Process a search query (non-URL input)."""
        logger.info(f"Searching for: {query}")
        
        # Try YouTube first
        try:
            try:
                info = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: ydl.extract_info(f"ytsearch:{query}", download=False)
                )
                if info and info.get('entries'):
                    info = info['entries'][0]
                    return self._extract_url_and_title(info, 'YouTube')
            except yt_dlp.utils.DownloadError as e:
                error_msg = str(e)
                if "Sign in to confirm your age" in error_msg or "inappropriate for some users" in error_msg:
                    logger.warning(f"Age-restricted content detected in search: {query}")
                    
                    # Extract the video ID if possible
                    video_id = None
                    title = None
                    if info and info.get('entries') and len(info['entries']) > 0:
                        entry = info['entries'][0]
                        video_id = entry.get('id')
                        title = entry.get('title')
                    
                    # If we have a title, try SoundCloud as fallback
                    if title:
                        logger.info(f"Trying SoundCloud fallback for age-restricted YouTube search result: {title}")
                        try:
                            return await self._fallback_to_soundcloud(title)
                        except Exception as sc_error:
                            logger.warning(f"SoundCloud fallback failed: {sc_error}")
                            # If SoundCloud fails and we have a video ID, suggest YouTube Music
                            if video_id:
                                ytmusic_url = f"https://music.youtube.com/watch?v={video_id}"
                                raise ValueError(f"The video found is age-restricted and couldn't be played on YouTube or SoundCloud. As a last resort, try YouTube Music: {ytmusic_url}")
                    
                    # If we couldn't extract a title or SoundCloud failed
                    raise ValueError("The song found is age-restricted and couldn't be played. Try searching for a different version.")
                logger.warning(f"YouTube search failed: {e}")
        except ValueError:
            raise
        except Exception as e:
            logger.warning(f"YouTube search failed: {e}")
        
        # If YouTube fails, try SoundCloud
        try:
            info = await asyncio.get_event_loop().run_in_executor(
                None, lambda: ydl.extract_info(f"scsearch:{query}", download=False)
            )
            if info and info.get('entries'):
                info = info['entries'][0]
                return self._extract_url_and_title(info, 'SoundCloud')
        except Exception as e:
            logger.warning(f"SoundCloud search failed: {e}")
        
        # If both fail, raise error
        raise ValueError(f"Could not find any results for '{query}'")
    
    async def _handle_direct_url(self, ydl, url: str) -> Tuple[str, str, str]:
        """Process a direct URL input."""
        try:
            info = await asyncio.get_event_loop().run_in_executor(
                None, lambda: ydl.extract_info(url, download=False)
            )
            if not info:
                raise ValueError("Could not get audio information")
            
            extractor = info.get('extractor', '').lower()
            if extractor == 'soundcloud':
                platform = 'SoundCloud'
            elif extractor in ['lbry', 'odysee']:
                platform = 'Odysee'
            else:
                platform = 'YouTube'  # Default fallback
            
            return self._extract_url_and_title(info, platform)
        except yt_dlp.utils.DownloadError as e:
            error_msg = str(e)
            if "Sign in to confirm your age" in error_msg or "inappropriate for some users" in error_msg:
                logger.warning(f"Age-restricted content detected: {url}")
                
                # Try to extract video ID for suggesting alternatives
                video_id = None
                youtube_id_match = re.search(r'(?:v=|\/)([0-9A-Za-z_-]{11}).*', url)
                if youtube_id_match:
                    video_id = youtube_id_match.group(1)
                
                # First, try to get the video title
                title = None
                if video_id:
                    title = await self._get_video_title(video_id)
                
                # If we have a title, try SoundCloud as fallback
                if title:
                    logger.info(f"Trying SoundCloud fallback for age-restricted YouTube video: {title}")
                    try:
                        return await self._fallback_to_soundcloud(title)
                    except Exception as sc_error:
                        logger.warning(f"SoundCloud fallback failed: {sc_error}")
                        # If SoundCloud fails, suggest YouTube Music as last resort
                        if video_id:
                            ytmusic_url = f"https://music.youtube.com/watch?v={video_id}"
                            raise ValueError(f"This video is age-restricted and couldn't be played on YouTube or SoundCloud. As a last resort, try YouTube Music: {ytmusic_url}")
                
                # If we couldn't extract a title or SoundCloud failed
                raise ValueError("This video is age-restricted and couldn't be played. Try a different version of the song.")
            raise
    
    def _extract_url_and_title(self, info: dict, platform: str) -> Tuple[str, str, str]:
        """Extract the streaming URL and title from the info dictionary."""
        url = info.get('url')
        if not url:
            formats = info.get('formats', [])
            for f in formats:
                if f.get('ext') in ['opus', 'm4a', 'mp3']:
                    url = f.get('url')
                    break
            if not url and formats:
                url = formats[0].get('url')
                
        title = info.get('title', 'Unknown title')
        return url, title, platform

    def _validate_and_sanitize_query(self, query: str) -> Tuple[bool, str, Optional[str]]:
        """Validate and sanitize the input query.
        
        Returns:
            Tuple[bool, str, Optional[str]]: (is_valid, sanitized_query, error_message)
        """
        # Check for empty or whitespace-only input
        if not query or not query.strip():
            return False, "", "Query cannot be empty"
        
        # Sanitize the query
        sanitized = query.strip()
        
        # Check for reasonable length limits
        MAX_SEARCH_LENGTH = 500  # Reasonable limit for search terms
        MAX_URL_LENGTH = 2000    # YouTube/SoundCloud URLs can be long with parameters
        
        # Determine if it's likely a URL or search term
        is_url = sanitized.startswith(('http://', 'https://', 'www.'))
        
        if is_url:
            if len(sanitized) > MAX_URL_LENGTH:
                return False, "", f"URL is too long (max {MAX_URL_LENGTH} characters)"
            
            # Basic URL validation - check for common URL patterns
            url_pattern = re.compile(
                r'^(https?://)?(www\.)?(youtube\.com|youtu\.be|soundcloud\.com|music\.youtube\.com|odysee\.com|lbry\.tv)'
            )
            if not url_pattern.search(sanitized.lower()):
                # Allow the URL through but log it for monitoring
                logger.info(f"Non-standard URL detected: {sanitized[:100]}...")
        else:
            # It's a search query
            if len(sanitized) > MAX_SEARCH_LENGTH:
                return False, "", f"Search query is too long (max {MAX_SEARCH_LENGTH} characters)"
            
            # Remove potentially problematic characters but keep most Unicode for international songs
            # Remove control characters and some potentially problematic symbols
            sanitized = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', sanitized)
            
            # Check for minimum meaningful length
            if len(sanitized.strip()) < 2:
                return False, "", "Search query is too short (minimum 2 characters)"
        
        # Check for potential injection patterns (basic protection)
        suspicious_patterns = [
            r'javascript:',
            r'data:',
            r'vbscript:',
            r'<script',
            r'</script>'
        ]
        
        for pattern in suspicious_patterns:
            if re.search(pattern, sanitized, re.IGNORECASE):
                return False, "", "Query contains suspicious content"
        
        return True, sanitized, None
    
    @app_commands.command(name='play', description='Play a song by URL or search query')
    @app_commands.describe(query="YouTube/SoundCloud/Odysee URL or search terms")
    @commands.cooldown(rate=3, per=10, type=commands.BucketType.user)  # 3 commands per 10 seconds per user
    async def play(self, interaction: discord.Interaction, query: str):
        """Plays a song from YouTube or SoundCloud, or searches for a song"""
        await interaction.response.defer()
        
        # Validate and sanitize input
        is_valid, sanitized_query, error_message = self._validate_and_sanitize_query(query)
        if not is_valid:
            await interaction.followup.send(
                f"❌ **Invalid Input**\n"
                f"{error_message}\n"
                f"💡 **Try:**\n"
                f"• Use a valid YouTube or SoundCloud URL\n"
                f"• Enter meaningful search terms (2-500 characters)\n"
                f"• Avoid special characters or extremely long queries"
            )
            return

        # Get or join voice channel
        voice_client = await self._ensure_voice_client(interaction)
        if not voice_client:
            return

        # Create mock context for compatibility with get_voice_state
class MockContext:
    def __init__(self, guild, voice_client, channel):
        self.guild = guild
        self.voice_client = voice_client
        self.channel = channel

        mock_ctx = MockContext(interaction.guild, voice_client, interaction.channel)
        try:
            # Log sanitized query (truncated for security)
            query_log = sanitized_query if len(sanitized_query) <= 100 else f"{sanitized_query[:100]}..."
            logger.info(f"Attempting to play: {query_log} in {interaction.guild.name}")
            
            # Process the URL/query with enhanced error handling
            try:
                audio_url, title, platform = await self.process_url(sanitized_query)
            except ValueError as ve:
                error_msg = str(ve)
                logger.warning(f"URL/search processing failed for '{query}': {error_msg}")
                
                # Provide contextual error messages based on error type
                if "Could not find any results" in error_msg:
                    await interaction.followup.send(
                        f"🔍 **Search Failed**\n"
                        f"❌ No results found for: `{query}`\n"
                        f"💡 **Try:**\n"
                        f"• Different search terms\n"
                        f"• Direct YouTube/SoundCloud URL\n"
                        f"• Check spelling and try again\n"
                        f"🛠️ **Technical Details:** `{error_msg}`"
                    )
                elif "age-restricted" in error_msg.lower():
                    await interaction.followup.send(
                        f"🔞 **Age-Restricted Content**\n"
                        f"❌ Cannot play age-restricted video\n"
                        f"💡 **Try:**\n"
                        f"• Search for the song name instead of using the URL\n"
                        f"• Use a different version of the song\n"
                        f"• Check if cookies.txt is configured for age-restricted content\n"
                        f"🛠️ **Technical Details:** `{error_msg}`"
                    )
                elif "timeout" in error_msg.lower():
                    await interaction.followup.send(
                        f"⏱️ **Processing Timeout**\n"
                        f"❌ Request took too long to process\n"
                        f"💡 **Try:**\n"
                        f"• Try again in a moment\n"
                        f"• Use a different source/URL\n"
                        f"• Check your internet connection\n"
                        f"🛠️ **Technical Details:** `{error_msg}`"
                    )
                else:
                    await interaction.followup.send(
                        f"❌ **Processing Error**\n"
                        f"Failed to process: `{query}`\n"
                        f"💡 **Try:** Different search terms or URL\n"
                        f"🛠️ **Technical Details:** `{error_msg}`"
                    )
                return
            
            # Create audio source with error handling
            try:
                source = await self._create_audio_source(audio_url)
            except Exception as audio_error:
                logger.error(f"Audio source creation failed for {title}: {str(audio_error)}", exc_info=True)
                await interaction.followup.send(
                    f"🎵 **Audio Processing Error**\n"
                    f"❌ Failed to create audio source for: `{title}`\n"
                    f"💡 **Try:**\n"
                    f"• Try a different version of the song\n"
                    f"• Check if FFmpeg is properly installed\n"
                    f"• Try again in a moment\n"
                    f"🛠️ **Technical Details:** `{str(audio_error)}`"
                )
                return
        
            # Check queue size limits before adding
            state = self.get_voice_state(mock_ctx)
            
            # Check total queue size limit
            if len(state.queue) >= self.MAX_QUEUE_SIZE:
                await interaction.followup.send(
                    f"🚫 **Queue Full**\n"
                    f"❌ Queue has reached maximum size ({self.MAX_QUEUE_SIZE} songs)\n"
                    f"💡 **Try:** Wait for some songs to finish or use `/clear` to clear the queue\n"
                    f"📊 **Current Queue Size:** {len(state.queue)}/{self.MAX_QUEUE_SIZE}"
                )
                return
                
            # Check per-user queue limit
            user_songs_in_queue = sum(1 for _, _, requester in state.queue if requester.id == interaction.user.id)
            if user_songs_in_queue >= self.USER_QUEUE_LIMIT:
                await interaction.followup.send(
                    f"👤 **User Queue Limit Reached**\n"
                    f"❌ You already have {user_songs_in_queue} songs in the queue (limit: {self.USER_QUEUE_LIMIT})\n"
                    f"💡 **Try:** Wait for your songs to play or remove some with `/remove`\n"
                    f"📊 **Your Songs:** {user_songs_in_queue}/{self.USER_QUEUE_LIMIT}"
                )
                return
            
            # Add to queue
            state.voice = voice_client
            state.queue.append((source, f"{title} ({platform})", interaction.user))
            
            # Signal the audio player that queue has new items (fixes CPU polling issue)
            state.queue_ready.set()
            
            # Save queue state after adding song
            if self.ENABLE_QUEUE_PERSISTENCE:
                asyncio.create_task(self._save_queue_state())
            
            queue_position = len(state.queue)
            total_songs = queue_position + (1 if state.current else 0)
            
            logger.info(f"Added to queue: {title} from {platform} in {interaction.guild.name} (position {queue_position})")
            await interaction.followup.send(
                f'✅ **Added to Queue**\n'
                f'🎵 {title} ({platform})\n'
                f'👤 Requested by {interaction.user.display_name}\n'
                f'📊 Queue position: {queue_position} | Total songs: {total_songs}'
            )

        except discord.HTTPException as http_error:
            error_type, user_message = self._extract_error_details(http_error)
            await self._handle_error(
                error=http_error,
                error_type=error_type,
                context_info=f"play command in guild: {interaction.guild.name}",
                interaction=interaction,
                user_message=f"Failed to process: `{sanitized_query}`\n{user_message}"
            )
        except Exception as e:
            error_type, user_message = self._extract_error_details(e)
            await self._handle_error(
                error=e,
                error_type=error_type,
                context_info=f"play command in guild: {interaction.guild.name}",
                interaction=interaction,
                user_message=f"Failed to process: `{sanitized_query}`\n{user_message}"
                f"🛠️ **Technical Details:** `{type(e).__name__}: {str(e)}`"
            )
            
    async def _ensure_voice_client(self, interaction: discord.Interaction) -> Optional[discord.VoiceClient]:
        """Ensure we have a valid voice client, joining if necessary."""
        if not interaction.guild.voice_client:
            return await self.auto_join(interaction)
        return interaction.guild.voice_client
            
    async def _create_audio_source(self, audio_url: str) -> discord.FFmpegOpusAudio:
        """Create an audio source from the given URL."""
        ffmpeg_options = {
            'options': '-vn -b:a 128k -bufsize 1024k -maxrate 192k -ar 48000 -ac 2 -af loudnorm=I=-16:TP=-1.5:LRA=11 -fflags +genpts -thread_queue_size 1024',
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -reconnect_at_eof 1 -timeout 60000000 -multiple_requests 1'
        }
        
        return await discord.FFmpegOpusAudio.from_probe(
            audio_url, 
            **ffmpeg_options,
            method='fallback'
        )

    @app_commands.command(name='skip', description='Skip the current song')
    @commands.cooldown(rate=5, per=10, type=commands.BucketType.guild)  # 5 skips per 10 seconds per guild
    async def skip(self, interaction: discord.Interaction):
        """Skips the current song"""
        try:
            if interaction.guild.voice_client is None:
                return await interaction.response.send_message(
                    f"🔌 **Not Connected**\n"
                    f"❌ Bot is not connected to any voice channel\n"
                    f"💡 **Try:** Use `/play` to start playing music first"
                )

            ctx = await self.bot.get_context(interaction)
            state = self.get_voice_state(ctx)
            
            if state.voice and state.voice.is_playing():
                current_song = state.current_title or "current song"
                state.voice.stop()
                logger.info(f"Skipped song '{current_song}' in {interaction.guild.name}")
                await interaction.response.send_message(
                    f"⏭️ **Song Skipped**\n"
                    f"🎵 Skipped: {current_song}\n"
                    f'👤 Requested by {interaction.user.display_name}\n'
                )
            else:
                await interaction.response.send_message(
                    f"⏸️ **Nothing Playing**\n"
                    f"❌ No music is currently playing\n"
                    f"💡 **Try:** Use `/play` to add songs to the queue"
                )
                
        except Exception as e:
            error_type, user_message = self._extract_error_details(e)
            await self._handle_error(
                error=e,
                error_type=error_type,
                context_info=f"skip command in guild: {interaction.guild.name}",
                interaction=interaction,
                user_message=f"Failed to skip the current song\n{user_message}"
            )

    @app_commands.command(name='queue', description='Show the current queue')
    async def queue(self, interaction: discord.Interaction):
        """Shows the current queue"""
        ctx = await self.bot.get_context(interaction)
        state = self.get_voice_state(ctx)
        
        if not state.current and len(state.queue) == 0:
            await interaction.response.send_message('Queue is empty.')
            return

        # Format the queue as a list of songs
        queue_message = self._format_queue(state)
        await interaction.response.send_message(queue_message)
        
    def _format_queue(self, state: VoiceState) -> str:
        """Format the queue as a string for display."""
        queue_list = []
        if state.current:
            queue_list.append(f"**Currently Playing:** {state.current_title} (requested by {state.current_requester.name})")
        
        if state.queue:
            queue_list.append("\n**Queue:**")
            for i, (_, title, requester) in enumerate(state.queue, 1):
                queue_list.append(f"{i}. {title} (requested by {requester.name})")

        return '\n'.join(queue_list)

    @app_commands.command(name='clear', description='Clear the queue')
    @commands.cooldown(rate=2, per=30, type=commands.BucketType.guild)  # 2 clears per 30 seconds per guild
    async def clear(self, interaction: discord.Interaction):
        """Clears the queue"""
        ctx = await self.bot.get_context(interaction)
        state = self.get_voice_state(ctx)
        queue_size = len(state.queue)
        state.queue.clear()
        
        # Save queue state after clearing
        if self.ENABLE_QUEUE_PERSISTENCE:
            asyncio.create_task(self._save_queue_state())
            
        logger.info(f"Queue cleared in {interaction.guild.name}")
        await interaction.response.send_message(
            f'✅ **Queue Cleared**\n'
            f'🗑️ Removed {queue_size} song(s) from queue\n'
            f'👤 Requested by {interaction.user.display_name}\n'
        )

    @app_commands.command(name='leave', description='Leave the voice channel')
    async def leave(self, interaction: discord.Interaction):
        """Leaves the voice channel"""
        try:
            if not interaction.guild.voice_client:
                await interaction.response.send_message(
                    f"🔌 **Not Connected**\n"
                    f"❌ Bot is not connected to any voice channel\n"
                    f"💡 **Info:** Nothing to disconnect from"
                )
                return
                
            ctx = await self.bot.get_context(interaction)
            state = self.get_voice_state(ctx)
            
            # Get info about what's being stopped
            queue_size = len(state.queue)
            current_song = state.current_title
            
            # Stop everything and clean up
            await state.stop()
            
            # Clean up voice state
            if interaction.guild.id in self.voice_states:
                del self.voice_states[interaction.guild.id]
            
            # Provide informative feedback
            status_info = []
            if current_song:
                status_info.append(f"🎵 Stopped: {current_song}")
            if queue_size > 0:
                status_info.append(f"🗑️ Cleared {queue_size} song(s) from queue")
            
            response = f"👋 **Left Voice Channel**\n✅ Successfully disconnected"
            if status_info:
                response += "\n" + "\n".join(status_info)
            response += f'👤 Requested by {interaction.user.display_name}\n'
            
            await interaction.response.send_message(response)
            logger.info(f"Left voice channel in {interaction.guild.name} (requested by {interaction.user.name})")
            
        except Exception as e:
            error_type, user_message = self._extract_error_details(e)
            await self._handle_error(
                error=e,
                error_type=error_type,
                context_info=f"leave command in guild: {interaction.guild.name}",
                interaction=interaction,
                user_message=f"Error occurred while leaving voice channel\n{user_message}"
            )

    @app_commands.command(name='remove', description='Remove a specific song from the queue by its position number')
    @app_commands.describe(position="Position number of the song to remove (1-based)")
    @commands.cooldown(rate=3, per=5, type=commands.BucketType.user)  # 3 removes per 5 seconds per user
    async def remove(self, interaction: discord.Interaction, position: int):
        """Removes a specific song from the queue"""
        try:
            ctx = await self.bot.get_context(interaction)
            state = self.get_voice_state(ctx)
            
            if len(state.queue) == 0:
                await interaction.response.send_message(
                    f"📄 **Queue Empty**\n"
                    f"❌ The music queue is currently empty\n"
                    f"💡 **Try:** Use `/play` to add songs to the queue first"
                )
                return
                
            if position < 1 or position > len(state.queue):
                await interaction.response.send_message(
                    f"🔢 **Invalid Position**\n"
                    f"❌ Position `{position}` is not valid\n"
                    f"💡 **Valid Range:** 1 to {len(state.queue)}\n"
                    f"📄 Use `/queue` to see current songs"
                )
                return
                
            # Remove the song at the specified position
            removed_song = self._remove_song_from_queue(state, position)
            _, title, requester = removed_song
            
            # Save queue state after removing song
            if self.ENABLE_QUEUE_PERSISTENCE:
                asyncio.create_task(self._save_queue_state())
            
            logger.info(f"Removed song '{title}' at position {position} from queue in {interaction.guild.name}")
            await interaction.response.send_message(
                f"✅ **Song Removed**\n"
                f"🗑️ Removed: {title}\n"
                f"👤 Originally requested by {requester.name}\n"
                f"🔢 Position: {position}"
            )
            
        except Exception as e:
            error_type, user_message = self._extract_error_details(e)
            await self._handle_error(
                error=e,
                error_type=error_type,
                context_info=f"remove command (position {position}) in guild: {interaction.guild.name}",
                interaction=interaction,
                user_message=f"Failed to remove song at position {position}\n{user_message}"
            )
            
    def _remove_song_from_queue(self, state: VoiceState, position: int):
        """Remove a song from the queue at the specified position."""
        # Convert queue to list to remove specific index
        queue_list = list(state.queue)
        removed_song = queue_list.pop(position - 1)  # -1 because user input is 1-based
        state.queue = deque(queue_list)
        return removed_song

    @app_commands.command(name='help', description='Show all available commands')
    async def help(self, interaction: discord.Interaction):
        """Shows all available commands and their descriptions"""
        embed = self._create_help_embed()
        await interaction.response.send_message(embed=embed)
        
    def _create_help_embed(self) -> discord.Embed:
        """Create the help embed with all commands and descriptions."""
        embed = discord.Embed(
            title="🎵 Music Bot Commands",
            description="Here are all the available commands:",
            color=discord.Color.blue()
        )

        commands = {
            "🎵 /play [query]": "Play a song by:\n• Searching for a song name (e.g., `/play despacito`)\n• Using a YouTube URL (e.g., `/play https://youtube.com/...`)\n• Using a SoundCloud URL (e.g., `/play https://soundcloud.com/...`)",
            "⏭️ /skip": "Skip the currently playing song",
            "📋 /queue": "Show the current music queue and who requested each song",
            "🗑️ /clear": "Clear all songs from the queue",
            "❌ /remove [number]": "Remove a specific song from the queue by its position\nExample: `/remove 2` removes the second song",
            "👋 /leave": "Make the bot leave the voice channel",
            "❓ /help": "Show this help message"
        }

        for cmd, desc in commands.items():
            embed.add_field(name=cmd, value=desc, inline=False)

        embed.set_footer(text="Bot made with ❤️ | Supports both YouTube and SoundCloud links and searches")
        return embed 

    async def _try_youtube_music(self, original_query: str, video_id: str) -> Tuple[str, str, str]:
        """Try to use YouTube Music as a fallback for age-restricted content"""
        # Special handling for known problematic video IDs
        if video_id == "8jZLYF7WNKs":  # The video ID that was causing issues
            logger.info(f"Using special handling for problematic video ID: {video_id}")
            return await self._extract_using_invidious(video_id)
        
        ytmusic_url = f"https://music.youtube.com/watch?v={video_id}"
        logger.info(f"Attempting YouTube Music URL: {ytmusic_url}")
        
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio[ext=opus]/bestaudio/best',
            'noplaylist': True,
            'nocheckcertificate': True,
            'quiet': True,
            'default_search': 'auto',
            'socket_timeout': 60,
            'retries': 10,
            'skip_download': True,
            'cachedir': False,
            'age_limit': 99,
            'cookiefile': 'cookies.txt',
            'geo_bypass': True,
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'web'],
                    'player_skip': ['webpage', 'configs'],
                    'ssl_verify': False
                }
            }
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: ydl.extract_info(ytmusic_url, download=False)
                )
                
                if not info:
                    raise ValueError("Could not extract audio from YouTube Music")
                
                url = info.get('url')
                if not url:
                    formats = info.get('formats', [])
                    for f in formats:
                        if f.get('ext') in ['opus', 'm4a', 'mp3']:
                            url = f.get('url')
                            break
                    if not url and formats:
                        url = formats[0].get('url')
                
                title = info.get('title', 'Unknown title')
                return url, title, 'YouTube Music'
        except Exception as e:
            logger.warning(f"YouTube Music extraction failed: {e}")
            raise ValueError(f"Failed to extract audio from YouTube Music: {str(e)}")

    async def _extract_using_invidious(self, video_id: str) -> Tuple[str, str, str]:
        """Use Invidious instances to extract audio from problematic videos"""
        # List of public Invidious instances
        instances = [
            "https://invidious.snopyta.org",
            "https://yewtu.be",
            "https://invidious.kavin.rocks",
            "https://inv.riverside.rocks",
            "https://yt.artemislena.eu",
            "https://invidious.flokinet.to"
        ]
        
        # First, try to get the video title for potential fallback to SoundCloud
        title = await self._get_video_title(video_id)
        
        # Shuffle the instances to distribute load
        random.shuffle(instances)
        
        errors = []
        
        # Try each instance until one works
        for instance in instances:
            try:
                api_url = f"{instance}/api/v1/videos/{video_id}"
                logger.info(f"Trying Invidious instance: {api_url}")
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(api_url, timeout=10) as response:
                        if response.status == 200:
                            data = await response.json()
                            title = data.get('title', title or 'Unknown title')
                            
                            # Look for audio formats
                            adaptiveFormats = data.get('adaptiveFormats', [])
                            audioFormats = [f for f in adaptiveFormats if f.get('type', '').startswith('audio')]
                            
                            if audioFormats:
                                # Sort by bitrate and get the best one
                                bestAudio = max(audioFormats, key=lambda x: x.get('bitrate', 0))
                                url = bestAudio.get('url')
                                
                                if url:
                                    logger.info(f"Successfully extracted audio via Invidious: {title}")
                                    return url, title, 'YouTube (via Invidious)'
                        
                        logger.warning(f"Failed to extract from {instance}: {response.status}")
                        errors.append(f"HTTP {response.status} from {instance}")
            
            except Exception as e:
                logger.warning(f"Error with Invidious instance {instance}: {str(e)}")
                errors.append(f"{instance}: {str(e)}")
                continue
        
        # If all instances fail, try SoundCloud as fallback if we have a title
        if title and title != 'Unknown title':
            logger.info(f"All Invidious instances failed. Trying SoundCloud search for: {title}")
            try:
                return await self._fallback_to_soundcloud(title)
            except Exception as e:
                logger.warning(f"SoundCloud fallback also failed: {e}")
                # Fall through to the error
        
        # If both YouTube and SoundCloud fail, raise an error with details
        error_detail = "; ".join(errors)
        raise ValueError(f"Could not play age-restricted YouTube video and SoundCloud fallback failed. Please try a different video. Details: {error_detail}")
        
    async def _get_video_title(self, video_id: str) -> Optional[str]:
        """Try to get the title of a YouTube video even if we can't play it"""
        try:
            # Use a simple request to get video info without downloading
            simple_opts = {
                'skip_download': True,
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,
                'force_generic_extractor': True,
            }
            
            with yt_dlp.YoutubeDL(simple_opts) as ydl:
                result = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                )
                if result:
                    return result.get('title')
        except:
            # Try alternative method - scrape title from metadata
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"https://www.youtube.com/oembed?url=http://www.youtube.com/watch?v={video_id}&format=json") as response:
                        if response.status == 200:
                            data = await response.json()
                            return data.get('title')
            except:
                pass
        return None
        
    async def _fallback_to_soundcloud(self, title: str) -> Tuple[str, str, str]:
        """Search for a song on SoundCloud when YouTube fails"""
        logger.info(f"Attempting SoundCloud search for: {title}")
        
        # Clean up the title for better search results
        search_query = self._clean_title_for_search(title)
        
        # Set up yt-dlp for SoundCloud search
        ydl_opts = {
            'format': 'bestaudio',
            'quiet': True,
            'no_warnings': True,
            'default_search': 'scsearch',
            'skip_download': True,
            'extract_flat': False,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'opus',
                'preferredquality': '128'
            }]
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Search SoundCloud for the song
            search_results = await asyncio.get_event_loop().run_in_executor(
                None, lambda: ydl.extract_info(f"scsearch5:{search_query}", download=False)
            )
            
            if not search_results or not search_results.get('entries'):
                raise ValueError(f"No results found on SoundCloud for '{search_query}'")
            
            # Get the first result
            first_result = search_results['entries'][0]
            result_url = first_result.get('url')
            
            # Extract full info for the first result
            info = await asyncio.get_event_loop().run_in_executor(
                None, lambda: ydl.extract_info(result_url, download=False)
            )
            
            if not info:
                raise ValueError("Could not extract SoundCloud audio information")
            
            sc_url = info.get('url')
            sc_title = info.get('title', 'Unknown SoundCloud track')
            
            return sc_url, sc_title, 'SoundCloud'
    
    def _clean_title_for_search(self, title: str) -> str:
        """Clean a YouTube title to get better search results on SoundCloud"""
        # Remove common patterns in YouTube titles that might not be in SoundCloud titles
        patterns = [
            r'\(Official Video\)',
            r'\(Official Music Video\)',
            r'\(Official Audio\)',
            r'\(Lyrics\)',
            r'\(Lyric Video\)',
            r'\(Audio\)',
            r'\[.*?\]',  # Anything in square brackets
            r'ft\..*$', r'feat\..*$',  # Feature credits often differ between platforms
            r'HD', r'HQ', r'4K',
            r'VEVO',
        ]
        
        result = title
        for pattern in patterns:
            result = re.sub(pattern, '', result, flags=re.IGNORECASE)
        
        # Remove any extra whitespace and trim
        result = re.sub(r'\s+', ' ', result).strip()
        