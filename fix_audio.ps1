# Fix audio stuttering in Discord music bot
Write-Host "Applying audio stuttering fixes..."

$content = Get-Content 'music_cog.py' -Raw

# Fix 1: Increase buffer size from 256k to 1024k
$content = $content -replace 'bufsize 256k', 'bufsize 1024k'

# Fix 2: Add maxrate and stereo options
$content = $content -replace '-vn -b:a 128k -bufsize 1024k -ar 48000', '-vn -b:a 128k -bufsize 1024k -maxrate 192k -ar 48000 -ac 2'

# Fix 3: Add threading and analysis improvements
$content = $content -replace 'loudnorm=I=-16:TP=-1.5:LRA=11', 'loudnorm=I=-16:TP=-1.5:LRA=11 -fflags +genpts -thread_queue_size 1024'

# Fix 4: Improve reconnection handling
$content = $content -replace '-reconnect_delay_max 5 -timeout 60000000', '-reconnect_delay_max 5 -reconnect_at_eof 1 -timeout 60000000 -multiple_requests 1'

$content | Set-Content 'music_cog.py' -NoNewline

Write-Host "Audio fixes applied successfully!"
