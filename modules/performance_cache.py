"""
Performance Caching Module

This module provides intelligent caching for media extraction operations to dramatically
improve response times and reduce API calls.
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Optional, Tuple, Any

import aiofiles

# Import advanced optimizations
from .advanced_optimizer import register_batch_operation, add_to_batch, register_adaptive_task

logger = logging.getLogger('PerformanceCache')

@dataclass
class CacheEntry:
    """Represents a cached media extraction result."""
    audio_url: str
    title: str
    platform: str
    cached_at: float
    hit_count: int = 0
    last_accessed: float = None
    
    def is_expired(self, max_age_hours: int = 24) -> bool:
        """Check if cache entry has expired."""
        return (time.time() - self.cached_at) > (max_age_hours * 3600)
    
    def is_stale(self, max_age_hours: int = 6) -> bool:
        """Check if cache entry is stale (should be refreshed in background)."""
        return (time.time() - self.cached_at) > (max_age_hours * 3600)

class PerformanceCache:
    """Intelligent caching system for media extraction operations."""
    
    def __init__(self):
        """Initialize the performance cache with advanced optimizations."""
        self.cache: Dict[str, CacheEntry] = {}
        
        # Cache configuration
        self.CACHE_ENABLED = os.getenv('ENABLE_PERFORMANCE_CACHE', 'true').lower() == 'true'
        self.CACHE_FILE = Path(os.getenv('CACHE_FILE', 'data/performance_cache.json'))
        self.MAX_CACHE_SIZE = int(os.getenv('MAX_CACHE_SIZE', '1000'))
        self.CACHE_MAX_AGE_HOURS = int(os.getenv('CACHE_MAX_AGE_HOURS', '24'))
        self.CACHE_STALE_HOURS = int(os.getenv('CACHE_STALE_HOURS', '6'))
        
        # Ensure cache directory exists
        self.CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        
        # Advanced optimization integration
        self.pending_cache_saves = 0
        self.batch_save_threshold = int(os.getenv('CACHE_BATCH_SAVE_THRESHOLD', '20'))
        
        logger.info(f"PerformanceCache initialized - Enabled: {self.CACHE_ENABLED}, Max size: {self.MAX_CACHE_SIZE}")
        
        if self.CACHE_ENABLED:
            # Register batch save processor for efficient cache operations
            register_batch_operation('cache_save', self._batch_save_processor, 
                                   batch_size=self.batch_save_threshold, timeout=30.0)
            
            # Use adaptive task management for cache operations
            asyncio.create_task(self._initialize_optimized_cache())
    
    def _generate_cache_key(self, query: str) -> str:
        """Generate a consistent cache key for a query."""
        # Normalize query for consistent caching
        normalized = query.lower().strip()
        return hashlib.sha256(normalized.encode()).hexdigest()
    
    async def get(self, query: str) -> Optional[Tuple[str, str, str]]:
        """Get cached result for a query."""
        if not self.CACHE_ENABLED:
            return None
            
        cache_key = self._generate_cache_key(query)
        entry = self.cache.get(cache_key)
        
        if not entry:
            return None
            
        # Check if expired
        if entry.is_expired(self.CACHE_MAX_AGE_HOURS):
            logger.debug(f"Cache entry expired for query: {query[:50]}...")
            del self.cache[cache_key]
            return None
        
        # Update access statistics
        entry.hit_count += 1
        entry.last_accessed = time.time()
        
        # Check if stale (needs background refresh)
        if entry.is_stale(self.CACHE_STALE_HOURS):
            logger.debug(f"Cache entry stale, queuing refresh for: {query[:50]}...")
            await self.refresh_queue.put((query, cache_key))
        
        logger.debug(f"Cache HIT for query: {query[:50]}... (hit count: {entry.hit_count})")
        return (entry.audio_url, entry.title, entry.platform)
    
    async def set(self, query: str, audio_url: str, title: str, platform: str) -> None:
        """Store result in cache with batched saving."""
        if not self.CACHE_ENABLED:
            return
            
        cache_key = self._generate_cache_key(query)
        
        # Check cache size limit
        if len(self.cache) >= self.MAX_CACHE_SIZE:
            await self._evict_old_entries()
        
        self.cache[cache_key] = CacheEntry(
            audio_url=audio_url,
            title=title,
            platform=platform,
            cached_at=time.time(),
            last_accessed=time.time()
        )
        
        logger.debug(f"Cache SET for query: {query[:50]}... -> {title}")
        
        # Use batch processing for cache saves to reduce I/O overhead
        self.pending_cache_saves += 1
        if self.pending_cache_saves >= self.batch_save_threshold:
            await add_to_batch('cache_save', {'timestamp': time.time()})
            self.pending_cache_saves = 0
    
    async def _evict_old_entries(self) -> None:
        """Remove old cache entries to make space."""
        # Sort by last accessed time and remove oldest 20%
        sorted_entries = sorted(
            self.cache.items(),
            key=lambda x: x[1].last_accessed or 0
        )
        
        evict_count = max(1, len(sorted_entries) // 5)  # Remove 20%
        
        for cache_key, _ in sorted_entries[:evict_count]:
            del self.cache[cache_key]
        
        logger.info(f"Evicted {evict_count} old cache entries")
    
    async def load_cache(self) -> None:
        """Load cache from persistent storage."""
        try:
            if not self.CACHE_FILE.exists():
                logger.info("No cache file found, starting with empty cache")
                return
            
            async with aiofiles.open(self.CACHE_FILE, 'r', encoding='utf-8') as f:
                content = await f.read()
                cache_data = json.loads(content)
            
            # Convert to CacheEntry objects
            loaded_count = 0
            for cache_key, entry_data in cache_data.items():
                try:
                    entry = CacheEntry(**entry_data)
                    
                    # Skip expired entries
                    if not entry.is_expired(self.CACHE_MAX_AGE_HOURS):
                        self.cache[cache_key] = entry
                        loaded_count += 1
                    
                except Exception as e:
                    logger.warning(f"Skipping invalid cache entry: {e}")
                    continue
            
            logger.info(f"Loaded {loaded_count} cache entries from persistent storage")
            
        except Exception as e:
            logger.error(f"Failed to load cache: {e}")
    
    async def save_cache(self) -> None:
        """Save cache to persistent storage."""
        if not self.CACHE_ENABLED:
            return
            
        try:
            # Convert CacheEntry objects to dict for JSON serialization
            cache_data = {
                cache_key: asdict(entry) for cache_key, entry in self.cache.items()
            }
            
            async with aiofiles.open(self.CACHE_FILE, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(cache_data, indent=2, ensure_ascii=False))
            
            logger.debug(f"Cache saved to persistent storage ({len(cache_data)} entries)")
            
        except Exception as e:
            logger.error(f"Failed to save cache: {e}")
    
    async def _initialize_optimized_cache(self) -> None:
        """Initialize cache with optimized task management."""
        # Load cache data
        await self.load_cache()
        
        # Register adaptive tasks for cache operations
        await register_adaptive_task('cache_refresh', self._refresh_stale_entries, 
                                    interval=300.0, priority='low')  # 5 minutes
        await register_adaptive_task('cache_cleanup', self._cleanup_expired_entries, 
                                    interval=3600.0, priority='low')  # 1 hour
    
    async def _batch_save_processor(self, operations: list) -> None:
        """Process batched cache save operations."""
        logger.debug(f"Processing batch cache save for {len(operations)} operations")
        await self.save_cache()
    
    async def _refresh_stale_entries(self) -> None:
        """Refresh stale cache entries - adaptive task version."""
        if not self.CACHE_ENABLED:
            return
            
        current_time = time.time()
        stale_entries = [
            (key, entry) for key, entry in self.cache.items()
            if entry.is_stale(self.CACHE_STALE_HOURS)
        ]
        
        if stale_entries:
            logger.debug(f"Found {len(stale_entries)} stale cache entries for refresh")
            # In a full implementation, this would trigger re-extraction
            # For now, just log the refresh opportunity
    
    async def _cleanup_expired_entries(self) -> None:
        """Clean up expired cache entries - adaptive task version."""
        if not self.CACHE_ENABLED:
            return
            
        initial_size = len(self.cache)
        expired_keys = [
            key for key, entry in self.cache.items()
            if entry.is_expired(self.CACHE_MAX_AGE_HOURS)
        ]
        
        for key in expired_keys:
            del self.cache[key]
        
        if expired_keys:
            logger.info(f"Cleaned up {len(expired_keys)} expired cache entries")
    
    async def _background_refresh_worker(self) -> None:
        """Worker task to refresh stale cache entries in background."""
        while True:
            try:
                # Wait for stale entries to refresh
                query, cache_key = await self.refresh_queue.get()
                
                logger.debug(f"Background refreshing cache for: {query[:50]}...")
                
                # TODO: This would need to be integrated with MediaExtractor
                # For now, we'll just log the refresh request
                # In full implementation, this would re-extract the media info
                
                # Mark task as done
                self.refresh_queue.task_done()
                
                # Small delay to prevent overwhelming the system
                await asyncio.sleep(1)
                
            except asyncio.CancelledError:
                logger.info("Background cache refresh task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in background cache refresh: {e}")
                continue
    
    async def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache performance statistics."""
        if not self.CACHE_ENABLED:
            return {"enabled": False}
        
        total_entries = len(self.cache)
        total_hits = sum(entry.hit_count for entry in self.cache.values())
        
        # Calculate age distribution
        now = time.time()
        fresh_count = sum(1 for entry in self.cache.values() 
                         if (now - entry.cached_at) < (self.CACHE_STALE_HOURS * 3600))
        stale_count = total_entries - fresh_count
        
        return {
            "enabled": True,
            "total_entries": total_entries,
            "max_cache_size": self.MAX_CACHE_SIZE,
            "total_hits": total_hits,
            "fresh_entries": fresh_count,
            "stale_entries": stale_count,
            "cache_file": str(self.CACHE_FILE),
            "utilization_percent": round((total_entries / self.MAX_CACHE_SIZE) * 100, 1)
        }
    
    async def cleanup(self) -> None:
        """Cleanup cache resources."""
        if self.refresh_task and not self.refresh_task.done():
            self.refresh_task.cancel()
            try:
                await self.refresh_task
            except asyncio.CancelledError:
                pass
        
        await self.save_cache()
        logger.info("PerformanceCache cleanup completed")

# Global cache instance
performance_cache = PerformanceCache()
