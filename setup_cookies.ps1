Write-Host "YouTube Cookie Setup Helper" -ForegroundColor Green
Write-Host "==============================" -ForegroundColor Green
Write-Host 

# Check if cookie file exists in current directory
$destFile = Join-Path $PSScriptRoot "www.youtube.com_cookies.txt"

if (Test-Path $destFile) {
    Write-Host "Cookie file already exists in the bot directory!" -ForegroundColor Green
    Write-Host "Location: $destFile"
    exit 0
}

# Ask user for cookie file location
Write-Host "Cookie file 'www.youtube.com_cookies.txt' not found in the bot directory." -ForegroundColor Yellow
Write-Host "Please enter the full path to your cookie file:" -ForegroundColor Cyan
$sourcePath = Read-Host

# Validate the path
if (-not (Test-Path $sourcePath)) {
    Write-Host "Error: File not found at '$sourcePath'." -ForegroundColor Red
    Write-Host "Please check the path and try again."
    exit 1
}

# Copy the file to the bot directory
try {
    Copy-Item -Path $sourcePath -Destination $destFile -Force
    Write-Host "Success! Cookie file copied to the bot directory." -ForegroundColor Green
    Write-Host "The bot should now be able to play age-restricted content."
} catch {
    Write-Host "Error copying file: $_" -ForegroundColor Red
    exit 1
}

# Verify the file exists
if (Test-Path $destFile) {
    Write-Host "Verified: Cookie file is properly placed at: $destFile" -ForegroundColor Green
} else {
    Write-Host "Warning: Something went wrong. Cookie file not found at the destination." -ForegroundColor Red
}

Write-Host 
Write-Host "To run the bot, launch it normally. It will now use your cookies for authentication." -ForegroundColor Cyan 