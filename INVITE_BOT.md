# How to Invite Bot with Correct Permissions

## Why Permissions Don't Save in Developer Portal

The **"Bot Permissions"** section in the Discord Developer Portal is just a **calculator tool** - it doesn't actually set permissions! It's only used to calculate the permissions integer value.

**Permissions are actually set when you invite the bot to a server** using an OAuth2 URL with the correct permissions.

## How to Invite Your Bot with Correct Permissions

### Method 1: Use the Invite Link Script (Recommended)

1. **Get your Bot's Client ID**:
   - Go to https://discord.com/developers/applications
   - Select your bot application
   - Go to "General Information"
   - Copy the "Application ID" (this is your Client ID)

2. **Add to .env file** (optional):
   ```
   DISCORD_CLIENT_ID=your_client_id_here
   ```

3. **Run the script**:
   ```bash
   python get_invite_link.py
   ```

4. **Copy the generated URL** and open it in your browser

5. **Select your server** and click "Authorize"

### Method 2: Use OAuth2 URL Generator

1. Go to https://discord.com/developers/applications
2. Select your bot application
3. Go to **"OAuth2"** → **"URL Generator"**
4. Under **"Scopes"**, check:
   - ✅ `bot`
   - ✅ `applications.commands`
5. Under **"Bot Permissions"**, check:
   - ✅ **Send Messages**
   - ✅ **Embed Links** (required for embed images)
   - ✅ **Attach Files** (optional, for file attachments)
   - ✅ **Read Message History**
6. Copy the generated URL at the bottom
7. Open the URL in your browser
8. Select your server and authorize

### Method 3: Manual URL Construction

If you know your Client ID, you can construct the URL manually:

```
https://discord.com/api/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=49664&scope=bot%20applications.commands
```

Replace `YOUR_CLIENT_ID` with your bot's Application ID.

**Permissions breakdown:**
- Send Messages: 2048
- Embed Links: 16384
- Attach Files: 32768
- **Total: 49664**

## Verify Permissions After Inviting

After inviting the bot:

1. Go to your Discord server
2. Right-click on your bot → **"Edit Server Profile"** (or go to Server Settings → Members → select your bot)
3. Check the permissions tab
4. You should see:
   - ✅ Send Messages
   - ✅ Embed Links
   - ✅ Attach Files (if you included it)

## Important Notes

- **The "Bot Permissions" calculator in Developer Portal doesn't set permissions** - it's just a tool
- **Permissions are set per-server** when you invite the bot
- **You need to re-invite the bot** if you want to change permissions
- **The bot must have "Embed Links" permission** for embed images to work
- **"Attach Files" is optional** but recommended for file attachments

## Troubleshooting

### Bot doesn't have permissions after inviting
- Make sure you used the OAuth2 URL Generator or the invite link script
- Check that you selected the correct server when authorizing
- Try removing and re-inviting the bot

### Images still don't show
- Verify the bot has "Embed Links" permission in your server
- Check server settings → Members → Your Bot → Permissions
- The issue might be the image URL, not permissions (check logs)

