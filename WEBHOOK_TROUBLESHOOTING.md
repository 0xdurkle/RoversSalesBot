# Webhook Not Receiving Events - Troubleshooting Guide

## Problem: Bot Not Receiving Webhook Events

If the bot didn't automatically post a sale, it means **webhook events aren't being received**.

## Quick Diagnostic Checklist

### 1. Check if Webhook Server is Running
Look in logs for:
```
✅ Webhook server started on port 8080
✅ Webhook endpoint: http://0.0.0.0:8080/webhook
```

If you don't see this, the bot isn't running or webhook server didn't start.

### 2. Check if Webhooks are Being Received
Look in logs for:
```
✅ Received webhook from Alchemy: ...
```

If you **don't see this**, Alchemy isn't sending webhooks to your bot.

### 3. Verify Webhook Configuration in Alchemy

1. Go to https://dashboard.alchemy.com/
2. Navigate to **"Notify"** → **"Webhooks"**
3. Check if you have a webhook configured
4. Verify:
   - ✅ Webhook is **Active** (not paused/disabled)
   - ✅ **Event Type**: NFT Transfers (or NFT_ACTIVITY)
   - ✅ **Contract Address**: `0xe0e7f149959c6cac0dDc2Cb4ab27942BFFdA1eb4`
   - ✅ **URL**: Points to your webhook endpoint

### 4. Check Webhook URL

**If running locally:**
- You need ngrok or similar tunnel
- Webhook URL should be: `https://your-ngrok-url.ngrok.io/webhook`
- Test: `curl https://your-ngrok-url.ngrok.io/webhook-test`

**If running on Railway:**
- Webhook URL should be: `https://your-app.railway.app/webhook`
- Test: `curl https://your-app.railway.app/webhook-test`

**If running on other platform:**
- Webhook URL should be: `https://your-domain.com/webhook`
- Must be publicly accessible (not localhost)

### 5. Check Alchemy Webhook Delivery Status

1. Go to Alchemy Dashboard → Your Webhook
2. Check **"Delivery Status"** tab
3. Look for:
   - Recent delivery attempts
   - Error messages
   - Response codes (should be 200)

**Common errors:**
- **404**: Webhook URL is wrong or endpoint doesn't exist
- **401**: Authentication failed (if WEBHOOK_SECRET is set)
- **500**: Server error (check bot logs)
- **Timeout**: Server not responding (bot might be down)

## Step-by-Step Fix

### Step 1: Verify Bot is Running
```bash
# Check if bot process is running
ps aux | grep bot.py

# Check logs for webhook server startup
tail -f bot.log | grep -i webhook
```

### Step 2: Test Webhook Endpoint

**Local:**
```bash
# If using ngrok
curl https://your-ngrok-url.ngrok.io/webhook-test

# Should return: "Webhook endpoint is accessible!"
```

**Railway:**
```bash
curl https://your-app.railway.app/webhook-test

# Should return: "Webhook endpoint is accessible!"
```

### Step 3: Check Alchemy Webhook Configuration

1. **Verify webhook exists:**
   - Go to Alchemy Dashboard → Notify → Webhooks
   - You should see your webhook listed

2. **Check webhook is active:**
   - Status should be "Active" (green)
   - If paused/disabled, click to activate

3. **Verify contract address:**
   - Should be: `0xe0e7f149959c6cac0dDc2Cb4ab27942BFFdA1eb4`
   - Must match exactly (case-insensitive)

4. **Verify webhook URL:**
   - Should end with `/webhook`
   - Must be publicly accessible
   - Test the URL in browser or with curl

### Step 4: Test Webhook Manually

You can test if the webhook endpoint works by sending a test request:

```bash
curl -X POST https://your-webhook-url.com/webhook \
  -H "Content-Type: application/json" \
  -d '{"webhookId": "test", "type": "NFT_ACTIVITY"}'
```

Check bot logs - you should see:
```
✅ Received webhook from Alchemy: test
```

### Step 5: Check Recent Sales

1. **Verify a sale actually happened:**
   - Check Etherscan for recent transactions
   - Verify it's for your contract: `0xe0e7f149959c6cac0dDc2Cb4ab27942BFFdA1eb4`

2. **Check Alchemy webhook delivery:**
   - Go to Alchemy Dashboard → Your Webhook → Delivery Status
   - Look for delivery attempts around the time of the sale
   - Check if they succeeded (200) or failed

## Common Issues & Solutions

### Issue: Webhook URL is localhost
**Problem**: Alchemy can't reach `http://localhost:8080/webhook`

**Solution**: 
- Use ngrok for local testing: `ngrok http 8080`
- Or deploy to Railway/Heroku for production

### Issue: Webhook URL is wrong
**Problem**: URL doesn't end with `/webhook` or points to wrong server

**Solution**:
- Verify URL in Alchemy Dashboard
- Must be: `https://your-domain.com/webhook`
- Test with curl to verify it's accessible

### Issue: Webhook is paused/disabled
**Problem**: Webhook exists but is not active

**Solution**:
- Go to Alchemy Dashboard → Your Webhook
- Click to activate/enable the webhook

### Issue: Contract address mismatch
**Problem**: Webhook is configured for different contract

**Solution**:
- Verify contract address in Alchemy webhook config
- Should be: `0xe0e7f149959c6cac0dDc2Cb4ab27942BFFdA1eb4`

### Issue: Bot is running locally but webhook points to Railway
**Problem**: Webhook URL points to production but bot is running locally

**Solution**:
- Either run bot on Railway, OR
- Update Alchemy webhook to point to ngrok URL

## Next Steps

1. **Check bot logs** for webhook activity
2. **Verify Alchemy webhook** is configured and active
3. **Test webhook endpoint** with curl
4. **Check Alchemy delivery status** for errors
5. **Verify a sale actually happened** on Etherscan

If webhooks still aren't working after checking all of the above, the issue might be:
- Alchemy webhook service issue (check Alchemy status page)
- Network/firewall blocking webhook requests
- Bot server not accessible from internet

