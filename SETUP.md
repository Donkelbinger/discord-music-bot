# Discord Music Bot - Setup Guide

## Quick Start

1. **Copy the environment template:**
   ```bash
   cp .env.example .env
   ```

2. **Configure your bot:**
   - Edit `.env` with your Discord token and authorized guild IDs
   - See detailed configuration guide below

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run the bot:**
   ```bash
   python bot.py
   ```

## Discord Bot Setup

### 1. Create Discord Application

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click "New Application"
3. Give your bot a name and create it

### 2. Configure Bot Settings

1. Go to the "Bot" section
2. Click "Add Bot"
3. **Copy the Token** (this is your `DISCORD_TOKEN`)
4. Under "Privileged Gateway Intents", enable:
   - ✅ Message Content Intent
   - ✅ Server Members Intent (optional)

### 3. Bot Permissions

Your bot needs these permissions:
- ✅ Connect (to join voice channels)
- ✅ Speak (to play music)
- ✅ Send Messages (to respond to commands)
- ✅ Use Slash Commands
- ✅ Embed Links (for rich music info)
- ✅ Read Message History

**Permission Integer:** `277028203584`

### 4. Invite Bot to Server

Use this URL template (replace `YOUR_BOT_CLIENT_ID`):
```
https://discord.com/api/oauth2/authorize?client_id=YOUR_BOT_CLIENT_ID&permissions=277028203584&scope=bot%20applications.commands
```

## Configuration Guide

### Required Settings

```bash
# Your Discord Bot Token
DISCORD_TOKEN=your_bot_token_here

# Discord Server IDs where bot is allowed (comma-separated)
AUTHORIZED_GUILD_IDS=123456789012345678,987654321098765432
```

**How to get Guild ID:**
1. Enable Developer Mode in Discord (Settings → Appearance → Developer Mode)
2. Right-click your server name → "Copy Server ID"

### Cookie Setup for Age-Restricted Content

If you want to play age-restricted YouTube videos:

1. **Export cookies from your browser:**
   - Install "Get cookies.txt" extension for Chrome/Firefox
   - Go to YouTube and make sure you're logged in
   - Click the extension and export cookies for `youtube.com`
   - Save as `cookies.txt` in the bot directory

2. **Configure cookie path:**
   ```bash
   YOUTUBE_COOKIE_FILE=./cookies.txt
   ```

3. **Verify cookies work:**
   ```bash
   python check_cookies.py
   ```

### Optional Enhancements

```bash
# Logging level (DEBUG, INFO, WARNING, ERROR)
LOG_LEVEL=INFO

# Maximum songs in queue per server
MAX_QUEUE_SIZE=50

# Auto-disconnect timeout (seconds)
VOICE_TIMEOUT=300

# Audio quality (best, high, medium, low)
AUDIO_QUALITY=medium
```

## Docker Deployment

### Using Docker Compose (Recommended)

1. **Create docker-compose.yml:**
   ```yaml
   version: '3.8'
   services:
     discord-bot:
       build: .
       environment:
         - DISCORD_TOKEN=${DISCORD_TOKEN}
         - AUTHORIZED_GUILD_IDS=${AUTHORIZED_GUILD_IDS}
         - LOG_LEVEL=INFO
       volumes:
         - ./cookies.txt:/app/cookies.txt:ro  # Optional: for age-restricted content
       restart: unless-stopped
   ```

2. **Run with:**
   ```bash
   docker-compose up -d
   ```

### Using Docker Directly

```bash
# Build image
docker build -t discord-music-bot .

# Run container
docker run -d \
  --name discord-music-bot \
  -e DISCORD_TOKEN=your_token_here \
  -e AUTHORIZED_GUILD_IDS=your_guild_ids \
  discord-music-bot
```

## Kubernetes Deployment

The bot includes Helm charts for Kubernetes deployment:

```bash
# Install with Helm
helm install discord-bot ./helm \
  --set bot.token=your_token_here \
  --set bot.authorizedGuilds="guild1,guild2"
```

## Troubleshooting

### Bot doesn't respond to commands
1. Check bot is online in Discord
2. Verify `AUTHORIZED_GUILD_IDS` includes your server ID
3. Make sure bot has necessary permissions
4. Check logs: `docker logs discord-music-bot`

### Age-restricted content fails
1. Verify cookie file exists and is readable
2. Run cookie validation: `python check_cookies.py`
3. Re-export cookies if they've expired
4. Ensure YouTube account is age-verified

### Bot leaves server immediately
- Your server ID is not in `AUTHORIZED_GUILD_IDS`
- This is a security feature - add your server ID to the authorized list

### Audio quality issues
1. Check your internet connection
2. Try different audio quality setting
3. Verify FFmpeg is installed (included in Docker image)

## Security Best Practices

1. **Never commit `.env` file** to version control
2. **Rotate tokens regularly** if compromised
3. **Limit authorized guilds** to trusted servers only
4. **Monitor logs** for unauthorized access attempts
5. **Use strong passwords** for any database connections
6. **Keep dependencies updated** regularly

## Support

For issues and questions:
1. Check the troubleshooting section above
2. Review bot logs for error messages
3. Verify your configuration matches this guide
4. Ensure all dependencies are installed correctly

## Commands

Once configured, your bot supports these slash commands:
- `/play <song>` - Play a song from YouTube or SoundCloud
- `/skip` - Skip the current song
- `/queue` - Show the current queue
- `/clear` - Clear the queue
- `/stop` - Stop playback and disconnect
