"""
Advanced Performance Optimizer Module

This module provides advanced RAM and CPU optimizations including:
- Dynamic background task management
- Adaptive resource monitoring  
- Batch operation processing
- Connection pooling enhancements
- Memory object pooling
"""

import asyncio
import gc
import logging
import os
import time
import weakref
from collections import deque
from typing import Any, Dict, List, Optional, Set, Callable, Union
import psutil
from pathlib import Path
import json

logger = logging.getLogger('AdvancedOptimizer')

class AdaptiveTaskManager:
    """
    Manages background tasks with adaptive scheduling based on system load
    and activity levels to minimize CPU overhead.
    """
    
    def __init__(self):
        self.tasks: Dict[str, asyncio.Task] = {}
        self.task_intervals: Dict[str, float] = {}
        self.task_last_run: Dict[str, float] = {}
        self.system_load_threshold = float(os.getenv('SYSTEM_LOAD_THRESHOLD', '0.8'))  # 80% CPU
        self.adaptive_scaling = True
        
        # Track system performance for adaptive scheduling
        self.process = psutil.Process()
        self.baseline_cpu = 0.0
        
        logger.info("AdaptiveTaskManager initialized")
    
    async def register_task(self, name: str, coro_func: Callable, 
                          base_interval: float, priority: str = 'normal') -> None:
        """
        Register a background task with adaptive scheduling.
        
        Args:
            name: Unique task identifier
            coro_func: Coroutine function to execute
            base_interval: Base interval in seconds
            priority: 'high', 'normal', or 'low'
        """
        if name in self.tasks:
            logger.warning(f"Task {name} already registered, replacing")
            await self.stop_task(name)
        
        self.task_intervals[name] = base_interval
        self.task_last_run[name] = time.time()
        
        # Create task with adaptive wrapper
        self.tasks[name] = asyncio.create_task(
            self._adaptive_task_wrapper(name, coro_func, priority)
        )
        
        logger.debug(f"Registered adaptive task: {name} (interval: {base_interval}s)")
    
    async def _adaptive_task_wrapper(self, name: str, coro_func: Callable, priority: str):
        """Wrapper that adapts task execution based on system performance."""
        priority_multipliers = {'high': 0.8, 'normal': 1.0, 'low': 1.5}
        multiplier = priority_multipliers.get(priority, 1.0)
        
        while True:
            try:
                start_time = time.time()
                
                # Execute the task function
                await coro_func()
                
                # Calculate adaptive interval based on system load
                base_interval = self.task_intervals[name] * multiplier
                
                if self.adaptive_scaling:
                    cpu_percent = self.process.cpu_percent()
                    
                    if cpu_percent > self.system_load_threshold * 100:
                        # System under load - increase interval
                        adaptive_interval = base_interval * 1.5
                        logger.debug(f"High CPU load ({cpu_percent:.1f}%), increasing interval for {name}")
                    elif cpu_percent < 0.3 * 100:  # Very low load
                        # System idle - decrease interval for better responsiveness  
                        adaptive_interval = base_interval * 0.8
                    else:
                        adaptive_interval = base_interval
                else:
                    adaptive_interval = base_interval
                
                self.task_last_run[name] = time.time()
                await asyncio.sleep(max(adaptive_interval, 1.0))  # Minimum 1s interval
                
            except asyncio.CancelledError:
                logger.debug(f"Adaptive task {name} cancelled")
                break
            except Exception as e:
                logger.error(f"Error in adaptive task {name}: {e}")
                # Back off on error
                await asyncio.sleep(self.task_intervals[name] * 2)
    
    async def stop_task(self, name: str) -> bool:
        """Stop and remove a registered task."""
        if name not in self.tasks:
            return False
        
        task = self.tasks[name]
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        del self.tasks[name]
        del self.task_intervals[name]
        del self.task_last_run[name]
        
        logger.debug(f"Stopped adaptive task: {name}")
        return True
    
    async def stop_all_tasks(self) -> None:
        """Stop all registered tasks."""
        for name in list(self.tasks.keys()):
            await self.stop_task(name)
        
        logger.info("All adaptive tasks stopped")
    
    def get_task_stats(self) -> Dict[str, Any]:
        """Get statistics for all registered tasks."""
        stats = {}
        current_time = time.time()
        
        for name, task in self.tasks.items():
            last_run = self.task_last_run.get(name, 0)
            stats[name] = {
                'status': 'running' if not task.done() else 'stopped',
                'interval': self.task_intervals.get(name, 0),
                'last_run_ago': current_time - last_run,
                'cancelled': task.cancelled() if hasattr(task, 'cancelled') else False
            }
        
        return stats

class BatchOperationProcessor:
    """
    Processes operations in batches to reduce overhead and improve performance.
    """
    
    def __init__(self):
        self.batch_queues: Dict[str, deque] = {}
        self.batch_timers: Dict[str, float] = {}
        self.batch_processors: Dict[str, Callable] = {}
        self.batch_sizes: Dict[str, int] = {}
        self.batch_timeouts: Dict[str, float] = {}
        
        # Default configuration
        self.default_batch_size = int(os.getenv('DEFAULT_BATCH_SIZE', '10'))
        self.default_batch_timeout = float(os.getenv('DEFAULT_BATCH_TIMEOUT', '5.0'))  # seconds
        
        logger.info("BatchOperationProcessor initialized")
    
    def register_batch_processor(self, operation_type: str, processor_func: Callable,
                                batch_size: Optional[int] = None, 
                                batch_timeout: Optional[float] = None) -> None:
        """
        Register a batch processor for a specific operation type.
        
        Args:
            operation_type: Unique identifier for the operation type
            processor_func: Function to process batched operations
            batch_size: Maximum batch size (default: 10)
            batch_timeout: Maximum time to wait before processing batch (default: 5.0s)
        """
        self.batch_queues[operation_type] = deque()
        self.batch_processors[operation_type] = processor_func
        self.batch_sizes[operation_type] = batch_size or self.default_batch_size
        self.batch_timeouts[operation_type] = batch_timeout or self.default_batch_timeout
        self.batch_timers[operation_type] = time.time()
        
        logger.debug(f"Registered batch processor: {operation_type}")
    
    async def add_operation(self, operation_type: str, operation_data: Any) -> None:
        """Add an operation to the batch queue."""
        if operation_type not in self.batch_queues:
            logger.warning(f"No batch processor registered for: {operation_type}")
            return
        
        queue = self.batch_queues[operation_type]
        queue.append(operation_data)
        
        # Check if we should process the batch
        current_time = time.time()
        should_process = False
        
        # Process if batch is full
        if len(queue) >= self.batch_sizes[operation_type]:
            should_process = True
            logger.debug(f"Processing batch for {operation_type} (size limit reached)")
        
        # Process if timeout reached
        elif current_time - self.batch_timers[operation_type] >= self.batch_timeouts[operation_type]:
            should_process = True
            logger.debug(f"Processing batch for {operation_type} (timeout reached)")
        
        if should_process:
            await self._process_batch(operation_type)
    
    async def _process_batch(self, operation_type: str) -> None:
        """Process a batch of operations."""
        queue = self.batch_queues[operation_type]
        if not queue:
            return
        
        # Extract all operations from queue
        operations = []
        while queue and len(operations) < self.batch_sizes[operation_type]:
            operations.append(queue.popleft())
        
        if not operations:
            return
        
        try:
            # Process the batch
            processor = self.batch_processors[operation_type]
            await processor(operations)
            
            # Reset timer
            self.batch_timers[operation_type] = time.time()
            
            logger.debug(f"Processed batch of {len(operations)} operations for {operation_type}")
            
        except Exception as e:
            logger.error(f"Error processing batch for {operation_type}: {e}")
            # Put operations back in queue for retry
            queue.extendleft(reversed(operations))
    
    async def flush_all_batches(self) -> None:
        """Process all pending batches immediately."""
        for operation_type in list(self.batch_queues.keys()):
            await self._process_batch(operation_type)
    
    def get_batch_stats(self) -> Dict[str, Any]:
        """Get statistics for all batch operations."""
        stats = {}
        current_time = time.time()
        
        for operation_type, queue in self.batch_queues.items():
            stats[operation_type] = {
                'pending_operations': len(queue),
                'batch_size_limit': self.batch_sizes[operation_type],
                'time_since_last_process': current_time - self.batch_timers[operation_type],
                'timeout': self.batch_timeouts[operation_type]
            }
        
        return stats

class MemoryObjectPool:
    """
    Object pool for frequently created/destroyed objects to reduce GC pressure.
    """
    
    def __init__(self):
        self.pools: Dict[str, deque] = {}
        self.pool_factories: Dict[str, Callable] = {}
        self.pool_limits: Dict[str, int] = {}
        self.pool_stats: Dict[str, Dict[str, int]] = {}
        
        # Default pool limit
        self.default_pool_limit = int(os.getenv('OBJECT_POOL_LIMIT', '100'))
        
        logger.info("MemoryObjectPool initialized")
    
    def register_object_type(self, object_type: str, factory_func: Callable, 
                           pool_limit: Optional[int] = None) -> None:
        """
        Register an object type for pooling.
        
        Args:
            object_type: Unique identifier for the object type
            factory_func: Function that creates new instances
            pool_limit: Maximum objects to keep in pool
        """
        self.pools[object_type] = deque()
        self.pool_factories[object_type] = factory_func
        self.pool_limits[object_type] = pool_limit or self.default_pool_limit
        self.pool_stats[object_type] = {'created': 0, 'reused': 0, 'returned': 0}
        
        logger.debug(f"Registered object pool: {object_type}")
    
    def get_object(self, object_type: str, *args, **kwargs) -> Any:
        """Get an object from the pool or create a new one."""
        if object_type not in self.pools:
            logger.warning(f"No object pool registered for: {object_type}")
            return None
        
        pool = self.pools[object_type]
        stats = self.pool_stats[object_type]
        
        if pool:
            # Reuse existing object
            obj = pool.popleft()
            stats['reused'] += 1
            logger.debug(f"Reused pooled object: {object_type}")
            return obj
        else:
            # Create new object
            factory = self.pool_factories[object_type]
            obj = factory(*args, **kwargs)
            stats['created'] += 1
            logger.debug(f"Created new pooled object: {object_type}")
            return obj
    
    def return_object(self, object_type: str, obj: Any) -> bool:
        """Return an object to the pool."""
        if object_type not in self.pools:
            return False
        
        pool = self.pools[object_type]
        limit = self.pool_limits[object_type]
        stats = self.pool_stats[object_type]
        
        if len(pool) < limit:
            # Reset object state if it has a reset method
            if hasattr(obj, 'reset'):
                obj.reset()
            
            pool.append(obj)
            stats['returned'] += 1
            logger.debug(f"Returned object to pool: {object_type}")
            return True
        else:
            # Pool is full, let object be garbage collected
            logger.debug(f"Pool full, discarding object: {object_type}")
            return False
    
    def clear_pool(self, object_type: str) -> int:
        """Clear a specific object pool."""
        if object_type not in self.pools:
            return 0
        
        pool = self.pools[object_type]
        cleared_count = len(pool)
        pool.clear()
        
        logger.debug(f"Cleared object pool: {object_type} ({cleared_count} objects)")
        return cleared_count
    
    def get_pool_stats(self) -> Dict[str, Any]:
        """Get statistics for all object pools."""
        stats = {}
        
        for object_type, pool in self.pools.items():
            pool_stats = self.pool_stats[object_type].copy()
            pool_stats.update({
                'current_pool_size': len(pool),
                'pool_limit': self.pool_limits[object_type],
                'utilization_percent': round((len(pool) / self.pool_limits[object_type]) * 100, 1)
            })
            stats[object_type] = pool_stats
        
        return stats

class AdvancedResourceMonitor:
    """
    Enhanced resource monitoring with adaptive intervals and intelligent alerting.
    """
    
    def __init__(self):
        self.process = psutil.Process()
        self.monitoring_enabled = os.getenv('ENABLE_ADVANCED_MONITORING', 'false').lower() == 'true'
        
        # Adaptive monitoring configuration
        self.base_interval = float(os.getenv('BASE_MONITORING_INTERVAL', '60.0'))  # 1 minute
        self.min_interval = float(os.getenv('MIN_MONITORING_INTERVAL', '10.0'))    # 10 seconds
        self.max_interval = float(os.getenv('MAX_MONITORING_INTERVAL', '300.0'))   # 5 minutes
        
        # Performance thresholds for adaptive monitoring
        self.memory_threshold_mb = int(os.getenv('MEMORY_ALERT_THRESHOLD_MB', '200'))
        self.cpu_threshold_percent = float(os.getenv('CPU_ALERT_THRESHOLD_PERCENT', '80.0'))
        
        # Historical data for trend analysis
        self.history_size = int(os.getenv('MONITORING_HISTORY_SIZE', '100'))
        self.memory_history = deque(maxlen=self.history_size)
        self.cpu_history = deque(maxlen=self.history_size)
        
        # Alert state tracking
        self.last_alert_time = 0
        self.alert_cooldown = float(os.getenv('ALERT_COOLDOWN_SECONDS', '300'))  # 5 minutes
        
        self.monitoring_task = None
        
        if self.monitoring_enabled:
            logger.info("AdvancedResourceMonitor initialized")
        else:
            logger.info("AdvancedResourceMonitor disabled")
    
    async def start_monitoring(self) -> None:
        """Start adaptive resource monitoring."""
        if not self.monitoring_enabled or self.monitoring_task:
            return
        
        self.monitoring_task = asyncio.create_task(self._adaptive_monitoring_loop())
        logger.info("Advanced resource monitoring started")
    
    async def stop_monitoring(self) -> None:
        """Stop resource monitoring."""
        if self.monitoring_task and not self.monitoring_task.done():
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass
        
        self.monitoring_task = None
        logger.info("Advanced resource monitoring stopped")
    
    async def _adaptive_monitoring_loop(self) -> None:
        """Main monitoring loop with adaptive intervals."""
        current_interval = self.base_interval
        
        while True:
            try:
                start_time = time.time()
                
                # Collect current metrics
                memory_mb = self.process.memory_info().rss / 1024 / 1024
                cpu_percent = self.process.cpu_percent()
                
                # Store in history
                self.memory_history.append(memory_mb)
                self.cpu_history.append(cpu_percent)
                
                # Analyze trends and adjust monitoring interval
                should_alert, alert_message = self._analyze_performance_trends(memory_mb, cpu_percent)
                
                if should_alert:
                    await self._send_performance_alert(alert_message)
                    # Increase monitoring frequency during high resource usage
                    current_interval = max(self.min_interval, current_interval * 0.5)
                else:
                    # Decrease monitoring frequency during stable periods
                    current_interval = min(self.max_interval, current_interval * 1.1)
                
                # Log detailed metrics periodically
                if len(self.memory_history) % 10 == 0:  # Every 10th measurement
                    await self._log_detailed_metrics(memory_mb, cpu_percent)
                
                await asyncio.sleep(current_interval)
                
            except asyncio.CancelledError:
                logger.debug("Advanced monitoring loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in advanced monitoring loop: {e}")
                await asyncio.sleep(self.base_interval)
    
    def _analyze_performance_trends(self, current_memory: float, current_cpu: float) -> tuple[bool, str]:
        """Analyze performance trends and determine if alerting is needed."""
        current_time = time.time()
        
        # Skip if still in alert cooldown
        if current_time - self.last_alert_time < self.alert_cooldown:
            return False, ""
        
        should_alert = False
        alert_parts = []
        
        # Memory analysis
        if current_memory > self.memory_threshold_mb:
            if len(self.memory_history) >= 5:
                # Check if memory usage is trending upward
                recent_avg = sum(list(self.memory_history)[-5:]) / 5
                if recent_avg > self.memory_threshold_mb * 0.8:
                    should_alert = True
                    alert_parts.append(f"High memory usage: {current_memory:.1f}MB (threshold: {self.memory_threshold_mb}MB)")
        
        # CPU analysis  
        if current_cpu > self.cpu_threshold_percent:
            if len(self.cpu_history) >= 3:
                # Check sustained high CPU usage
                recent_avg = sum(list(self.cpu_history)[-3:]) / 3
                if recent_avg > self.cpu_threshold_percent * 0.7:
                    should_alert = True
                    alert_parts.append(f"High CPU usage: {current_cpu:.1f}% (threshold: {self.cpu_threshold_percent}%)")
        
        alert_message = "; ".join(alert_parts) if alert_parts else ""
        return should_alert, alert_message
    
    async def _send_performance_alert(self, message: str) -> None:
        """Send performance alert (log for now, could be extended to Discord notifications)."""
        self.last_alert_time = time.time()
        logger.warning(f"PERFORMANCE ALERT: {message}")
        
        # Could be extended to send Discord webhook notifications, etc.
    
    async def _log_detailed_metrics(self, memory_mb: float, cpu_percent: float) -> None:
        """Log detailed performance metrics."""
        if len(self.memory_history) >= 2 and len(self.cpu_history) >= 2:
            memory_trend = "↑" if memory_mb > self.memory_history[-2] else "↓" 
            cpu_trend = "↑" if cpu_percent > self.cpu_history[-2] else "↓"
            
            logger.info(f"Performance: Memory {memory_mb:.1f}MB {memory_trend}, CPU {cpu_percent:.1f}% {cpu_trend}")
    
    def get_performance_summary(self) -> Dict[str, Any]:
        """Get comprehensive performance summary."""
        if not self.memory_history or not self.cpu_history:
            return {"error": "No performance data available"}
        
        memory_data = list(self.memory_history)
        cpu_data = list(self.cpu_history)
        
        return {
            "current": {
                "memory_mb": memory_data[-1] if memory_data else 0,
                "cpu_percent": cpu_data[-1] if cpu_data else 0,
            },
            "averages": {
                "memory_mb": sum(memory_data) / len(memory_data),
                "cpu_percent": sum(cpu_data) / len(cpu_data),
            },
            "peaks": {
                "memory_mb": max(memory_data),
                "cpu_percent": max(cpu_data),
            },
            "trends": {
                "memory_direction": "stable",  # Could calculate actual trends
                "cpu_direction": "stable",
            },
            "monitoring": {
                "enabled": self.monitoring_enabled,
                "samples_collected": len(memory_data),
                "history_limit": self.history_size,
            }
        }

# Global instances for easy access
adaptive_task_manager = AdaptiveTaskManager()
batch_processor = BatchOperationProcessor() 
memory_pool = MemoryObjectPool()
advanced_monitor = AdvancedResourceMonitor()

# Utility functions for integration
async def register_adaptive_task(name: str, coro_func: Callable, interval: float, priority: str = 'normal'):
    """Register a task with adaptive scheduling."""
    await adaptive_task_manager.register_task(name, coro_func, interval, priority)

async def stop_adaptive_task(name: str) -> bool:
    """Stop an adaptive task."""
    return await adaptive_task_manager.stop_task(name)

def register_batch_operation(operation_type: str, processor_func: Callable, 
                           batch_size: int = 10, timeout: float = 5.0):
    """Register a batch operation processor."""
    batch_processor.register_batch_processor(operation_type, processor_func, batch_size, timeout)

async def add_to_batch(operation_type: str, data: Any):
    """Add operation to batch queue."""
    await batch_processor.add_operation(operation_type, data)

def register_object_pool(object_type: str, factory_func: Callable, limit: int = 100):
    """Register an object type for pooling."""
    memory_pool.register_object_type(object_type, factory_func, limit)

def get_pooled_object(object_type: str, *args, **kwargs):
    """Get object from pool."""
    return memory_pool.get_object(object_type, *args, **kwargs)

def return_pooled_object(object_type: str, obj: Any) -> bool:
    """Return object to pool."""
    return memory_pool.return_object(object_type, obj)

async def start_advanced_monitoring():
    """Start advanced resource monitoring."""
    await advanced_monitor.start_monitoring()

async def stop_advanced_monitoring():
    """Stop advanced resource monitoring."""
    await advanced_monitor.stop_monitoring()

def get_advanced_stats() -> Dict[str, Any]:
    """Get comprehensive performance statistics."""
    return {
        "adaptive_tasks": adaptive_task_manager.get_task_stats(),
        "batch_operations": batch_processor.get_batch_stats(),
        "object_pools": memory_pool.get_pool_stats(),
        "performance": advanced_monitor.get_performance_summary()
    }
