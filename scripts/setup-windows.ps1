# retention.sh - Windows Setup Script
# One-command setup for Android device emulation
#
# Usage (Run as Administrator):
#   Set-ExecutionPolicy Bypass -Scope Process -Force; iex ((New-Object System.Net.WebClient).DownloadString('https://raw.githubusercontent.com/HomenShum/retention/main/scripts/setup-windows.ps1'))

Write-Host "🚀 retention.sh - Device Emulation Setup for Windows" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""

function Test-Command($cmdname) {
    return [bool](Get-Command -Name $cmdname -ErrorAction SilentlyContinue)
}

function Write-Status($installed, $name) {
    if ($installed) {
        Write-Host "✓ $name is installed" -ForegroundColor Green
    } else {
        Write-Host "✗ $name is not installed" -ForegroundColor Red
    }
    return $installed
}

# Check existing installations
Write-Host "📋 Checking existing installations..." -ForegroundColor Yellow
Write-Host ""

$chocoOk = Write-Status (Test-Command "choco") "Chocolatey"
$javaOk = Write-Status (Test-Command "java") "Java"
$nodeOk = Write-Status (Test-Command "node") "Node.js"
$adbOk = Write-Status (Test-Command "adb") "Android ADB"

Write-Host ""

# Install Chocolatey if needed
if (-not $chocoOk) {
    Write-Host "📦 Installing Chocolatey..." -ForegroundColor Yellow
    Set-ExecutionPolicy Bypass -Scope Process -Force
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
    iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
}

# Install Java if needed
if (-not $javaOk) {
    Write-Host "☕ Installing OpenJDK 17..." -ForegroundColor Yellow
    choco install openjdk17 -y
}

# Install Node.js if needed
if (-not $nodeOk) {
    Write-Host "📗 Installing Node.js..." -ForegroundColor Yellow
    choco install nodejs-lts -y
}

# Set up Android SDK
$ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"
if (-not (Test-Path $ANDROID_HOME)) {
    Write-Host "🤖 Installing Android Command Line Tools..." -ForegroundColor Yellow
    
    New-Item -ItemType Directory -Force -Path "$ANDROID_HOME\cmdline-tools" | Out-Null
    
    $cmdlineToolsUrl = "https://dl.google.com/android/repository/commandlinetools-win-10406996_latest.zip"
    $zipPath = "$env:TEMP\cmdline-tools.zip"
    
    Invoke-WebRequest -Uri $cmdlineToolsUrl -OutFile $zipPath
    Expand-Archive -Path $zipPath -DestinationPath "$ANDROID_HOME\cmdline-tools" -Force
    Rename-Item "$ANDROID_HOME\cmdline-tools\cmdline-tools" "$ANDROID_HOME\cmdline-tools\latest"
    Remove-Item $zipPath
}

# Set environment variables
Write-Host ""
Write-Host "🔧 Setting up environment variables..." -ForegroundColor Yellow

[Environment]::SetEnvironmentVariable("ANDROID_HOME", $ANDROID_HOME, "User")
$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
$newPaths = @(
    "$ANDROID_HOME\cmdline-tools\latest\bin",
    "$ANDROID_HOME\platform-tools",
    "$ANDROID_HOME\emulator"
)

foreach ($newPath in $newPaths) {
    if ($currentPath -notlike "*$newPath*") {
        $currentPath = "$currentPath;$newPath"
    }
}
[Environment]::SetEnvironmentVariable("Path", $currentPath, "User")

# Update current session
$env:ANDROID_HOME = $ANDROID_HOME
$env:Path = "$env:Path;$ANDROID_HOME\cmdline-tools\latest\bin;$ANDROID_HOME\platform-tools;$ANDROID_HOME\emulator"

# Accept licenses and install SDK components
Write-Host ""
Write-Host "📱 Installing Android SDK components..." -ForegroundColor Yellow

$sdkmanager = "$ANDROID_HOME\cmdline-tools\latest\bin\sdkmanager.bat"
echo "y" | & $sdkmanager --licenses 2>$null

& $sdkmanager "platform-tools" "emulator" "platforms;android-34" "system-images;android-34;google_apis;x86_64"

# Create AVD
Write-Host ""
Write-Host "📲 Creating Android Virtual Device..." -ForegroundColor Yellow

$avdmanager = "$ANDROID_HOME\cmdline-tools\latest\bin\avdmanager.bat"
$emulator = "$ANDROID_HOME\emulator\emulator.exe"

$existingAvds = & $emulator -list-avds 2>$null
if ($existingAvds -notcontains "Pixel_7_API_34") {
    echo "no" | & $avdmanager create avd -n "Pixel_7_API_34" -k "system-images;android-34;google_apis;x86_64" -d "pixel_7" --force
    Write-Host "✓ Created AVD: Pixel_7_API_34" -ForegroundColor Green
} else {
    Write-Host "✓ AVD already exists: Pixel_7_API_34" -ForegroundColor Green
}

# Final status
Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "✅ Setup Complete!" -ForegroundColor Green
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "To start using device emulation:" -ForegroundColor White
Write-Host ""
Write-Host "  1. Restart your terminal (or open a new PowerShell window)" -ForegroundColor White
Write-Host ""
Write-Host "  2. Launch an emulator:" -ForegroundColor White
Write-Host "     emulator -avd Pixel_7_API_34" -ForegroundColor Gray
Write-Host ""
Write-Host "  3. Start the retention.sh backend:" -ForegroundColor White
Write-Host "     cd backend; python -m uvicorn app.main:app --reload" -ForegroundColor Gray
Write-Host ""
Write-Host "  4. Open the retention.sh UI:" -ForegroundColor White
Write-Host "     http://localhost:5173/demo/devices" -ForegroundColor Gray

