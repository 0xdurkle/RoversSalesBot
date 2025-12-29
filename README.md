# NFT Sales Discord Bot

A Discord bot that monitors NFT sales for a specific Ethereum NFT collection and posts real-time sale notifications to a Discord channel.

## Features

- ✅ Real-time NFT sale monitoring via Alchemy webhooks
- ✅ Automatic posting to Discord channel when sales occur
- ✅ Support for ETH and WETH payments
- ✅ Detection and categorization of sweeps (Single, Mini 2-5, Big 6-10, Huge 11+)
- ✅ NFT image display in Discord embeds
- ✅ `/lastsale` command to fetch and display the most recent sale
- ✅ Price formatting with proper decimal handling (max 4 decimals, trailing zeros removed)

## Setup

### 1. Install Dependencies

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Create `.env` File

Create a `.env` file in the project root with the following variables:

```bash
# Discord Configuration
DISCORD_BOT_TOKEN=your_discord_bot_token_here
DISCORD_CHANNEL_ID=your_discord_channel_id_here

# NFT Collection
NFT_CONTRACT_ADDRESS=0xYourNFTContractAddressHere

# Alchemy API
ALCHEMY_API_KEY=your_alchemy_api_key_here

# Webhook Configuration
WEBHOOK_PORT=8080
WEBHOOK_SECRET=

# Polling Configuration (optional - webhooks are primary)
POLL_INTERVAL_SECONDS=0
ENABLE_BACKUP_POLLING=false
```

**Note**: Your credentials have been configured. Make sure the `.env` file exists with these values.

### 3. Run the Bot

```bash
python bot.py
```

The bot will:
- Connect to Discord
- Start the webhook server on port 8080
- Listen for Alchemy webhooks
- Process and post sales to Discord

## Testing Locally

### Test `/lastsale` Command

1. Start the bot
2. In Discord, use the `/lastsale` command
3. Verify the sale is displayed correctly

### Test Webhook (Local Development)

1. Install ngrok: `brew install ngrok` (or download from ngrok.com)
2. Start the bot
3. In another terminal, run: `ngrok http 8080`
4. Copy the ngrok URL (e.g., `https://abc123.ngrok.io`)
5. In Alchemy dashboard, create a webhook pointing to: `https://abc123.ngrok.io/webhook`

## Deployment

### Railway (Recommended)

1. Push code to GitHub
2. Create new project on Railway
3. Connect GitHub repository
4. Add all environment variables from `.env` file
5. Deploy

The webhook URL will be: `https://your-app.railway.app/webhook`

### Configure Alchemy Webhook

1. Go to Alchemy Dashboard → Notify
2. Create new webhook
3. Select "NFT Transfers"
4. Set Event Type: `NFT_TRANSFER`
5. Add contract address filter: Your NFT contract address
6. Set URL: `https://your-app.railway.app/webhook`
7. Save webhook

## Commands

- `/lastsale` - Fetch and display the most recent sale from the collection

## Project Structure

```
RoversSalesBot/
├── bot.py              # Main Discord bot file
├── sales_fetcher.py    # Alchemy API integration module
├── requirements.txt    # Python dependencies
├── runtime.txt         # Python version (3.11)
├── Procfile           # Deployment configuration
├── .env               # Environment variables (not in git)
└── README.md          # This file
```

## Troubleshooting

### Bot Not Responding
- Check that bot token is correct
- Verify bot has MESSAGE CONTENT INTENT enabled in Discord Developer Portal
- Check bot is in the server and has permissions

### Webhook Not Receiving Events
- Verify webhook URL is accessible
- Check contract address filter in Alchemy
- Ensure webhook is active in Alchemy dashboard
- Check logs for errors

### Sales Not Posting
- Verify Discord channel ID is correct
- Check bot has permissions in channel
- Review logs for errors

### Price Shows 0 ETH
- Check if it's a WETH sale (should show WETH)
- Verify transaction has value/transfers
- Check price fetching logic in logs

## Support

For detailed documentation, see the PRD.md file.

