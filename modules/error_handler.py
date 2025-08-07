"""
Error Handler Module

This module provides standardized error handling functionality including:
- Consistent error logging with correlation IDs
- User-friendly error messages with contextual suggestions
- Error classification and intelligent categorization
- Safe cleanup operations with error handling
"""

import asyncio
import logging
import time
from typing import Optional, Union, Callable, Any, Tuple, Dict

import aiohttp
import discord
import yt_dlp

logger = logging.getLogger('ErrorHandler')


class ErrorType:
    """Standardized error types for consistent handling."""
    COMMAND = "command"  # User command errors
    AUDIO = "audio"     # Audio processing/playbook errors
    NETWORK = "network" # Network/connection errors
    SYSTEM = "system"   # System/internal errors
    USER_INPUT = "user_input"  # User input validation errors
    PERMISSION = "permission"  # Permission/access errors


class ErrorHandler:
    """Handles standardized error logging, user feedback, and cleanup operations."""
    
    def __init__(self, bot: discord.Client) -> None:
        """Initialize the ErrorHandler."""
        self.bot = bot
        logger.info("ErrorHandler initialized")
    
    async def handle_error(self, 
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
    
    async def safe_cleanup(self, 
                           cleanup_func: Union[Callable, Callable[..., Any]], 
                           context_info: str,
                           *args: Any, **kwargs: Any) -> bool:
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
            await self.handle_error(
                error=e,
                error_type=ErrorType.SYSTEM,
                context_info=f"cleanup operation ({context_info})"
            )
            return False
    
    def extract_error_details(self, error: Exception) -> Tuple[str, str]:
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
    
    async def handle_command_error(self, interaction: discord.Interaction, error: Exception) -> None:
        """Handle errors specifically from Discord command interactions."""
        error_type, user_message = self.extract_error_details(error)
        await self.handle_error(
            error=error,
            error_type=error_type,
            context_info=f"{interaction.command.name if interaction.command else 'unknown'} command in guild: {interaction.guild.name}",
            interaction=interaction,
            user_message=user_message
        )
    
    async def handle_audio_error(self, error: Exception, context: str, channel: Optional[discord.TextChannel] = None) -> None:
        """Handle audio processing or playback errors with optional channel notification."""
        error_type, user_message = self.extract_error_details(error)
        
        # Log the error
        await self.handle_error(
            error=error,
            error_type=error_type,
            context_info=context
        )
        
        # Send channel notification if provided
        if channel:
            try:
                await channel.send(
                    f"⚠️ **Audio Error**\n"
                    f"❌ {user_message}\n"
                    f"💡 **Info:** Attempting to continue with next song"
                )
            except discord.HTTPException as e:
                logger.warning(f"Failed to send audio error notification: {e}")
    
    async def handle_network_error(self, error: Exception, context: str, retry_suggestion: bool = True) -> None:
        """Handle network-related errors with optional retry suggestions."""
        error_type, user_message = self.extract_error_details(error)
        
        # Use warning level for network errors as they're often temporary
        logger.warning(f"{context}: {user_message}")
        
        if retry_suggestion:
            logger.info(f"Network error in {context} - users should retry in a moment")
    
    async def handle_system_error(self, error: Exception, context: str, critical: bool = False) -> None:
        """Handle system errors with appropriate logging level."""
        error_type, user_message = self.extract_error_details(error)
        
        if critical:
            logger.critical(f"CRITICAL SYSTEM ERROR - {context}: {user_message}", exc_info=True)
        else:
            logger.error(f"{context}: {user_message}", exc_info=True)
