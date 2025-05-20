Write-Host "YouTube Cookie Validator" -ForegroundColor Green
Write-Host "======================" -ForegroundColor Green
Write-Host ""

$cookieFile = Join-Path $PSScriptRoot "cookies.txt"

if (-not (Test-Path $cookieFile)) {
    Write-Host "ERROR: Cookie file not found at: $cookieFile" -ForegroundColor Red
    exit 1
}

$content = Get-Content $cookieFile -Raw
$size = (Get-Item $cookieFile).Length

Write-Host "Cookie file found: $cookieFile" -ForegroundColor Green
Write-Host "File size: $size bytes" -ForegroundColor Cyan

# Check common YouTube authentication cookies
$authCookies = @("SID", "HSID", "SSID", "APISID", "SAPISID", "LOGIN_INFO", "__Secure-1PSID", "__Secure-3PSID")
$foundAuth = $false

foreach ($cookie in $authCookies) {
    if ($content -match $cookie) {
        $foundAuth = $true
        Write-Host "Found authentication cookie: $cookie" -ForegroundColor Green
    } else {
        Write-Host "Missing authentication cookie: $cookie" -ForegroundColor Yellow
    }
}

Write-Host ""
if ($foundAuth) {
    Write-Host "The cookie file contains some YouTube authentication cookies." -ForegroundColor Green
    Write-Host "This should be sufficient for playing age-restricted content." -ForegroundColor Green
} else {
    Write-Host "WARNING: No YouTube authentication cookies found!" -ForegroundColor Red
    Write-Host "This cookie file may not work for age-restricted content." -ForegroundColor Red
    Write-Host ""
    Write-Host "Recommendations:" -ForegroundColor Cyan
    Write-Host "1. Make sure you are logged into YouTube in your browser before exporting cookies" -ForegroundColor Cyan
    Write-Host "2. Use a tool like 'Get cookies.txt' extension for Chrome to export cookies" -ForegroundColor Cyan
    Write-Host "3. Ensure your YouTube account has age verification completed" -ForegroundColor Cyan
} 