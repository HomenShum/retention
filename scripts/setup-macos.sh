#!/bin/bash
# retention.sh - macOS Setup Script
# One-command setup for Android device emulation
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/HomenShum/retention/main/scripts/setup-macos.sh | bash

set -e

echo "🚀 retention.sh - Device Emulation Setup for macOS"
echo "=================================================="
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

check_installed() {
    if command -v "$1" &> /dev/null; then
        echo -e "${GREEN}✓${NC} $2 is installed"
        return 0
    else
        echo -e "${RED}✗${NC} $2 is not installed"
        return 1
    fi
}

# Check existing installations
echo "📋 Checking existing installations..."
echo ""

BREW_OK=$(check_installed brew "Homebrew" && echo 1 || echo 0)
JAVA_OK=$(check_installed java "Java" && echo 1 || echo 0)
NODE_OK=$(check_installed node "Node.js" && echo 1 || echo 0)
ADB_OK=$(check_installed adb "Android ADB" && echo 1 || echo 0)

echo ""

# Install Homebrew if needed
if [ "$BREW_OK" = "0" ]; then
    echo "📦 Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    
    # Add to path for Apple Silicon
    if [[ $(uname -m) == 'arm64' ]]; then
        echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
fi

# Install Java if needed
if [ "$JAVA_OK" = "0" ]; then
    echo "☕ Installing OpenJDK 17..."
    brew install openjdk@17
    sudo ln -sfn $(brew --prefix)/opt/openjdk@17/libexec/openjdk.jdk /Library/Java/JavaVirtualMachines/openjdk-17.jdk
fi

# Install Node.js if needed
if [ "$NODE_OK" = "0" ]; then
    echo "📗 Installing Node.js..."
    brew install node
fi

# Install Android SDK
ANDROID_HOME="$HOME/Library/Android/sdk"
if [ ! -d "$ANDROID_HOME" ]; then
    echo "🤖 Installing Android Command Line Tools..."
    
    # Create SDK directory
    mkdir -p "$ANDROID_HOME/cmdline-tools"
    
    # Download command line tools
    CMDLINE_TOOLS_URL="https://dl.google.com/android/repository/commandlinetools-mac-10406996_latest.zip"
    curl -L "$CMDLINE_TOOLS_URL" -o /tmp/cmdline-tools.zip
    unzip -q /tmp/cmdline-tools.zip -d "$ANDROID_HOME/cmdline-tools"
    mv "$ANDROID_HOME/cmdline-tools/cmdline-tools" "$ANDROID_HOME/cmdline-tools/latest"
    rm /tmp/cmdline-tools.zip
fi

# Set up environment variables
echo ""
echo "🔧 Setting up environment variables..."

SHELL_RC="$HOME/.zshrc"
if [ ! -f "$SHELL_RC" ]; then
    touch "$SHELL_RC"
fi

# Add Android environment if not already present
if ! grep -q "ANDROID_HOME" "$SHELL_RC"; then
    echo "" >> "$SHELL_RC"
    echo "# Android SDK (added by retention.sh setup)" >> "$SHELL_RC"
    echo "export ANDROID_HOME=\$HOME/Library/Android/sdk" >> "$SHELL_RC"
    echo "export PATH=\$PATH:\$ANDROID_HOME/cmdline-tools/latest/bin" >> "$SHELL_RC"
    echo "export PATH=\$PATH:\$ANDROID_HOME/platform-tools" >> "$SHELL_RC"
    echo "export PATH=\$PATH:\$ANDROID_HOME/emulator" >> "$SHELL_RC"
fi

# Source the updated profile
export ANDROID_HOME="$HOME/Library/Android/sdk"
export PATH="$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator"

# Accept licenses and install SDK components
echo ""
echo "📱 Installing Android SDK components..."
yes | "$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" --licenses > /dev/null 2>&1 || true

# Install required SDK components
"$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" \
    "platform-tools" \
    "emulator" \
    "platforms;android-34" \
    "system-images;android-34;google_apis;arm64-v8a"

# Create AVD if none exists
echo ""
echo "📲 Creating Android Virtual Device..."
if ! "$ANDROID_HOME/emulator/emulator" -list-avds | grep -q "Pixel_7_API_34"; then
    echo "no" | "$ANDROID_HOME/cmdline-tools/latest/bin/avdmanager" create avd \
        -n "Pixel_7_API_34" \
        -k "system-images;android-34;google_apis;arm64-v8a" \
        -d "pixel_7" \
        --force
    echo -e "${GREEN}✓${NC} Created AVD: Pixel_7_API_34"
else
    echo -e "${GREEN}✓${NC} AVD already exists: Pixel_7_API_34"
fi

# Final status
echo ""
echo "=================================================="
echo -e "${GREEN}✅ Setup Complete!${NC}"
echo "=================================================="
echo ""
echo "To start using device emulation:"
echo ""
echo "  1. Restart your terminal (or run: source ~/.zshrc)"
echo ""
echo "  2. Launch an emulator:"
echo "     emulator -avd Pixel_7_API_34 &"
echo ""
echo "  3. Start the retention.sh backend:"
echo "     cd backend && python -m uvicorn app.main:app --reload"
echo ""
echo "  4. Open the retention.sh UI:"
echo "     http://localhost:5173/demo/devices"
echo ""
echo "Need help? Visit: https://docs.retention.com/setup"

