"""
Audio Player Module

This module handles all audio playback functionality including:
- VoiceState management for each guild
- Audio player task that processes queues
- Voice channel connection and disconnection
- Audio source management and cleanup
- Now playing messages and status updates
"""

import asyncio
import logging
import os
import time
from typing import Any, Dict, Optional

import discord
from discord.ext import commands

# Import smart garbage collection
from .resource_optimizer import smart_garbage_collect

if TYPE_CHECKING:
    from .queue_manager import QueueManager
    from .error_handler import ErrorHandler

logger = logging.getLogger('AudioPlayer')


class VoiceState:
    """Manages voice state and audio playback for a single guild."""
    
    def __init__(self, bot, ctx, queue_manager, error_handler):
        """Initialize VoiceState for a guild."""
        self.bot = bot
        self.ctx = ctx
        self.queue_manager = queue_manager
        self.error_handler = error_handler
        
        # Current song state
        self.current = None
        self.current_title: Optional[str] = None
        self.current_requester: Optional[discord.Member] = None
        self.current_message: Optional[discord.Message] = None
        
        # Voice client and control
        self.voice = ctx.voice_client
        self.next = asyncio.Event()
        self.queue_ready = asyncio.Event()  # Event to signal when queue has items
        
        # Efficient activity tracking system
        self.last_activity_time = asyncio.get_event_loop().time()
        self.activity_timeout = 180.0  # 3 minutes
        self.cleanup_check_interval = 30.0  # Check every 30 seconds instead of one 180s wait
        self.is_cleanup_running = False
        
        # Auto-leave empty channel configuration
        # CONFIG: Enable automatic leaving of empty voice channels (default: true)
        self.auto_leave_empty = os.getenv('AUTO_LEAVE_EMPTY_CHANNEL', 'true').lower() == 'true'
        # CONFIG: Delay before leaving empty channel in seconds (default: 10s for smooth transitions)
        self.empty_channel_delay = float(os.getenv('EMPTY_CHANNEL_LEAVE_DELAY', '10.0'))
        self.empty_channel_detected_time = None
        
        # Tasks
        self.audio_player = bot.loop.create_task(self.audio_player_task())
        self.cleanup_task = bot.loop.create_task(self.efficient_cleanup_monitor())
        
        logger.info(f"Voice State initialized for guild: {ctx.guild.name}")
    
    async def audio_player_task(self) -> None:
        """Main task that handles playing songs from the queue."""
        try:
            while True:
                self.next.clear()
                
                # If queue is empty, clean up and wait efficiently
                if self.queue_manager.is_queue_empty(self.ctx.guild.id):
                    await self._handle_empty_queue()
                    # Wait for new items to be added to queue instead of polling
                    try:
                        await self.queue_ready.wait()
                        self.queue_ready.clear()
                    except asyncio.CancelledError:
                        logger.info(f"Audio player task cancelled for guild: {self.ctx.guild.name}")
                        break
                    continue
                
                # Get next song from queue
                try:
                    next_song = self.queue_manager.get_next_song(self.ctx.guild.id)
                    if not next_song:
                        continue  # Queue became empty while waiting
                    
                    source, title, requester = next_song
                    
                    # Clean up any previous song
                    await self._cleanup_current_song()
                    
                    # Set current song info
                    self.current = source
                    self.current_title = title
                    self.current_requester = requester
                    
                    # Send now playing message
                    await self._send_now_playing_message()
                    
                    # Update activity - no need to manage cleanup tasks manually
                    self._update_activity_timestamp()
                    
                except Exception as e:
                    # Handle errors getting songs from queue
                    await self.error_handler.handle_error(
                        error=e,
                        error_type="system",
                        context_info=f"getting next song from queue in guild: {self.ctx.guild.name}"
                    )
                    
                    # Clean up failed song and continue
                    await self._cleanup_current_song()
                    continue
                
                # Play the song with proper error handling
                try:
                    if self.voice and self.current:
                        self.voice.play(self.current, after=self.play_next)
                        logger.info(f"Started playing song in {self.ctx.guild.name}")
                    else:
                        logger.warning(f"No voice client or audio source in {self.ctx.guild.name}")
                        continue
                        
                    # Wait for song to finish or be skipped
                    await self.next.wait()
                    
                except Exception as e:
                    # Handle playback errors
                    await self._handle_playback_error(e)
                    
                    # Clean up the current song
                    await self._cleanup_current_song()
                    
                    # Save queue state after song completion
                    if hasattr(self.bot.get_cog('MusicCog'), 'ENABLE_QUEUE_PERSISTENCE'):
                        cog = self.bot.get_cog('MusicCog')
                        if cog and hasattr(cog, 'queue_manager'):
                            asyncio.create_task(cog.queue_manager.save_queue_state())
                    
        except asyncio.CancelledError:
            logger.info(f"Audio player task cancelled for guild: {self.ctx.guild.name}")
        except Exception as e:
            await self.error_handler.handle_error(
                error=e,
                error_type="system", 
                context_info=f"audio player task in guild: {self.ctx.guild.name}"
            )
        finally:
            # Ensure cleanup happens even if task is cancelled or crashes
            logger.info(f"Audio player task ending for guild: {self.ctx.guild.name}")
            await self._cleanup_current_song()
            await self._cancel_cleanup_task()
            # Use smart garbage collection for efficient resource cleanup
            await smart_garbage_collect()
    
    async def _handle_empty_queue(self) -> None:
        """Handle when the queue is empty."""
        await self._delete_current_message()
        
        # Update activity timestamp - efficient cleanup monitor will handle the rest
        self._update_activity_timestamp()
    
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
                f"🎵 Now playing: **{self.current_title}** (requested by {self.current_requester.name})"
            )
        except discord.HTTPException as e:
            logger.error(f"Failed to send now playing message: {e}")
    
    async def _cleanup_current_song(self) -> None:
        """Clean up resources for the current song."""
        try:
            if self.current:
                self.current.cleanup()
                self.current = None
            
            self.current_title = None
            self.current_requester = None
            
            # Use smart garbage collection instead of forcing GC every song
            await smart_garbage_collect()
            
        except Exception as e:
            logger.error(f"Error during song cleanup: {e}", exc_info=True)
    
    def play_next(self, error=None) -> None:
        """Callback called after the current song finishes playing."""
        if error:
            logger.error(f"Audio player error: {error}")
            # Schedule error handling
            asyncio.create_task(self._handle_playback_error(error))
        
        # Update activity timestamp when song finishes
        self._update_activity_timestamp()
        
        # Signal that the song is done and we can play the next one
        self.next.set()
    
    async def efficient_cleanup_monitor(self) -> None:
        """Efficient cleanup monitor that reduces task creation overhead."""
        try:
            while True:
                # CONFIG: Cleanup check interval - Check every 30 seconds for better responsiveness
                # This replaces the old system of creating/cancelling tasks frequently
                await asyncio.sleep(self.cleanup_check_interval)
                
                # Check if we should cleanup based on activity timestamp
                if self._should_cleanup():
                    if self.queue_manager.is_queue_empty(self.ctx.guild.id) and not self.current:
                        # Check if there are any users in the voice channel
                        # CONFIG: Minimum users in voice channel - Bot leaves if only 1 member (itself) remains
                        # This prevents the bot from playing music to empty channels
                        if self.voice and len(self.voice.channel.members) <= 1:  # Only bot is in the channel
                            await self.stop()
                            await self.ctx.channel.send("👋 Leaving voice channel due to inactivity.")
                            break  # Exit the monitor loop after cleanup
        except asyncio.CancelledError:
            logger.debug(f"Efficient cleanup monitor cancelled for guild: {self.ctx.guild.name}")
        except Exception as e:
            # Use standardized error logging for cleanup failures
            logger.error(f"Error in efficient cleanup monitor for {self.ctx.guild.name}: {type(e).__name__}: {str(e)}", exc_info=True)
    
    def _update_activity_timestamp(self) -> None:
        """Update the last activity timestamp efficiently."""
        self.last_activity_time = asyncio.get_event_loop().time()
        # Reset empty channel detection when there's activity
        self.empty_channel_detected_time = None
    
    def _is_voice_channel_empty(self) -> bool:
        """Check if the voice channel contains only the bot (no human users)."""
        if not self.voice or not self.voice.channel:
            return True  # No voice connection means effectively "empty"
        
        # Get all members in the voice channel
        channel_members = self.voice.channel.members
        
        # Count non-bot users (exclude the bot itself and other bots)
        human_users = [
            member for member in channel_members 
            if not member.bot and member.id != self.bot.user.id
        ]
        
        is_empty = len(human_users) == 0
        
        if is_empty and len(channel_members) > 1:  # Bot + other bots but no humans
            logger.debug(f"Voice channel in {self.ctx.guild.name} contains only bots, considering empty")
        
        return is_empty
    
    def _should_cleanup(self) -> bool:
        """Check if cleanup should occur based on inactivity or empty channel detection."""
        current_time = asyncio.get_event_loop().time()
        
        # Check for empty voice channel first (immediate cleanup with delay)
        if self.auto_leave_empty and self._is_voice_channel_empty():
            if self.empty_channel_detected_time is None:
                # First time detecting empty channel - start timer
                self.empty_channel_detected_time = current_time
                logger.info(f"Empty voice channel detected in {self.ctx.guild.name}, will leave in {self.empty_channel_delay}s")
                return False
            elif current_time - self.empty_channel_detected_time >= self.empty_channel_delay:
                # Empty channel delay has passed - cleanup immediately
                logger.info(f"Leaving empty voice channel in {self.ctx.guild.name}")
                return True
        else:
            # Reset empty channel timer if users are present
            self.empty_channel_detected_time = None
        
        # CONFIG: Auto-disconnect timeout - Bot leaves voice channel after 3 minutes (180s) of inactivity
        # This prevents the bot from staying in inactive voice channels indefinitely
        time_since_activity = current_time - self.last_activity_time
        return time_since_activity >= self.activity_timeout
    
    async def _cancel_cleanup_task(self) -> None:
        """Cancel the cleanup monitor task if it exists."""
        if self.cleanup_task and not self.cleanup_task.cancelled():
            try:
                self.cleanup_task.cancel()
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Error cancelling cleanup monitor: {e}")
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
                "⚠️ **Playback Error**",
                f"❌ Error occurred while playing: {song_title}",
                f"Attempting to continue with next song. Technical details: `{error_str}`"
            )
        except Exception as e:
            # Use standardized error handling for notification failures
            await self.error_handler.handle_error(
                error=e,
                error_type="system",
                context_info=f"playback error notification in guild: {self.ctx.guild.name}"
            )
    
    async def stop(self) -> None:
        """Stop playing music and disconnect from voice channel."""
        logger.info(f"Stopping voice state for guild: {self.ctx.guild.name}")
        
        try:
            # Cancel the audio player task first to prevent new songs from starting
            if self.audio_player and not self.audio_player.cancelled():
                self.audio_player.cancel()
                try:
                    await self.audio_player
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"Error stopping audio player: {e}")
                finally:
                    self.audio_player = None
            
            # Cancel cleanup task
            await self._cancel_cleanup_task()
            
            # Clean up current song
            await self._cleanup_current_song()
            
            # Disconnect voice client if connected
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
            
            # Use smart garbage collection for efficient resource cleanup
            await smart_garbage_collect(force=True)  # Force GC on full cleanup
            
        except Exception as e:
            logger.error(f"Error during voice state cleanup: {e}", exc_info=True)
        finally:
            logger.info(f"Voice state cleanup completed for guild: {self.ctx.guild.name}")
    
    def skip(self) -> bool:
        """Skip the current song if one is playing."""
        if self.voice and self.voice.is_playing():
            self.voice.stop()
            return True
        return False
    
    def is_playing(self) -> bool:
        """Check if audio is currently playing."""
        return self.voice and self.voice.is_playing()


class AudioPlayerManager:
    """Manages VoiceState instances for all guilds."""
    
    def __init__(self, bot, queue_manager, error_handler):
        """Initialize the AudioPlayerManager."""
        self.bot = bot
        self.queue_manager = queue_manager
        self.error_handler = error_handler
        self.voice_states: Dict[int, VoiceState] = {}
        
        logger.info("AudioPlayerManager initialized")
    
    def get_voice_state(self, ctx) -> VoiceState:
        """Get or create a voice state for the guild."""
        guild_id = ctx.guild.id
        state = self.voice_states.get(guild_id)
        
        if not state or not ctx.voice_client:
            state = VoiceState(self.bot, ctx, self.queue_manager, self.error_handler)
            self.voice_states[guild_id] = state
        
        return state
    
    def remove_voice_state(self, guild_id: int) -> None:
        """Remove a voice state for a guild."""
        if guild_id in self.voice_states:
            del self.voice_states[guild_id]
            logger.debug(f"Removed voice state for guild {guild_id}")
    
    async def stop_all_voice_states(self) -> None:
        """Stop all voice states (used during bot shutdown)."""
        logger.info("Stopping all voice states...")
        
        # Create a list of tasks to stop all voice states concurrently
        stop_tasks = []
        for voice_state in self.voice_states.values():
            stop_tasks.append(voice_state.stop())
        
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)
        
        self.voice_states.clear()
        logger.info("All voice states stopped")
    
    def get_all_voice_states(self) -> Dict[int, VoiceState]:
        """Get all current voice states."""
        return self.voice_states.copy()
    
    async def signal_queue_ready(self, guild_id: int) -> None:
        """Signal that a queue has new items ready to play."""
        if guild_id in self.voice_states:
            self.voice_states[guild_id].queue_ready.set()
