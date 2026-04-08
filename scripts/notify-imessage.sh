#!/bin/bash
# Send iMessage/SMS notification to user
# Usage: ./notify-imessage.sh "Your message here"

PHONE="+14083335386"
SMS_ACCOUNT="B3463984-8E3E-44FE-9049-A2EB72F91337"
MESSAGE="${1:-[TA Agent] No message provided}"

# Escape special characters for AppleScript
ESCAPED_MSG=$(echo "$MESSAGE" | sed 's/\\/\\\\/g; s/"/\\"/g')

osascript -e "
tell application \"Messages\"
    set targetService to account id \"$SMS_ACCOUNT\"
    set targetBuddy to participant \"$PHONE\" of targetService
    send \"$ESCAPED_MSG\" to targetBuddy
end tell" 2>/dev/null

if [ $? -eq 0 ]; then
    echo "[notify-imessage] Sent to $PHONE"
else
    echo "[notify-imessage] Failed to send to $PHONE"
fi
