"""
Queue Management Module

This module handles all queue-related functionality including:
- Queue operations (add, remove, clear)
- Queue persistence and restoration
- Queue size and user limits enforcement
- Queue validation and sanitization
"""

import asyncio
import json
import logging
import os
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Deque

import aiofiles
import discord

# Import resource optimizations
from .resource_optimizer import MemoryOptimizedQueue, write_queue_data_efficiently, smart_garbage_collect

logger = logging.getLogger('QueueManager')


class QueueManager:
    """Manages music queue operations, persistence, and validation."""
    
    def __init__(self, client: discord.Client):
        self.client = client
        # Use memory-optimized queues instead of regular deques
        self.guild_queues: Dict[int, MemoryOptimizedQueue] = {}
        
        # Configuration from environment variables
        self.max_queue_size = int(os.getenv('MAX_QUEUE_SIZE', '100'))
        self.user_queue_limit = int(os.getenv('USER_QUEUE_LIMIT', '20'))
        
        # Queue persistence settings
        self.enable_persistence = os.getenv('ENABLE_QUEUE_PERSISTENCE', 'true').lower() == 'true'
        self.persistence_file = Path(os.getenv('QUEUE_PERSISTENCE_FILE', 'data/queue_state.json'))
        self.max_age_hours = int(os.getenv('QUEUE_PERSISTENCE_MAX_AGE_HOURS', '24'))
        self.save_interval_minutes = int(os.getenv('QUEUE_SAVE_INTERVAL_MINUTES', '5'))
        
        # Create data directory if it doesn't exist
        self.persistence_file.parent.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"QueueManager initialized - Max size: {self.max_queue_size}, User limit: {self.user_queue_limit}, Persistence: {self.enable_persistence}")
        if self.enable_persistence:
            logger.info(f"Queue persistence enabled - File: {self.persistence_file}")
    
    def get_queue(self, guild_id: int) -> MemoryOptimizedQueue:
        """Get or create a memory-optimized queue for a guild."""
        if guild_id not in self.guild_queues:
            self.guild_queues[guild_id] = MemoryOptimizedQueue(maxlen=self.max_queue_size)
        return self.guild_queues[guild_id]
    
    def add_to_queue(self, guild_id: int, source: Any, title: str, requester: discord.Member) -> Tuple[int, int]:
        """
        Add a song to the queue.
        
        Args:
            guild_id: Discord guild ID
            source: Audio source object
            title: Song title
            requester: User who requested the song
            
        Returns:
            Tuple of (queue_position, total_songs)
            
        Raises:
            ValueError: If queue limits are exceeded
        """
        queue = self.get_queue(guild_id)
        
        # Check total queue size limit
        if len(queue) >= self.max_queue_size:
            raise ValueError(f"Queue has reached maximum size ({self.max_queue_size} songs)")
        
        # Check per-user queue limit
        user_songs = sum(1 for _, _, req in queue if req.id == requester.id)
        if user_songs >= self.user_queue_limit:
            raise ValueError(f"You have reached your personal queue limit ({self.user_queue_limit} songs)")
        
        # Add song to queue
        queue.append((source, title, requester))
        queue_position = len(queue)
        
        logger.info(f"Added to queue: '{title}' by {requester.name} in guild {guild_id} (position {queue_position})")
        
        return queue_position, queue_position
    
    def remove_song(self, guild_id: int, position: int) -> Tuple[Any, str, Any]:
        """
        Remove a song from the queue by position (1-indexed).
        Returns the removed song data.
        Uses memory-optimized removal to avoid copying the entire queue.
        """
        queue = self.get_queue(guild_id)
        
        if position < 1 or position > len(queue):
            raise ValueError(f"Invalid position {position}. Queue has {len(queue)} songs.")
        
        # Use memory-optimized removal (no list conversion needed)
        removed_song = queue.remove_by_index(position - 1)
        
        if self.enable_persistence:
            asyncio.create_task(self._save_queue_state())
        
        logger.debug(f"Removed song at position {position} from guild {guild_id} queue")
        return removed_song
    
    def clear_queue(self, guild_id: int) -> int:
        """
        Clear all songs from the queue.
        
        Args:
            guild_id: Discord guild ID
            
        Returns:
            Number of songs that were cleared
        """
        queue = self.get_queue(guild_id)
        queue_size = len(queue)
        queue.clear()
        
        logger.info(f"Cleared queue with {queue_size} songs in guild {guild_id}")
        
        return queue_size
    
    def get_next_song(self, guild_id: int) -> Optional[Tuple[Any, str, discord.Member]]:
        """
        Get and remove the next song from the queue.
        
        Args:
            guild_id: Discord guild ID
            
        Returns:
            Tuple of (source, title, requester) or None if queue is empty
        """
        queue = self.get_queue(guild_id)
        
        if not queue:
            return None
        
        next_song = queue.popleft()
        logger.debug(f"Retrieved next song: '{next_song[1]}' from guild {guild_id}")
        
        return next_song
    
    def peek_queue(self, guild_id: int) -> Optional[Tuple[Any, str, discord.Member]]:
        """
        Peek at the next song without removing it from the queue.
        
        Args:
            guild_id: Discord guild ID
            
        Returns:
            Tuple of (source, title, requester) or None if queue is empty
        """
        queue = self.get_queue(guild_id)
        
        if not queue:
            return None
        
        return queue[0]
    
    def get_queue_info(self, guild_id: int) -> Dict[str, Any]:
        """
        Get detailed information about the queue.
        
        Args:
            guild_id: Discord guild ID
            
        Returns:
            Dict with queue information
        """
        queue = self.get_queue(guild_id)
        
        # Count songs per user
        user_counts = {}
        for _, title, requester in queue:
            user_counts[requester.id] = user_counts.get(requester.id, 0) + 1
        
        return {
            'total_songs': len(queue),
            'user_counts': user_counts,
            'max_queue_size': self.max_queue_size,
            'user_queue_limit': self.user_queue_limit,
            'songs': [(title, requester.name) for _, title, requester in queue]
        }
    
    def is_queue_empty(self, guild_id: int) -> bool:
        """Check if a guild's queue is empty."""
        return len(self.get_queue(guild_id)) == 0
    
    def get_queue_length(self, guild_id: int) -> int:
        """Get the length of a guild's queue."""
        return len(self.get_queue(guild_id))
    
    # ==================================================================================
    # QUEUE PERSISTENCE METHODS
    # ==================================================================================
    
    async def _save_queue_state(self) -> None:
        """
        Save current queue state to disk using memory-efficient streaming approach.
        This avoids building large data structures in memory.
        """
        if not self.enable_persistence:
            return
        
        try:
            # Import here to avoid circular imports
            from .audio_player import AudioPlayerManager
            audio_manager = getattr(self.client.get_cog('MusicCog'), 'audio_manager', None)
            voice_states = audio_manager.voice_states if audio_manager else {}
            
            # Use streaming JSON writer to avoid memory spikes
            await write_queue_data_efficiently(
                self.persistence_file, 
                self.guild_queues, 
                voice_states
            )
            
            logger.debug(f"Queue state saved efficiently to {self.persistence_file}")
            
            # Trigger smart garbage collection after large persistence operation
            await smart_garbage_collect()
            
        except Exception as e:
            logger.error(f"Failed to save queue state: {e}", exc_info=True)
    
    async def restore_queues_on_startup(self) -> None:
        """Restore saved queue states on bot startup."""
        if not self.enable_persistence:
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
    
    async def start_periodic_save(self, voice_states: Dict[int, Any]) -> None:
        """Start the periodic queue save task."""
        if not self.ENABLE_QUEUE_PERSISTENCE:
            return
            
        save_interval = int(os.getenv('QUEUE_SAVE_INTERVAL_MINUTES', '5'))  # Default: every 5 minutes
        
        while True:
            try:
                await asyncio.sleep(save_interval * 60)  # Convert minutes to seconds
                
                # Only save if there are active queues
                active_queues = sum(1 for queue in self.guild_queues.values() if queue)
                if voice_states:
                    active_queues += sum(1 for vs in voice_states.values() if vs.current)
                
                if active_queues > 0:
                    await self.save_queue_state(voice_states)
                    logger.debug(f"Periodic queue save completed ({active_queues} active queues)")
                    
            except asyncio.CancelledError:
                logger.info("Periodic queue save task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in periodic queue save: {str(e)}", exc_info=True)
                # Continue the loop even if one save fails
                continue
    
    async def cleanup_on_shutdown(self, voice_states: Optional[Dict[int, Any]] = None) -> None:
        """Save queue state before bot shutdown."""
        if self.ENABLE_QUEUE_PERSISTENCE:
            logger.info("Saving queue state before shutdown...")
            await self.save_queue_state(voice_states)
            logger.info("Queue state saved successfully")
