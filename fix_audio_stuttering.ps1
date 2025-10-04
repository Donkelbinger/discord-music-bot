# PowerShell script to fix audio stuttering in Discord music bot
# This script optimizes FFmpeg buffering settings for smoother audio playback

Write-Host "Fixing audio stuttering issues in Discord music bot..."

# Read the current file
$content = Get-Content 'music_cog.py' -Raw

# Replace the small buffer size with a larger one for smoother streaming
$content = $content -replace "bufsize 256k", "bufsize 1024k"

# Add maxrate for better bitrate control
$content = $content -replace "'-vn -b:a 128k -bufsize 1024k -ar 48000", "'-vn -b:a 128k -bufsize 1024k -maxrate 192k -ar 48000 -ac 2"

# Add additional FFmpeg flags for smoother streaming
$content = $content -replace "loudnorm=I=-16:TP=-1.5:LRA=11'", "loudnorm=I=-16:TP=-1.5:LRA=11 -fflags +genpts -thread_queue_size 1024 -analyzeduration 0 -probesize 32M'"

# Improve reconnection options
$content = $content -replace "'-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -timeout 60000000'", "'-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -reconnect_at_eof 1 -timeout 60000000 -multiple_requests 1 -seekable 0'"

# Write the updated content back
$content | Set-Content 'music_cog.py' -NoNewline

Write-Host "Audio stuttering fixes applied successfully!"
Write-Host ""
Write-Host "Key improvements made:"
Write-Host "  ✓ Increased buffer size from 256k to 1024k"
Write-Host "  ✓ Added maxrate control for better bitrate management"
Write-Host "  ✓ Enhanced audio threading and queue management"
Write-Host "  ✓ Improved network reconnection handling"
Write-Host "  ✓ Optimized streaming parameters"
Write-Host ""
Write-Host "These changes should significantly reduce audio stuttering and improve playback quality."
