$cookieFile = Join-Path $PSScriptRoot "cookies.txt"

if (Test-Path $cookieFile) {
    $size = (Get-Item $cookieFile).Length
    Write-Output "Cookie file found: $cookieFile"
    Write-Output "File size: $size bytes"
    
    $content = Get-Content $cookieFile -Raw
    
    # Basic check for common YouTube cookies
    $authCookies = @("SID", "HSID", "SSID", "LOGIN_INFO")
    $found = 0
    
    foreach ($cookie in $authCookies) {
        if ($content -match $cookie) {
            $found++
            Write-Output "Found cookie: $cookie"
        }
    }
    
    if ($found -gt 0) {
        Write-Output "Found $found authentication cookies. This should work for YouTube."
    } else {
        Write-Output "WARNING: No YouTube authentication cookies found in the file."
    }
} else {
    Write-Output "ERROR: Cookie file not found at: $cookieFile"
} 