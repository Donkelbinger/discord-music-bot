"""
Resource Optimization Module

This module provides RAM and CPU optimizations for the Discord music bot,
focusing on efficient memory usage and reduced CPU overhead while maintaining
audio quality and functionality.
"""

import asyncio
import gc
import logging
import os
import psutil
import weakref
from collections import deque
from typing import Any, Deque, Optional, Dict, List, Tuple
import time
import json
from pathlib import Path

logger = logging.getLogger('ResourceOptimizer')

class MemoryOptimizedQueue:
    """
    Memory-optimized queue implementation that avoids expensive copy operations.
    """
    
    def __init__(self, maxlen: Optional[int] = None):
        self._queue: Deque[Tuple[Any, str, Any]] = deque(maxlen=maxlen)
        self._size = 0
    
    def append(self, item: Tuple[Any, str, Any]) -> None:
        """Add item to queue without memory copies."""
        self._queue.append(item)
        self._size += 1
    
    def popleft(self) -> Tuple[Any, str, Any]:
        """Remove and return leftmost item without memory copies."""
        if not self._queue:
            raise IndexError("pop from empty queue")
        self._size -= 1
        return self._queue.popleft()
    
    def remove_by_index(self, index: int) -> Tuple[Any, str, Any]:
        """
        Memory-efficient removal by index without full list conversion.
        Uses deque rotation instead of list conversion.
        """
        if index < 0 or index >= len(self._queue):
            raise IndexError(f"Index {index} out of range")
        
        # Rotate queue to bring target item to front, then remove
        self._queue.rotate(-index)
        removed_item = self._queue.popleft()
        self._queue.rotate(index)
        
        self._size -= 1
        return removed_item
    
    def clear(self) -> int:
        """Clear queue and return previous size."""
        old_size = self._size
        self._queue.clear()
        self._size = 0
        return old_size
    
    def __len__(self) -> int:
        return self._size
    
    def __iter__(self):
        return iter(self._queue)
    
    def __getitem__(self, index: int) -> Tuple[Any, str, Any]:
        """Access item by index without creating list copy."""
        if index < 0:
            index = len(self._queue) + index
        
        # Rotate to access item without list conversion
        self._queue.rotate(-index)
        item = self._queue[0]
        self._queue.rotate(index)
        return item

class SmartGarbageCollector:
    """
    Intelligent garbage collection that reduces CPU overhead while maintaining
    memory efficiency.
    """
    
    def __init__(self):
        self.last_gc_time = time.time()
        self.gc_threshold_mb = int(os.getenv('GC_THRESHOLD_MB', '50'))  # MB
        self.gc_interval_seconds = int(os.getenv('GC_INTERVAL_SECONDS', '30'))  # seconds
        self.force_gc_threshold_mb = int(os.getenv('FORCE_GC_THRESHOLD_MB', '100'))  # MB
        
        # Track process memory
        self.process = psutil.Process()
        self.baseline_memory_mb = self._get_memory_usage_mb()
        
        logger.info(f"SmartGC initialized - Threshold: {self.gc_threshold_mb}MB, Interval: {self.gc_interval_seconds}s")
    
    def _get_memory_usage_mb(self) -> float:
        """Get current memory usage in MB."""
        try:
            return self.process.memory_info().rss / 1024 / 1024
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return 0.0
    
    def should_collect(self, force: bool = False) -> bool:
        """
        Determine if garbage collection should run based on intelligent heuristics.
        """
        if force:
            return True
        
        current_time = time.time()
        current_memory_mb = self._get_memory_usage_mb()
        memory_increase_mb = current_memory_mb - self.baseline_memory_mb
        
        # Force GC if memory usage is critically high
        if memory_increase_mb > self.force_gc_threshold_mb:
            logger.warning(f"Force GC triggered: {memory_increase_mb:.1f}MB increase")
            return True
        
        # Regular GC based on time and memory thresholds
        time_since_gc = current_time - self.last_gc_time
        
        if (time_since_gc > self.gc_interval_seconds and 
            memory_increase_mb > self.gc_threshold_mb):
            logger.debug(f"Smart GC triggered: {memory_increase_mb:.1f}MB increase, {time_since_gc:.1f}s elapsed")
            return True
        
        return False
    
    def collect(self, generation: int = 2) -> Dict[str, Any]:
        """
        Perform garbage collection and return statistics.
        """
        memory_before = self._get_memory_usage_mb()
        
        # Perform collection
        collected_objects = gc.collect(generation)
        
        memory_after = self._get_memory_usage_mb()
        memory_freed_mb = memory_before - memory_after
        
        self.last_gc_time = time.time()
        
        # Update baseline if we freed significant memory
        if memory_freed_mb > 5.0:  # 5MB threshold
            self.baseline_memory_mb = memory_after
        
        stats = {
            'objects_collected': collected_objects,
            'memory_before_mb': memory_before,
            'memory_after_mb': memory_after,
            'memory_freed_mb': memory_freed_mb,
            'generation': generation
        }
        
        if memory_freed_mb > 1.0:  # Log if freed > 1MB
            logger.info(f"GC freed {memory_freed_mb:.1f}MB, collected {collected_objects} objects")
        
        return stats

class StreamingJSONWriter:
    """
    Memory-efficient JSON writer that streams data instead of building
    large data structures in memory.
    """
    
    def __init__(self, file_path: Path):
        self.file_path = file_path
    
    async def write_queue_data(self, guild_queues: Dict[int, Any], voice_states: Dict[int, Any]) -> None:
        """
        Stream queue data to JSON file without building large intermediate structures.
        """
        try:
            # Write directly to file in streaming fashion
            async with open(self.file_path, 'w', encoding='utf-8') as f:
                await f.write('{\n')
                
                first_entry = True
                
                for guild_id, queue in guild_queues.items():
                    if not queue and (guild_id not in voice_states or not voice_states[guild_id].current):
                        continue
                    
                    if not first_entry:
                        await f.write(',\n')
                    first_entry = False
                    
                    # Write guild entry directly without intermediate dict
                    await f.write(f'  "{guild_id}": {{\n')
                    
                    # Write guild info
                    guild = voice_states[guild_id].ctx.guild if guild_id in voice_states else None
                    guild_name = guild.name if guild else f"Guild {guild_id}"
                    await f.write(f'    "guild_name": {json.dumps(guild_name)},\n')
                    
                    # Write current song if exists
                    current_song = None
                    if guild_id in voice_states and voice_states[guild_id].current:
                        vs = voice_states[guild_id]
                        current_song = {
                            'title': vs.current_title or 'Unknown',
                            'requester_id': vs.current_requester.id if vs.current_requester else 0,
                            'requester_name': vs.current_requester.name if vs.current_requester else 'Unknown'
                        }
                    
                    await f.write(f'    "current_song": {json.dumps(current_song)},\n')
                    
                    # Write queue items
                    await f.write('    "queue": [\n')
                    
                    for i, (_, title, requester) in enumerate(queue):
                        if i > 0:
                            await f.write(',\n')
                        
                        item = {
                            'title': title,
                            'requester_id': requester.id,
                            'requester_name': requester.name
                        }
                        await f.write(f'      {json.dumps(item)}')
                    
                    await f.write('\n    ],\n')
                    await f.write(f'    "saved_at": {time.time()}\n')
                    await f.write('  }')
                
                await f.write('\n}\n')
            
            logger.debug(f"Streaming JSON write completed to {self.file_path}")
        
        except Exception as e:
            logger.error(f"Failed to write streaming JSON: {e}", exc_info=True)
            raise

class ResourceMonitor:
    """
    Lightweight resource monitoring for optimization insights.
    """
    
    def __init__(self):
        self.process = psutil.Process()
        self.monitoring_enabled = os.getenv('ENABLE_RESOURCE_MONITORING', 'false').lower() == 'true'
        self.log_interval = int(os.getenv('RESOURCE_LOG_INTERVAL_SECONDS', '300'))  # 5 minutes
        
        if self.monitoring_enabled:
            asyncio.create_task(self._monitoring_loop())
    
    async def _monitoring_loop(self) -> None:
        """Background task to monitor resource usage."""
        while True:
            try:
                await asyncio.sleep(self.log_interval)
                await self._log_resource_stats()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in resource monitoring: {e}")
    
    async def _log_resource_stats(self) -> None:
        """Log current resource usage statistics."""
        try:
            memory_mb = self.process.memory_info().rss / 1024 / 1024
            cpu_percent = self.process.cpu_percent()
            
            logger.info(f"Resource usage - Memory: {memory_mb:.1f}MB, CPU: {cpu_percent:.1f}%")
            
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            logger.warning("Cannot access process information for resource monitoring")
    
    def get_current_stats(self) -> Dict[str, float]:
        """Get current resource usage statistics."""
        try:
            memory_info = self.process.memory_info()
            return {
                'memory_rss_mb': memory_info.rss / 1024 / 1024,
                'memory_vms_mb': memory_info.vms / 1024 / 1024,
                'cpu_percent': self.process.cpu_percent(),
                'num_threads': self.process.num_threads()
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return {'error': 'Cannot access process information'}

# Global instances
smart_gc = SmartGarbageCollector()
resource_monitor = ResourceMonitor()

# Utility functions for integration
async def smart_garbage_collect(force: bool = False) -> Optional[Dict[str, Any]]:
    """Smart garbage collection that only runs when beneficial."""
    if smart_gc.should_collect(force=force):
        return smart_gc.collect()
    return None

def create_optimized_queue(maxlen: Optional[int] = None) -> MemoryOptimizedQueue:
    """Create a memory-optimized queue instance."""
    return MemoryOptimizedQueue(maxlen=maxlen)

async def write_queue_data_efficiently(file_path: Path, guild_queues: Dict[int, Any], voice_states: Dict[int, Any]) -> None:
    """Write queue data using streaming JSON writer."""
    writer = StreamingJSONWriter(file_path)
    await writer.write_queue_data(guild_queues, voice_states)
