#!/bin/bash

echo -e "\e[32mYouTube Cookie Setup Helper\e[0m"
echo -e "\e[32m==============================\e[0m"
echo ""

# Get the script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
DEST_FILE="$SCRIPT_DIR/www.youtube.com_cookies.txt"

# Check if cookie file already exists
if [ -f "$DEST_FILE" ]; then
    echo -e "\e[32mCookie file already exists in the bot directory!\e[0m"
    echo "Location: $DEST_FILE"
    exit 0
fi

# Ask user for cookie file location
echo -e "\e[33mCookie file 'www.youtube.com_cookies.txt' not found in the bot directory.\e[0m"
echo -e "\e[36mPlease enter the full path to your cookie file:\e[0m"
read SOURCE_PATH

# Validate the path
if [ ! -f "$SOURCE_PATH" ]; then
    echo -e "\e[31mError: File not found at '$SOURCE_PATH'.\e[0m"
    echo "Please check the path and try again."
    exit 1
fi

# Copy the file to the bot directory
cp "$SOURCE_PATH" "$DEST_FILE" 2>/dev/null
if [ $? -eq 0 ]; then
    echo -e "\e[32mSuccess! Cookie file copied to the bot directory.\e[0m"
    echo "The bot should now be able to play age-restricted content."
else
    echo -e "\e[31mError copying file. Please check permissions and try again.\e[0m"
    exit 1
fi

# Verify the file exists
if [ -f "$DEST_FILE" ]; then
    echo -e "\e[32mVerified: Cookie file is properly placed at: $DEST_FILE\e[0m"
else
    echo -e "\e[31mWarning: Something went wrong. Cookie file not found at the destination.\e[0m"
fi

echo ""
echo -e "\e[36mTo run the bot, launch it normally. It will now use your cookies for authentication.\e[0m" 