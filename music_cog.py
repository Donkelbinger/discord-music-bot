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

load_dotenv()
logger = logging.getLogger('MusicCog')

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
        logger.info("Music Cog initialized")

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
            self.audio_player = bot.loop.create_task(self.audio_player_task())
            self.cleanup_task = None
            self.last_activity = asyncio.get_event_loop().time()
            logger.info(f"Voice State initialized for guild: {ctx.guild.name}")

        async def audio_player_task(self) -> None:
            """Main task that handles playing songs from the queue."""
            while True:
                self.next.clear()
                
                # If queue is empty, clean up and wait
                if not self.queue:
                    await self._handle_empty_queue()
                    await asyncio.sleep(1)
                    continue

                # Cancel cleanup if it was scheduled
                if self.cleanup_task:
                    self.cleanup_task.cancel()
                    self.cleanup_task = None

                try:
                    async with timeout(3600):  # Increase timeout to 1 hour
                        self.current, self.current_title, self.current_requester = self.queue.popleft()
                        logger.info(f"Playing next song in {self.ctx.guild.name}: {self.current_title}")
                        
                        # Delete previous now-playing message if it exists
                        await self._delete_current_message()
                        
                        # Send new now-playing message
                        await self._send_now_playing_message()

                except asyncio.TimeoutError:
                    logger.warning(f"Player timed out in {self.ctx.guild.name}")
                    self.bot.loop.create_task(self.stop())
                    return
                except Exception as e:
                    logger.error(f"Error in audio player task: {str(e)}", exc_info=True)
                    continue

                # Play the song
                try:
                    self.voice.play(self.current, after=self.play_next)
                    logger.info(f"Started playing song in {self.ctx.guild.name}")
                except Exception as e:
                    logger.error(f"Error playing song: {str(e)}", exc_info=True)
                    continue

                # Wait for song to finish
                try:
                    await self.next.wait()
                except Exception as e:
                    logger.error(f"Error waiting for next song: {str(e)}", exc_info=True)
                    continue
                
                # Clean up the current song
                await self._cleanup_current_song()

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
                    f"🎵 Now playing: **{self.current_title}** (requested by {self.current_requester.name})"
                )
            except discord.HTTPException as e:
                logger.error(f"Failed to send now playing message: {e}")

        async def _cleanup_current_song(self) -> None:
            """Clean up the current song resources."""
            if self.current:
                try:
                    self.current.cleanup()
                except Exception as e:
                    logger.error(f"Error cleaning up song: {e}")
                self.current = None
                gc.collect()

        def play_next(self, error=None) -> None:
            """Callback called after the current song finishes playing."""
            if error:
                logger.error(f'Player error: {error}')
            self.next.set()

        async def cleanup_check(self) -> None:
            """Check if bot should leave voice channel due to inactivity."""
            try:
                await asyncio.sleep(180)  # Wait 3 minutes
                if not self.queue and not self.current:
                    # Check if there are any users in the voice channel
                    if len(self.voice.channel.members) <= 1:  # Only bot is in the channel
                        await self.stop()
                        await self.ctx.channel.send("👋 Leaving voice channel due to inactivity.")
            except asyncio.CancelledError:
                pass

        async def stop(self) -> None:
            """Stop playing music and disconnect from voice channel."""
            self.queue.clear()
            if self.voice:
                if self.voice.is_playing():
                    self.voice.stop()
                await self.voice.disconnect()
            await self._delete_current_message()
            logger.info(f"Stopped playing in {self.ctx.guild.name}")

    def get_voice_state(self, ctx) -> VoiceState:
        """Get or create a voice state for the guild."""
        state = self.voice_states.get(ctx.guild.id)
        if not state or not ctx.voice_client:
            state = self.VoiceState(self.bot, ctx)
            self.voice_states[ctx.guild.id] = state
        return state

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
            
            platform = 'SoundCloud' if info.get('extractor', '').lower() == 'soundcloud' else 'YouTube'
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

    @app_commands.command(name='play', description='Play a song by URL or search query')
    async def play(self, interaction: discord.Interaction, query: str):
        """Plays a song from YouTube or SoundCloud, or searches for a song"""
        await interaction.response.defer()

        # Get or join voice channel
        voice_client = await self._ensure_voice_client(interaction)
        if not voice_client:
            return

        ctx = await self.bot.get_context(interaction)
        try:
            logger.info(f"Attempting to play: {query} in {interaction.guild.name}")
            
            # Process the URL/query
            try:
                audio_url, title, platform = await self.process_url(query)
            except ValueError as e:
                await interaction.followup.send(str(e))
                return
            except Exception as e:
                logger.error(f"Unexpected error processing query: {str(e)}", exc_info=True)
                await interaction.followup.send("An unexpected error occurred. Please try again later.")
                return

            # Create the audio source
            try:
                source = await self._create_audio_source(audio_url)
            except Exception as e:
                logger.error(f"Error creating audio source: {str(e)}", exc_info=True)
                await interaction.followup.send("Failed to create audio source. Please try again.")
                return

            # Add to queue
            state = self.get_voice_state(ctx)
            state.voice = voice_client
            state.queue.append((source, f"{title} ({platform})", interaction.user))
            logger.info(f"Added to queue: {title} from {platform} in {interaction.guild.name}")
            await interaction.followup.send(f'Added to queue: {title} ({platform})')

        except Exception as e:
            logger.error(f"Error playing {query}: {str(e)}", exc_info=True)
            await interaction.followup.send(f'An error occurred: {str(e)}')
            
    async def _ensure_voice_client(self, interaction: discord.Interaction) -> Optional[discord.VoiceClient]:
        """Ensure we have a valid voice client, joining if necessary."""
        if not interaction.guild.voice_client:
            return await self.auto_join(interaction)
        return interaction.guild.voice_client
            
    async def _create_audio_source(self, audio_url: str) -> discord.FFmpegOpusAudio:
        """Create an audio source from the given URL."""
        ffmpeg_options = {
            'options': '-vn -b:a 128k -bufsize 256k -ar 48000 -af loudnorm=I=-16:TP=-1.5:LRA=11',
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -timeout 60000000'
        }
        
        return await discord.FFmpegOpusAudio.from_probe(
            audio_url, 
            **ffmpeg_options,
            method='fallback'
        )

    @app_commands.command(name='skip', description='Skip the current song')
    async def skip(self, interaction: discord.Interaction):
        """Skips the current song"""
        if interaction.guild.voice_client is None:
            return await interaction.response.send_message('Not connected to any voice channel.')

        ctx = await self.bot.get_context(interaction)
        state = self.get_voice_state(ctx)
        
        if state.voice.is_playing():
            state.voice.stop()
            logger.info(f"Skipped song in {interaction.guild.name}")
            await interaction.response.send_message('Skipped the current song.')
        else:
            await interaction.response.send_message('Nothing is playing right now.')

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
    async def clear(self, interaction: discord.Interaction):
        """Clears the queue"""
        ctx = await self.bot.get_context(interaction)
        state = self.get_voice_state(ctx)
        state.queue.clear()
        logger.info(f"Queue cleared in {interaction.guild.name}")
        await interaction.response.send_message('Queue cleared.')

    @app_commands.command(name='leave', description='Leave the voice channel')
    async def leave(self, interaction: discord.Interaction):
        """Leaves the voice channel"""
        if not interaction.guild.voice_client:
            await interaction.response.send_message('Not connected to any voice channel.')
            return
            
        ctx = await self.bot.get_context(interaction)
        state = self.get_voice_state(ctx)
        await state.stop()
        del self.voice_states[interaction.guild.id]
        await interaction.response.send_message('Disconnected from voice channel.')
        logger.info(f"Left voice channel in {interaction.guild.name}")

    @app_commands.command(name='remove', description='Remove a specific song from the queue by its position number')
    async def remove(self, interaction: discord.Interaction, position: int):
        """Removes a specific song from the queue"""
        ctx = await self.bot.get_context(interaction)
        state = self.get_voice_state(ctx)
        
        if len(state.queue) == 0:
            await interaction.response.send_message('Queue is empty.')
            return
            
        if position < 1 or position > len(state.queue):
            await interaction.response.send_message(f'Invalid position. Please enter a number between 1 and {len(state.queue)}.')
            return
            
        try:
            # Remove the song at the specified position
            removed_song = self._remove_song_from_queue(state, position)
            _, title, requester = removed_song
            
            logger.info(f"Removed song at position {position} from queue in {interaction.guild.name}")
            await interaction.response.send_message(f'Removed from queue: {title} (requested by {requester.name})')
            
        except Exception as e:
            logger.error(f"Error removing song from queue: {str(e)}", exc_info=True)
            await interaction.response.send_message('An error occurred while trying to remove the song.')
            
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
        
        return result 