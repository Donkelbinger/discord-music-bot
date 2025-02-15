import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
import asyncio
from async_timeout import timeout
from collections import deque
import logging
import gc

logger = logging.getLogger('MusicCog')

class MusicCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.voice_states = {}
        self.cleanup_tasks = {}
        logger.info("Music Cog initialized")

    class VoiceState:
        def __init__(self, bot, ctx):
            self.bot = bot
            self.ctx = ctx
            self.current = None
            self.current_title = None
            self.current_message = None  # Store the current playing message
            self.voice = ctx.voice_client
            self.queue = deque()
            self.next = asyncio.Event()
            self.audio_player = bot.loop.create_task(self.audio_player_task())
            self.cleanup_task = None
            self.last_activity = asyncio.get_event_loop().time()
            logger.info(f"Voice State initialized for guild: {ctx.guild.name}")

        async def audio_player_task(self):
            while True:
                self.next.clear()
                
                if not self.queue:
                    self.current = None
                    self.current_title = None
                    if self.current_message:
                        try:
                            await self.current_message.delete()
                        except:
                            pass
                        self.current_message = None
                    
                    if not self.cleanup_task:
                        self.cleanup_task = self.bot.loop.create_task(self.cleanup_check())
                    
                    await asyncio.sleep(1)
                    continue

                if self.cleanup_task:
                    self.cleanup_task.cancel()
                    self.cleanup_task = None

                try:
                    async with timeout(180):  # 3 minute timeout
                        self.current, self.current_title = self.queue.popleft()
                        logger.info(f"Playing next song in {self.ctx.guild.name}: {self.current_title}")
                        
                        if self.current_message:
                            try:
                                await self.current_message.delete()
                            except:
                                pass
                        
                        try:
                            self.current_message = await self.ctx.channel.send(f"🎵 Now playing: **{self.current_title}**")
                        except:
                            pass

                except asyncio.TimeoutError:
                    logger.warning(f"Player timed out in {self.ctx.guild.name}")
                    self.bot.loop.create_task(self.stop())
                    return
                except Exception as e:
                    logger.error(f"Error in audio player task: {str(e)}", exc_info=True)
                    continue

                try:
                    self.voice.play(self.current, after=self.play_next)
                    logger.info(f"Started playing song in {self.ctx.guild.name}")
                except Exception as e:
                    logger.error(f"Error playing song: {str(e)}", exc_info=True)
                    continue

                try:
                    await self.next.wait()
                except Exception as e:
                    logger.error(f"Error waiting for next song: {str(e)}", exc_info=True)
                    continue
                
                if self.current:
                    try:
                        self.current.cleanup()
                    except:
                        pass
                    self.current = None
                    gc.collect()

        def play_next(self, error=None):
            if error:
                logger.error(f'Player error: {error}')
            self.next.set()

        async def cleanup_check(self):
            """Check if bot should leave voice channel due to inactivity"""
            try:
                await asyncio.sleep(180)  # Wait 3 minutes
                if not self.queue and not self.current:
                    await self.stop()
                    await self.ctx.channel.send("👋 Leaving voice channel due to inactivity.")
            except asyncio.CancelledError:
                pass

        async def stop(self):
            self.queue.clear()
            if self.voice:
                if self.voice.is_playing():
                    self.voice.stop()
                await self.voice.disconnect()
            if self.current_message:
                await self.current_message.delete()
                self.current_message = None
            logger.info(f"Stopped playing in {self.ctx.guild.name}")

    def get_voice_state(self, ctx):
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

    @app_commands.command(name='play', description='Play a song from YouTube')
    async def play(self, interaction: discord.Interaction, url: str):
        """Plays a song from YouTube"""
        await interaction.response.defer()

        # Auto-join voice channel and get voice client
        voice_client = None
        if not interaction.guild.voice_client:
            voice_client = await self.auto_join(interaction)
            if not voice_client:
                return
        else:
            voice_client = interaction.guild.voice_client

        ctx = await self.bot.get_context(interaction)
        try:
            logger.info(f"Attempting to play URL: {url} in {interaction.guild.name}")
            ydl_opts = {
                'format': 'bestaudio/best',
                'noplaylist': True,
                'nocheckcertificate': True,
                'ignoreerrors': False,
                'quiet': True,
                'no_warnings': True,
                'default_search': 'auto',
                'source_address': '0.0.0.0',
                'extract_flat': True,
                'socket_timeout': 10,
                'retries': 3,
                'extractor_retries': 3,
                'skip_download': True,
                'max_downloads': 1,
                'youtube_include_dash_manifest': False,
                'cachedir': False,
                'postprocessors': [{  # Add postprocessors for better audio
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'opus',
                    'preferredquality': '128',
                }]
            }

            # Create a ThreadPoolExecutor for CPU-intensive tasks
            loop = asyncio.get_event_loop()
            async def get_info():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    try:
                        return await loop.run_in_executor(
                            None, 
                            lambda: ydl.extract_info(url, download=False)
                        )
                    except Exception as e:
                        logger.error(f"Error extracting info: {str(e)}")
                        raise

            try:
                async with timeout(30):
                    info = await get_info()
            except asyncio.TimeoutError:
                await interaction.followup.send("The request timed out. Please try again.")
                return
            except Exception as e:
                await interaction.followup.send(f"An error occurred while processing the video: {str(e)}")
                return

            if not info:
                await interaction.followup.send("Could not get video information.")
                return

            # Get the best audio format
            formats = info.get('formats', [])
            url2 = None
            for f in formats:
                if f.get('acodec') == 'opus' and f.get('vcodec') == 'none':
                    url2 = f.get('url')
                    break
            
            if not url2:
                url2 = info.get('url')
                if not url2 and formats:
                    url2 = formats[0].get('url')
                if not url2:
                    await interaction.followup.send("Could not get video URL.")
                    return

            title = info.get('title', 'Unknown title')

            # Optimize FFmpeg options for lower CPU usage
            ffmpeg_options = {
                'options': '-vn -b:a 128k -bufsize 128k -cpu-used 4 -threads 2',
                'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
            }

            try:
                source = await discord.FFmpegOpusAudio.from_probe(
                    url2, 
                    **ffmpeg_options,
                    method='fallback'
                )
            except Exception as e:
                logger.error(f"Error creating audio source: {str(e)}")
                await interaction.followup.send("Failed to create audio source. Please try again.")
                return

            state = self.get_voice_state(ctx)
            state.voice = voice_client  # Set the voice client explicitly
            state.queue.append((source, title))
            logger.info(f"Added to queue: {title} in {interaction.guild.name}")
            await interaction.followup.send(f'Added to queue: {title}')

        except Exception as e:
            logger.error(f"Error playing URL {url}: {str(e)}", exc_info=True)
            await interaction.followup.send(f'An error occurred: {str(e)}')

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

        queue_list = []
        if state.current:
            queue_list.append(f"**Currently Playing:** {state.current_title}")
        
        if state.queue:
            queue_list.append("\n**Queue:**")
            for i, (_, title) in enumerate(state.queue, 1):
                queue_list.append(f"{i}. {title}")

        queue_message = '\n'.join(queue_list)
        await interaction.response.send_message(queue_message)

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
        if interaction.guild.voice_client:
            ctx = await self.bot.get_context(interaction)
            state = self.get_voice_state(ctx)
            await state.stop()
            del self.voice_states[interaction.guild.id]
            await interaction.response.send_message('Disconnected from voice channel.')
            logger.info(f"Left voice channel in {interaction.guild.name}")
        else:
            await interaction.response.send_message('Not connected to any voice channel.') 