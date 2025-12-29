# Real-Time Sales Webhook Setup

## ✅ Yes, the bot WILL push live sales in real-time!

The bot is already configured to receive real-time sales via Alchemy webhooks. Here's how to set it up:

## Current Status

- ✅ Webhook server is running on port 8080
- ✅ Webhook handler is implemented and ready
- ✅ Real-time processing is configured
- ⚠️ **You need to configure the Alchemy webhook** (see below)

## Setup Steps

### 1. Get Your Webhook URL

**For Local Testing:**
```bash
# Install ngrok
brew install ngrok  # or download from ngrok.com

# In a separate terminal, run:
ngrok http 8080

# Copy the HTTPS URL (e.g., https://abc123.ngrok.io)
# Your webhook URL will be: https://abc123.ngrok.io/webhook
```

**For Production (Railway/Heroku/etc.):**
- Your webhook URL will be: `https://your-app.railway.app/webhook`
- Make sure your deployment platform exposes port 8080

### 2. Configure Alchemy Webhook

1. Go to [Alchemy Dashboard](https://dashboard.alchemy.com/)
2. Navigate to **"Notify"** → **"Webhooks"**
3. Click **"Create Webhook"**
4. Configure:
   - **Network**: Ethereum Mainnet
   - **Event Type**: NFT Transfers
   - **Contract Address**: `0xe0e7f149959c6cac0dDc2Cb4ab27942BFFdA1eb4` (your NFT contract)
   - **URL**: Your webhook URL (from step 1)
   - **Name**: "NFT Sales Bot" (or any name)

5. Click **"Create Webhook"**

### 3. Verify Webhook is Working

Once configured, the bot will:
- ✅ Receive webhooks in real-time when sales happen
- ✅ Process them automatically
- ✅ Post to Discord channel immediately
- ✅ Log all webhook events

Check the logs:
```bash
tail -f bot.log | grep -i webhook
```

## How It Works

1. **Alchemy detects NFT transfer** → Sends webhook to your bot
2. **Bot receives webhook** → Processes asynchronously (doesn't block)
3. **Bot fetches price** → Checks ETH and WETH
4. **Bot fetches images** → Gets NFT metadata
5. **Bot posts to Discord** → Creates embed and sends

## Troubleshooting

### Webhook Not Receiving Events

1. **Check webhook URL is accessible:**
   ```bash
   curl https://your-webhook-url.com/webhook
   # Should return something (even if 404, means it's reachable)
   ```

2. **Check Alchemy dashboard:**
   - Go to Alchemy Dashboard → Notify → Your Webhook
   - Check "Delivery Status" - should show recent deliveries
   - Check for any errors

3. **Check bot logs:**
   ```bash
   tail -f bot.log | grep -i "webhook\|Received"
   ```

4. **Verify contract address:**
   - Make sure the contract address in Alchemy webhook matches exactly
   - Should be: `0xe0e7f149959c6cac0dDc2Cb4ab27942BFFdA1eb4`

### Sales Not Posting

1. **Check Discord channel ID:**
   - Make sure `DISCORD_CHANNEL_ID` in `.env` is correct
   - Bot must be in the server with that channel

2. **Check bot permissions:**
   - Bot needs: Send Messages, Embed Links, Read Message History

3. **Check logs for errors:**
   ```bash
   tail -f bot.log | grep -i error
   ```

## Testing

To test if webhooks are working:

1. **Use Alchemy's test webhook feature** (if available in dashboard)
2. **Or trigger a real sale** and watch the logs
3. **Check Discord channel** for the sale notification

## Notes

- Webhooks are processed asynchronously (fire-and-forget)
- Bot always returns 200 OK to keep webhook healthy
- Duplicate sales are automatically filtered
- Sweeps (multiple NFTs) are automatically grouped

