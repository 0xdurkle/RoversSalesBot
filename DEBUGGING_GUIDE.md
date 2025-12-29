# Debugging & Adjusting the Sales Bot

## Quick Adjustments

### 1. Adjust Block Range for `/lastsale` Command

Edit `sales_fetcher.py`, line ~383-384:
```python
block_chunk_size = 5000  # Increase for more blocks per chunk
max_chunks = 5  # Increase to check more chunks (more history)
```

**Example:** To look back 2 weeks instead of ~1 week:
```python
block_chunk_size = 10000
max_chunks = 10  # 10 chunks × 10k blocks = 100k blocks (~2 weeks)
```

### 2. Include Sales with 0 Price (for debugging)

If price detection is failing, you can temporarily see ALL transfers:

Edit `sales_fetcher.py`, line ~468:
```python
# Change from:
if price == 0:
    continue

# To:
if price == 0:
    logger.warning(f"Transfer {candidate['tx_hash'][:10]}... has 0 price - might be a sale with failed price detection")
    # Uncomment next line to include 0-price transfers:
    # price = 1  # Set to 1 wei so it's included
    continue
```

### 3. Increase Logging Verbosity

Edit `bot.py`, line ~24:
```python
# Change from:
logging.basicConfig(
    level=logging.INFO,
    ...
)

# To:
logging.basicConfig(
    level=logging.DEBUG,  # Shows more detailed logs
    ...
)
```

### 4. Adjust Price Detection Timeout

Edit `sales_fetcher.py`, line ~231:
```python
# In _get_transaction_price_simple, you can add timeout:
tx = await asyncio.wait_for(
    self.get_transaction(tx_hash),
    timeout=10.0
)
```

## Debugging Steps

### Step 1: Check What Blocks Are Being Searched

Run the bot and check logs:
```bash
tail -f bot.log | grep -i "Checking blocks\|Found.*transfers"
```

You should see:
```
INFO - Checking blocks 18000000 to 18005000 (chunk 1/5)
INFO - Found 25 transfers in blocks 18000000-18005000
```

### Step 2: Check Price Detection

Look for price detection logs:
```bash
tail -f bot.log | grep -i "Price:\|Skipping transfer"
```

You'll see:
```
DEBUG - Transfer 0x1234... - Price: 1000000000000000 wei (ETH)
DEBUG - Skipping transfer 0x5678... - no price detected
```

### Step 3: Check Final Results

```bash
tail -f bot.log | grep -i "Found.*sales\|Most recent"
```

Should show:
```
INFO - Found 5 unique sales. Most recent block: 18001234, TX: 0xabcd...
INFO - Most recent sale: 0.006000 ETH for token 4540
```

## Common Issues & Fixes

### Issue: "No recent sales found"

**Possible causes:**
1. **Price detection failing** - All transfers have price = 0
2. **Block range too small** - No sales in recent blocks
3. **Contract address wrong** - Not finding transfers for your contract

**Fix:**
1. Check logs to see if transfers are found:
   ```bash
   grep "Found.*transfers" bot.log
   ```
2. If transfers found but no sales, price detection might be failing
3. Increase `max_chunks` to look back further
4. Verify contract address in `.env` matches exactly

### Issue: Showing old sales instead of recent

**Possible causes:**
1. Sorting not working correctly
2. Block numbers incorrect

**Fix:**
1. Check logs for "Most recent block" - verify it's actually recent
2. Check if block numbers are being parsed correctly
3. The chunked approach should fix this, but verify logs

### Issue: Real-time webhooks not working

**Check:**
1. Webhook URL is accessible (test with curl)
2. Alchemy webhook is active in dashboard
3. Contract address in webhook matches exactly
4. Bot logs show webhook receipts:
   ```bash
   grep -i "Received webhook" bot.log
   ```

## Testing Price Detection

To test if price detection works for a specific transaction:

```python
# Add this to test_price.py
import asyncio
from sales_fetcher import SalesFetcher
import os
from dotenv import load_dotenv

load_dotenv()

async def test():
    fetcher = SalesFetcher(
        os.getenv("ALCHEMY_API_KEY"),
        os.getenv("NFT_CONTRACT_ADDRESS")
    )
    
    # Test with a known transaction hash
    tx_hash = "0x..."  # Replace with actual tx hash
    price, is_weth = await fetcher._get_transaction_price_simple(tx_hash)
    print(f"Price: {price} wei, WETH: {is_weth}")
    print(f"Price in ETH: {price / (10**18)}")
    
    await fetcher.close()

asyncio.run(test())
```

## Monitoring

### Watch logs in real-time:
```bash
tail -f bot.log
```

### Filter for important events:
```bash
tail -f bot.log | grep -E "INFO|ERROR|WARNING|Found.*sales|Received webhook"
```

### Check webhook activity:
```bash
tail -f bot.log | grep -i webhook
```

## Performance Tuning

### If `/lastsale` is too slow:

1. **Reduce chunks checked:**
   ```python
   max_chunks = 3  # Check fewer chunks
   ```

2. **Reduce block chunk size:**
   ```python
   block_chunk_size = 3000  # Smaller chunks = faster per chunk
   ```

3. **Limit transfers processed:**
   Add early exit after finding enough:
   ```python
   if len(transfer_candidates) > 100:
       transfer_candidates = transfer_candidates[:100]  # Limit to first 100
   ```

### If webhooks are slow:

- Already optimized (async processing)
- Check network latency
- Consider increasing timeout in price detection

## Getting Help

If issues persist:

1. **Enable DEBUG logging** (see above)
2. **Run `/lastsale` command**
3. **Copy full log output** from that command
4. **Share the logs** - they'll show exactly what's happening

The logs will show:
- Which blocks are checked
- How many transfers found
- Which transfers have prices
- Which sales are returned
- Any errors

