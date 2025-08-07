"""
Media Extraction Module

This module handles all media extraction functionality including:
- YouTube and SoundCloud URL processing
- Search query handling
- Audio source extraction with yt-dlp
- Fallback mechanisms for age-restricted content
- Intelligent performance caching
- Parallel processing for playlists
- HTTP connection pooling
"""

import yt_dlp
import asyncio
import logging
import aiohttp
import re
import random
from async_timeout import timeout
from typing import Tuple, Optional, Dict, Any, List, Union
import os
import concurrent.futures
from .performance_cache import performance_cache

logger = logging.getLogger('MediaExtractor')


class MediaExtractor:
    """Handles media extraction from YouTube and SoundCloud."""
    
    def __init__(self) -> None:
        """Initialize the MediaExtractor with configuration."""
        self.youtube_cookie_file = os.getenv('YOUTUBE_COOKIE_FILE', '')
        
        # Configurable audio quality settings
        self._load_audio_quality_config()
        
        # Performance optimization settings
        self.PARALLEL_EXTRACTION_ENABLED = os.getenv('PARALLEL_EXTRACTION_ENABLED', 'true').lower() == 'true'
        self.MAX_CONCURRENT_EXTRACTIONS = int(os.getenv('MAX_CONCURRENT_EXTRACTIONS', '5'))
        self.CONNECTION_POOL_SIZE = int(os.getenv('CONNECTION_POOL_SIZE', '10'))
        
        # Initialize HTTP session with connection pooling
        self.http_session = None
        asyncio.create_task(self._init_http_session())
        
        # Thread pool for CPU-intensive operations
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.MAX_CONCURRENT_EXTRACTIONS,
            thread_name_prefix="MediaExtractor"
        )
        
        logger.info(f"MediaExtractor initialized with audio quality: {self.audio_codec} @ {self.audio_quality}kbps")
        logger.info(f"Performance optimizations: Parallel={self.PARALLEL_EXTRACTION_ENABLED}, Max concurrent={self.MAX_CONCURRENT_EXTRACTIONS}")
    
    def _load_audio_quality_config(self) -> None:
        """Load audio quality configuration from environment variables."""
        # Audio codec selection
        codec_env = os.getenv('AUDIO_CODEC', 'opus').lower()
        valid_codecs = ['opus', 'mp3', 'm4a', 'aac']
        
        if codec_env in valid_codecs:
            self.audio_codec = codec_env
        else:
            logger.warning(f"Invalid AUDIO_CODEC '{codec_env}'. Using default: opus")
            self.audio_codec = 'opus'
        
        # Audio quality/bitrate selection
        try:
            quality_env = int(os.getenv('AUDIO_QUALITY', '128'))
            # Validate quality range (Discord voice channels optimized range)
            if 64 <= quality_env <= 320:
                self.audio_quality = str(quality_env)
            else:
                logger.warning(f"AUDIO_QUALITY {quality_env} outside recommended range (64-320kbps). Using default: 128")
                self.audio_quality = '128'
        except (ValueError, TypeError):
            logger.warning(f"Invalid AUDIO_QUALITY '{os.getenv('AUDIO_QUALITY')}'. Using default: 128")
            self.audio_quality = '128'
        
        # Audio format preference for source selection
        format_env = os.getenv('AUDIO_FORMAT', 'bestaudio').lower()
        valid_formats = ['bestaudio', 'bestaudio[ext=m4a]', 'bestaudio[ext=webm]', 'best[height<=720]']
        
        if format_env in valid_formats:
            self.audio_format = format_env
        else:
            logger.warning(f"Invalid AUDIO_FORMAT '{format_env}'. Using default: bestaudio")
            self.audio_format = 'bestaudio'
    
    def _get_ydl_options(self) -> Dict[str, Any]:
        """Get yt-dlp options with configured audio quality settings."""
        ydl_opts = {
            'format': self.audio_format,
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'extract_flat': False,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': self.audio_codec,
                'preferredquality': self.audio_quality
            }]
        }
        
        # Add cookies if available for age-restricted content
        if self.youtube_cookie_file and os.path.exists(self.youtube_cookie_file):
            ydl_opts['cookiefile'] = self.youtube_cookie_file
            logger.debug("Using YouTube cookies for age-restricted content")
        
        return ydl_opts
    
    async def _init_http_session(self) -> None:
        """Initialize HTTP session with connection pooling for optimal performance."""
        connector = aiohttp.TCPConnector(
            limit=self.CONNECTION_POOL_SIZE,
            limit_per_host=5,
            ttl_dns_cache=300,
            use_dns_cache=True,
            keepalive_timeout=60,
            enable_cleanup_closed=True
        )
        
        timeout_config = aiohttp.ClientTimeout(
            total=30,
            connect=10,
            sock_read=10
        )
        
        self.http_session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout_config,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
        )
        
        logger.debug(f"HTTP session initialized with connection pool size: {self.CONNECTION_POOL_SIZE}")
    
    async def process_playlist(self, playlist_url: str, max_songs: int = None) -> List[Tuple[str, str, str]]:
        """
        Process a playlist URL and return list of songs.
        
        Args:
            playlist_url: The playlist URL to process
            max_songs: Maximum number of songs to extract (None for all)
            
        Returns:
            List of tuples containing (audio_url, title, platform) for each song
            
        Raises:
            ValueError: If playlist processing fails or no songs are found
        """
        if not self._is_playlist_url(playlist_url):
            raise ValueError("Provided URL is not a recognized playlist format")
        
        logger.info(f"Processing playlist: {playlist_url[:60]}...")
        
        # Set up yt-dlp options for playlist extraction
        ydl_opts = self._get_ydl_options()
        ydl_opts['extract_flat'] = False  # We need full info for each song
        
        # Configure playlist extraction limits
        if max_songs:
            ydl_opts['playlist_end'] = max_songs
            logger.info(f"Limiting playlist extraction to {max_songs} songs")
        
        try:
            # Extract playlist with timeout
            async with timeout(300):  # 5 minutes for playlist processing
                return await self._extract_playlist_info(playlist_url, ydl_opts)
        except asyncio.TimeoutError:
            logger.error(f"Timeout while processing playlist: {playlist_url}")
            raise ValueError("Playlist processing timed out. Try a smaller playlist or try again later.")
        except Exception as e:
            logger.error(f"Error processing playlist '{playlist_url}': {str(e)}", exc_info=True)
            raise ValueError(f"Error processing playlist: {str(e)}")
    
    async def _extract_playlist_info(self, playlist_url: str, ydl_opts: Dict[str, Any]) -> List[Tuple[str, str, str]]:
        """Extract information for all songs in a playlist."""
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                # Extract playlist information
                playlist_info = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: ydl.extract_info(playlist_url, download=False)
                )
                
                if not playlist_info:
                    raise ValueError("Could not extract playlist information")
                
                # Get playlist entries
                entries = playlist_info.get('entries', [])
                if not entries:
                    raise ValueError("Playlist is empty or could not be accessed")
                
                # Determine platform
                platform = self._detect_playlist_platform(playlist_url)
                
                # Process entries with parallel extraction for optimal performance
                if self.PARALLEL_EXTRACTION_ENABLED and len(entries) > 3:
                    songs, failed_extractions = await self._process_playlist_entries_parallel(
                        entries, platform
                    )
                else:
                    songs, failed_extractions = await self._process_playlist_entries_sequential(
                        entries, platform
                    )
                
                if not songs:
                    raise ValueError("No playable songs found in playlist")
                
                # Log results
                success_count = len(songs)
                total_count = len(entries)
                logger.info(f"Successfully processed {success_count}/{total_count} songs from playlist")
                
                if failed_extractions > 0:
                    logger.warning(f"Failed to process {failed_extractions} songs (may be private, deleted, or age-restricted)")
                
                return songs
                
            except yt_dlp.utils.DownloadError as e:
                error_msg = str(e).lower()
                
                if 'private' in error_msg or 'unavailable' in error_msg:
                    raise ValueError("Playlist is private or unavailable. Please check the URL and permissions.")
                elif 'not found' in error_msg or '404' in error_msg:
                    raise ValueError("Playlist not found. Please check the URL.")
                else:
                    raise ValueError(f"Could not access playlist: {str(e)}")
    
    def _detect_playlist_platform(self, playlist_url: str) -> str:
        """Detect the platform of a playlist URL."""
        if 'youtube.com' in playlist_url.lower() or 'youtu.be' in playlist_url.lower():
            return 'YouTube'
        elif 'soundcloud.com' in playlist_url.lower():
            return 'SoundCloud'
        else:
            return 'Unknown Platform'
    
    async def _process_playlist_entries_parallel(self, entries: List[Dict[str, Any]], platform: str) -> Tuple[List[Tuple[str, str, str]], int]:
        """Process playlist entries in parallel for optimal performance."""
        songs = []
        failed_extractions = 0
        
        logger.info(f"Processing {len(entries)} songs from {platform} playlist using parallel extraction")
        
        # Create semaphore to limit concurrent extractions
        semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_EXTRACTIONS)
        
        async def process_entry_with_semaphore(i: int, entry: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
            async with semaphore:
                try:
                    if not entry:
                        return None
                    return await self._extract_song_from_playlist_entry(entry, platform)
                except Exception as e:
                    logger.warning(f"Failed to extract song {i + 1}: {e}")
                    return None
        
        # Create tasks for all entries
        tasks = [
            process_entry_with_semaphore(i, entry) 
            for i, entry in enumerate(entries)
        ]
        
        # Process with progress reporting
        completed_count = 0
        for coro in asyncio.as_completed(tasks):
            try:
                result = await coro
                completed_count += 1
                
                if result:
                    songs.append(result)
                else:
                    failed_extractions += 1
                
                # Log progress for large playlists
                if completed_count % 10 == 0 or completed_count == len(tasks):
                    logger.info(f"Processed {completed_count}/{len(entries)} songs... ({len(songs)} successful)")
                    
            except Exception as e:
                failed_extractions += 1
                logger.warning(f"Task failed: {e}")
        
        logger.info(f"Parallel processing completed: {len(songs)}/{len(entries)} songs extracted")
        return songs, failed_extractions
    
    async def _process_playlist_entries_sequential(self, entries: List[Dict[str, Any]], platform: str) -> Tuple[List[Tuple[str, str, str]], int]:
        """Process playlist entries sequentially (fallback method)."""
        songs = []
        failed_extractions = 0
        
        logger.info(f"Processing {len(entries)} songs from {platform} playlist sequentially")
        
        for i, entry in enumerate(entries):
            try:
                if not entry:  # Skip None entries
                    failed_extractions += 1
                    continue
                    
                # Extract song information
                song_info = await self._extract_song_from_playlist_entry(entry, platform)
                if song_info:
                    songs.append(song_info)
                    
                    # Log progress for large playlists
                    if (i + 1) % 10 == 0:
                        logger.info(f"Processed {i + 1}/{len(entries)} songs...")
                else:
                    failed_extractions += 1
                    
            except Exception as e:
                logger.warning(f"Failed to extract song {i + 1}: {e}")
                failed_extractions += 1
                continue
        
        return songs, failed_extractions
    
    async def _extract_song_from_playlist_entry(self, entry: Dict[str, Any], platform: str) -> Optional[Tuple[str, str, str]]:
        """Extract song information from a playlist entry."""
        try:
            # Handle different entry formats
            if entry.get('_type') == 'url':
                # Entry is just a URL, need to extract full info
                entry_url = entry.get('url')
                if not entry_url:
                    return None
                    
                # Process the individual URL (disable cache for playlist items to avoid cache pollution)
                return await self.process_url(entry_url, use_cache=False)
            else:
                # Entry has full info already
                return self._extract_url_and_title(entry, platform)
                
        except Exception as e:
            logger.debug(f"Failed to extract song from entry: {e}")
            return None
    
    async def process_url(self, query: str, use_cache: bool = True) -> Tuple[str, str, str]:
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
        youtube_id_match = re.search(r'(?:v=|\\/)([0-9A-Za-z_-]{11}).*', query)
        video_id = None
        
        if youtube_id_match:
            video_id = youtube_id_match.group(1)
            logger.info(f"Detected YouTube video ID: {video_id}")

        # Setup yt-dlp options
        ydl_opts = {
            'format': 'bestaudio',
            'quiet': True,
            'no_warnings': True,
            'default_search': 'ytsearch',
            'skip_download': True,
            'extract_flat': False,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'opus',
                # CONFIG: Audio quality - 128kbps provides good balance of quality and bandwidth
                # Lower values (96, 64) save bandwidth, higher (192, 256) increase quality
                'preferredquality': '128'
            }]
        }

        # Add cookies if available for age-restricted content
        if self.youtube_cookie_file and os.path.exists(self.youtube_cookie_file):
            ydl_opts['cookiefile'] = self.youtube_cookie_file
            logger.debug("Using YouTube cookies for age-restricted content")

        # Check cache first for significant performance improvement
        if use_cache:
            cached_result = await performance_cache.get(query)
            if cached_result:
                logger.info(f"Cache HIT for query: {query[:50]}...")
                return cached_result
        
        # Process with timeout and cache result
        try:
            # CONFIG: Media extraction timeout - 2 minutes (120s) for yt-dlp operations
            # This prevents hanging on slow/problematic media sources
            async with timeout(120):  # Increase extraction timeout to 2 minutes
                result = await self._extract_audio_info(query, ydl_opts)
                
                # Cache successful result for future requests
                if use_cache and result:
                    await performance_cache.set(query, result[0], result[1], result[2])
                    logger.debug(f"Cached result for query: {query[:50]}...")
                
                return result
                
        except asyncio.TimeoutError:
            logger.error(f"Timeout while processing query: {query}")
            raise ValueError("Operation timed out while processing your request. Please try again.")
        except Exception as e:
            logger.error(f"Error processing query '{query}': {str(e)}", exc_info=True)
            raise ValueError(f"Error processing request: {str(e)}")

    async def _extract_audio_info(self, query: str, ydl_opts: Dict[str, Any]) -> Tuple[str, str, str]:
        """Extract audio information from a query using yt-dlp."""
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Determine if it's a direct URL or search query
            is_url = self._is_url(query)
            
            if is_url:
                return await self._handle_direct_url(ydl, query)
            else:
                return await self._handle_search_query(ydl, query)

    def _is_url(self, text: str) -> bool:
        """Check if the text is a valid URL."""
        url_patterns = [
            r'^https?://(www\.)?(youtube\.com|youtu\.be)/',
            r'^https?://(www\.)?soundcloud\.com/',
        ]
        return any(re.match(pattern, text, re.IGNORECASE) for pattern in url_patterns)
    
    def _is_playlist_url(self, text: str) -> bool:
        """Check if the text is a playlist URL."""
        playlist_patterns = [
            # YouTube playlist patterns
            r'^https?://(www\.)?youtube\.com/playlist\?list=',
            r'^https?://(www\.)?youtube\.com/watch\?.*[&?]list=',
            # SoundCloud playlist patterns 
            r'^https?://(www\.)?soundcloud\.com/.+/sets/',
            r'^https?://(www\.)?soundcloud\.com/.+/likes/?$',
        ]
        return any(re.match(pattern, text, re.IGNORECASE) for pattern in playlist_patterns)

    async def _handle_direct_url(self, ydl: yt_dlp.YoutubeDL, url: str) -> Tuple[str, str, str]:
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
            
            # Handle age-restricted content
            if any(keyword in error_msg.lower() for keyword in ['age-restricted', 'sign in', 'private']):
                # Extract video ID for alternative attempts
                video_id_match = re.search(r'(?:v=|/)([0-9A-Za-z_-]{11})', url)
                if video_id_match:
                    video_id = video_id_match.group(1)
                    
                    # Try YouTube Music as fallback
                    try:
                        return await self._try_youtube_music(url, video_id)
                    except Exception:
                        pass
                    
                    # Try SoundCloud fallback using video title
                    title = await self._get_video_title(video_id)
                    if title and title != 'Unknown title':
                        try:
                            return await self._fallback_to_soundcloud(title)
                        except Exception:
                            pass
                    
                    # If SoundCloud fails, suggest YouTube Music as last resort
                    if video_id:
                        ytmusic_url = f"https://music.youtube.com/watch?v={video_id}"
                        raise ValueError(f"This video is age-restricted and couldn't be played on YouTube or SoundCloud. As a last resort, try YouTube Music: {ytmusic_url}")
            
            # If we couldn't extract a title or SoundCloud failed
            raise ValueError("This video is age-restricted and couldn't be played. Try a different version of the song.")
        raise

    async def _handle_search_query(self, ydl: yt_dlp.YoutubeDL, query: str) -> Tuple[str, str, str]:
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
                # If age-restricted, try to get title for SoundCloud fallback
                if any(keyword in error_msg.lower() for keyword in ['age-restricted', 'sign in']):
                    # Extract any video ID that might be in the error or try general search
                    video_id_match = re.search(r'([0-9A-Za-z_-]{11})', error_msg)
                    if video_id_match:
                        video_id = video_id_match.group(1)
                        title = await self._get_video_title(video_id)
                        if title:
                            return await self._fallback_to_soundcloud(title)
                raise e
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

    def _extract_url_and_title(self, info: Dict[str, Any], platform: str) -> Tuple[str, str, str]:
        """Extract URL and title from yt-dlp info dict."""
        # Get the best audio URL
        url = info.get('url')
        if not url:
            # If no direct URL, try to find in formats
            formats = info.get('formats', [])
            for f in formats:
                if f.get('ext') in ['opus', 'm4a', 'mp3'] and f.get('url'):
                    url = f['url']
                    break
            if not url and formats:
                url = formats[0].get('url')
        
        if not url:
            raise ValueError("No playable audio stream found")
        
        title = info.get('title', 'Unknown Title')
        return url, title, platform

    async def _try_youtube_music(self, original_query: str, video_id: str) -> Tuple[str, str, str]:
        """Try to use YouTube Music as a fallback for age-restricted content"""
        # Special handling for known problematic video IDs
        if video_id == "8jZLYF7WNKs":  # The video ID that was causing issues
            logger.info(f"Using special handling for problematic video ID: {video_id}")
            return await self._extract_using_invidious(video_id)
        
        ytmusic_url = f"https://music.youtube.com/watch?v={video_id}"
        logger.info(f"Attempting YouTube Music URL: {ytmusic_url}")
        # Set up yt-dlp options with configurable audio quality
        ydl_opts = self._get_ydl_options()
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.get_event_loop().run_in_executor(
                None, lambda: ydl.extract_info(ytmusic_url, download=False)
            )
            
            if not info:
                raise ValueError("Could not extract YouTube Music info")
            
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
        
        # Try each Invidious instance
        errors = []
        for instance in instances:
            try:
                invidious_url = f"{instance}/watch?v={video_id}"
                logger.info(f"Trying Invidious instance: {instance}")
                
                ydl_opts = {
                    'format': 'bestaudio',
                    'quiet': True,
                    'no_warnings': True,
                    'skip_download': True,
                }
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: ydl.extract_info(invidious_url, download=False)
                    )
                    
                    if info:
                        url = info.get('url')
                        title = info.get('title', title or 'Unknown title')
                        if url:
                            logger.info(f"Successfully extracted from Invidious: {instance}")
                            return url, title, 'YouTube (via Invidious)'
            except Exception as e:
                error_msg = f"{instance}: {str(e)}"
                errors.append(error_msg)
                logger.warning(f"Invidious instance failed: {error_msg}")
                continue
        
        # If all Invidious instances failed, try SoundCloud as last resort
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
        
        # Set up yt-dlp for SoundCloud search with configurable quality
        ydl_opts = self._get_ydl_options()
        ydl_opts['default_search'] = 'scsearch'
        
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
