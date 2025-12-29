# Alchemy Webhook Setup - REQUIRED for Real-Time Sales

## ⚠️ IMPORTANT: The bot will NOT receive real-time sales until you configure the Alchemy webhook!

## Step 1: Get Your Railway Webhook URL

1. Go to your Railway Dashboard
2. Find your deployed app
3. Copy the public URL (e.g., `https://your-app-name.railway.app`)
4. Your webhook endpoint is: `https://your-app-name.railway.app/webhook`

**Test it works:**
```bash
curl https://your-app-name.railway.app/health
# Should return: OK
```

## Step 2: Configure Alchemy Webhook

1. Go to [Alchemy Dashboard](https://dashboard.alchemy.com/)
2. Navigate to **"Notify"** → **"Webhooks"**
3. Click **"Create Webhook"** or **"Add Webhook"**
4. Fill in the form:
   - **Network**: Ethereum Mainnet
   - **Event Type**: Select **"NFT Transfers"**
   - **Contract Address**: `0xe0e7f149959c6cac0dDc2Cb4ab27942BFFdA1eb4`
   - **URL**: `https://your-app-name.railway.app/webhook` (use YOUR Railway URL)
   - **Name**: "NFT Sales Bot" (or any name you prefer)
5. Click **"Create Webhook"** or **"Save"**

## Step 3: Verify Webhook is Working

1. **Check Alchemy Dashboard:**
   - Go to your webhook in Alchemy
   - Check "Delivery Status" - should show recent deliveries
   - Look for any error messages

2. **Check Railway Logs:**
   - Go to Railway Dashboard → Your App → Logs
   - Look for messages like:
     - "Received webhook: ..."
     - "Processing sale ..."
     - "Posted sale to Discord"

3. **Test with a Real Sale:**
   - Wait for an NFT sale to happen
   - Check your Discord channel - should see the sale notification
   - If not, check the logs for errors

## Troubleshooting

### Webhook Not Receiving Events

1. **Verify URL is correct:**
   - Must be: `https://your-app.railway.app/webhook` (with `/webhook` at the end)
   - Test with: `curl https://your-app.railway.app/health`

2. **Check Railway is running:**
   - Railway Dashboard → Check deployment status
   - Should be "Active" or "Deploying"

3. **Check contract address:**
   - In Alchemy webhook config, verify: `0xe0e7f149959c6cac0dDc2Cb4ab27942BFFdA1eb4`
   - Must match exactly (case-insensitive)

4. **Check Alchemy webhook status:**
   - Alchemy Dashboard → Your Webhook
   - Should show "Active" status
   - Check delivery logs for errors

### Sales Not Appearing in Discord

1. **Check Discord channel ID:**
   - Verify `DISCORD_CHANNEL_ID` in Railway variables
   - Should be: `1451431384538153072`

2. **Check bot permissions:**
   - Bot needs: Send Messages, Embed Links, Attach Files

3. **Check Railway logs:**
   - Look for errors when processing sales
   - Common issues: API rate limits, network errors

## Current Status

✅ Webhook endpoint: `/webhook`  
✅ Health check: `/health`  
✅ Event type: `NFT_TRANSFER`  
✅ Contract filter: `0xe0e7f149959c6cac0dDc2Cb4ab27942BFFdA1eb4`  
⚠️ **You need to create the webhook in Alchemy Dashboard!**

