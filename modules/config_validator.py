"""
Configuration Validation Module

This module provides comprehensive environment variable validation
to ensure the bot has all required configuration before startup.
"""

import os
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger('ConfigValidator')


class ConfigValidationError(Exception):
    """Raised when configuration validation fails."""
    pass


class ConfigValidator:
    """Validates environment variables and configuration settings."""
    
    def __init__(self):
        """Initialize the configuration validator."""
        self.validation_errors: List[str] = []
        self.warnings: List[str] = []
        self.config: Dict[str, Any] = {}
        
    def validate_all_config(self) -> Dict[str, Any]:
        """
        Validate all environment variables and return validated configuration.
        
        Returns:
            Dict containing all validated configuration values
            
        Raises:
            ConfigValidationError: If any required configuration is invalid
        """
        logger.info("Starting comprehensive configuration validation...")
        
        # Reset validation state
        self.validation_errors.clear()
        self.warnings.clear()
        self.config.clear()
        
        # Validate required configuration
        self._validate_discord_config()
        self._validate_guild_access_config()
        
        # Validate optional configuration with defaults
        self._validate_queue_config()
        self._validate_persistence_config()
        self._validate_media_config()
        
        # Check for validation errors
        if self.validation_errors:
            error_summary = "\n".join([f"  ❌ {error}" for error in self.validation_errors])
            raise ConfigValidationError(
                f"Configuration validation failed:\n{error_summary}\n\n"
                f"💡 Please check your .env file and fix the above issues.\n"
                f"📋 See .env.example for reference configuration."
            )
        
        # Log warnings if any
        if self.warnings:
            warning_summary = "\n".join([f"  ⚠️ {warning}" for warning in self.warnings])
            logger.warning(f"Configuration warnings:\n{warning_summary}")
        
        logger.info("✅ Configuration validation completed successfully")
        return self.config.copy()
    
    def _validate_discord_config(self) -> None:
        """Validate Discord-related configuration."""
        # DISCORD_TOKEN (Required)
        token = os.getenv('DISCORD_TOKEN', '').strip()
        if not token:
            self.validation_errors.append("DISCORD_TOKEN is required but not set")
        elif len(token) < 50:  # Discord tokens are typically 59+ characters
            self.validation_errors.append("DISCORD_TOKEN appears to be invalid (too short)")
        else:
            # Don't store the full token in config for security
            self.config['DISCORD_TOKEN'] = token
            logger.info("✅ DISCORD_TOKEN validated")
    
    def _validate_guild_access_config(self) -> None:
        """Validate guild access control configuration."""
        guild_ids_raw = os.getenv('AUTHORIZED_GUILD_IDS', '').strip()
        
        if not guild_ids_raw:
            self.warnings.append("AUTHORIZED_GUILD_IDS not set - bot will use global commands (less secure)")
            self.config['AUTHORIZED_GUILDS'] = set()
            return
        
        try:
            # Parse comma-separated guild IDs
            guild_ids = [int(gid.strip()) for gid in guild_ids_raw.split(',') if gid.strip()]
            if not guild_ids:
                self.validation_errors.append("AUTHORIZED_GUILD_IDS is set but contains no valid guild IDs")
            else:
                self.config['AUTHORIZED_GUILDS'] = set(guild_ids)
                logger.info(f"✅ AUTHORIZED_GUILD_IDS validated ({len(guild_ids)} guilds)")
        except ValueError as e:
            self.validation_errors.append(f"AUTHORIZED_GUILD_IDS contains invalid guild ID (must be integers): {e}")
    
    def _validate_queue_config(self) -> None:
        """Validate queue-related configuration."""
        # MAX_QUEUE_SIZE (Optional with default)
        max_queue_size = self._validate_int_env(
            'MAX_QUEUE_SIZE', 
            default=50, 
            min_value=1, 
            max_value=1000,
            description="maximum queue size"
        )
        if max_queue_size is not None:
            self.config['MAX_QUEUE_SIZE'] = max_queue_size
        
        # USER_QUEUE_LIMIT (Optional with default)
        user_queue_limit = self._validate_int_env(
            'USER_QUEUE_LIMIT',
            default=20,
            min_value=1,
            max_value=100,
            description="per-user queue limit"
        )
        if user_queue_limit is not None:
            self.config['USER_QUEUE_LIMIT'] = user_queue_limit
            
            # Validate relationship between limits
            if (user_queue_limit is not None and max_queue_size is not None and 
                user_queue_limit > max_queue_size):
                self.warnings.append(f"USER_QUEUE_LIMIT ({user_queue_limit}) is greater than MAX_QUEUE_SIZE ({max_queue_size})")
    
    def _validate_persistence_config(self) -> None:
        """Validate queue persistence configuration."""
        # ENABLE_QUEUE_PERSISTENCE (Optional with default)
        enable_persistence = self._validate_bool_env('ENABLE_QUEUE_PERSISTENCE', default=True)
        self.config['ENABLE_QUEUE_PERSISTENCE'] = enable_persistence
        
        if enable_persistence:
            # QUEUE_PERSISTENCE_FILE (Optional with default)
            persistence_file = os.getenv('QUEUE_PERSISTENCE_FILE', 'data/queue_state.json').strip()
            persistence_path = Path(persistence_file)
            
            # Validate parent directory can be created
            try:
                persistence_path.parent.mkdir(parents=True, exist_ok=True)
                self.config['QUEUE_PERSISTENCE_FILE'] = persistence_path
                logger.info(f"✅ Queue persistence file path validated: {persistence_path}")
            except (OSError, PermissionError) as e:
                self.validation_errors.append(f"Cannot create directory for QUEUE_PERSISTENCE_FILE '{persistence_file}': {e}")
            
            # QUEUE_PERSISTENCE_MAX_AGE_HOURS (Optional with default)
            max_age = self._validate_int_env(
                'QUEUE_PERSISTENCE_MAX_AGE_HOURS',
                default=24,
                min_value=1,
                max_value=168,  # 1 week
                description="queue persistence maximum age in hours"
            )
            if max_age is not None:
                self.config['QUEUE_PERSISTENCE_MAX_AGE_HOURS'] = max_age
            
            # QUEUE_SAVE_INTERVAL_MINUTES (Optional with default)
            save_interval = self._validate_int_env(
                'QUEUE_SAVE_INTERVAL_MINUTES',
                default=5,
                min_value=1,
                max_value=60,
                description="queue save interval in minutes"
            )
            if save_interval is not None:
                self.config['QUEUE_SAVE_INTERVAL_MINUTES'] = save_interval
    
    def _validate_media_config(self) -> None:
        """Validate media extraction configuration."""
        # YOUTUBE_COOKIE_FILE (Optional)
        cookie_file = os.getenv('YOUTUBE_COOKIE_FILE', '').strip()
        if cookie_file:
            cookie_path = Path(cookie_file)
            if not cookie_path.exists():
                self.warnings.append(f"YOUTUBE_COOKIE_FILE '{cookie_file}' does not exist - age-restricted content may not work")
            elif not cookie_path.is_file():
                self.validation_errors.append(f"YOUTUBE_COOKIE_FILE '{cookie_file}' exists but is not a file")
            else:
                logger.info(f"✅ YouTube cookie file validated: {cookie_path}")
                
        self.config['YOUTUBE_COOKIE_FILE'] = cookie_file
    
    def _validate_int_env(self, var_name: str, default: int, min_value: int = None, 
                         max_value: int = None, description: str = "") -> Optional[int]:
        """
        Validate an integer environment variable.
        
        Args:
            var_name: Environment variable name
            default: Default value if not set
            min_value: Minimum allowed value
            max_value: Maximum allowed value  
            description: Human-readable description for error messages
            
        Returns:
            Validated integer value or None if validation failed
        """
        value_str = os.getenv(var_name, '').strip()
        
        if not value_str:
            logger.info(f"✅ {var_name} using default value: {default}")
            return default
        
        try:
            value = int(value_str)
        except ValueError:
            self.validation_errors.append(f"{var_name} must be an integer, got: '{value_str}'")
            return None
        
        # Range validation
        if min_value is not None and value < min_value:
            self.validation_errors.append(f"{var_name} must be >= {min_value}, got: {value}")
            return None
            
        if max_value is not None and value > max_value:
            self.validation_errors.append(f"{var_name} must be <= {max_value}, got: {value}")
            return None
        
        logger.info(f"✅ {var_name} validated: {value}")
        return value
    
    def _validate_bool_env(self, var_name: str, default: bool) -> bool:
        """
        Validate a boolean environment variable.
        
        Args:
            var_name: Environment variable name
            default: Default value if not set
            
        Returns:
            Validated boolean value
        """
        value_str = os.getenv(var_name, '').strip().lower()
        
        if not value_str:
            logger.info(f"✅ {var_name} using default value: {default}")
            return default
        
        if value_str in ('true', '1', 'yes', 'on', 'enabled'):
            logger.info(f"✅ {var_name} validated: True")
            return True
        elif value_str in ('false', '0', 'no', 'off', 'disabled'):
            logger.info(f"✅ {var_name} validated: False")
            return False
        else:
            self.warnings.append(f"{var_name} has invalid boolean value '{value_str}', using default: {default}")
            return default
    
    def get_config_summary(self) -> str:
        """Get a summary of the current configuration."""
        if not self.config:
            return "No configuration loaded"
        
        summary_lines = ["📋 Configuration Summary:"]
        
        # Discord config
        if 'DISCORD_TOKEN' in self.config:
            token_preview = self.config['DISCORD_TOKEN'][:20] + "..." if len(self.config['DISCORD_TOKEN']) > 20 else "[hidden]"
            summary_lines.append(f"  🔑 Discord Token: {token_preview}")
        
        # Guild access
        if 'AUTHORIZED_GUILDS' in self.config:
            guild_count = len(self.config['AUTHORIZED_GUILDS'])
            if guild_count > 0:
                summary_lines.append(f"  🛡️ Authorized Guilds: {guild_count} configured")
            else:
                summary_lines.append(f"  🌍 Guild Access: Global (all servers)")
        
        # Queue config
        if 'MAX_QUEUE_SIZE' in self.config:
            summary_lines.append(f"  📋 Max Queue Size: {self.config['MAX_QUEUE_SIZE']}")
        if 'USER_QUEUE_LIMIT' in self.config:
            summary_lines.append(f"  👤 User Queue Limit: {self.config['USER_QUEUE_LIMIT']}")
        
        # Persistence
        if self.config.get('ENABLE_QUEUE_PERSISTENCE', False):
            summary_lines.append(f"  💾 Queue Persistence: Enabled")
            if 'QUEUE_PERSISTENCE_FILE' in self.config:
                summary_lines.append(f"    📂 File: {self.config['QUEUE_PERSISTENCE_FILE']}")
        else:
            summary_lines.append(f"  💾 Queue Persistence: Disabled")
        
        # Media config
        cookie_file = self.config.get('YOUTUBE_COOKIE_FILE', '')
        if cookie_file:
            summary_lines.append(f"  🍪 YouTube Cookies: {Path(cookie_file).name}")
        else:
            summary_lines.append(f"  🍪 YouTube Cookies: Not configured")
        
        return "\n".join(summary_lines)
