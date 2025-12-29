# Image Display Debugging Guide

## Current Status

Images still not showing even after:
- ✅ Permissions updated (Embed Links checked)
- ✅ Code optimized to prefer Alchemy CDN URLs
- ✅ Enhanced logging added

## What to Do Next

### Step 1: Run `/lastsale` Command

Run the `/lastsale` command in Discord and check the bot logs. Look for these messages:

```
🔍 URL Selection - cachedUrl: ...
🔍 URL Selection - pngUrl: ...
🔍 URL Selection - thumbnailUrl: ...
✅ SELECTED: cached URL from top-level image (Alchemy CDN): ...
🔍 FULL IMAGE URL (before setting): ...
🔍 FULL EMBED IMAGE URL (copy this and test in browser):
🔍 https://...
```

### Step 2: Check Which URL is Being Used

**Good (should work):**
- `✅ SELECTED: cached URL from top-level image (Alchemy CDN): https://nft-cdn.alchemy.com/...`
- This means it's using the Alchemy CDN URL which should work

**Bad (won't work):**
- `⚠️ SELECTED: PNG URL from top-level image (Cloudinary - may return 400): https://res.cloudinary.com/...`
- This means `cachedUrl` was None/empty, so it fell back to Cloudinary (which fails)

### Step 3: Test the URL Manually

1. **Copy the FULL URL from logs** (the one after `🔍 FULL EMBED IMAGE URL`)
2. **Open it in your browser**
3. **Check what happens:**
   - ✅ If image loads: URL works, issue is with Discord
   - ❌ If 400/404 error: URL is broken, need different URL
   - ❌ If timeout: URL is not accessible

### Step 4: Check Discord Embed Limits

Discord has limits on embed images:
- **Max file size**: 8MB (but embeds can be larger)
- **Supported formats**: JPG, PNG, GIF, WebP
- **URL must be publicly accessible**: No authentication required
- **URL must return image**: Not HTML, JSON, or error page

## Common Issues

### Issue 1: Using Cloudinary URL (Returns 400)
**Symptom**: Logs show `⚠️ SELECTED: PNG URL from top-level image (Cloudinary)`

**Why**: `cachedUrl` is None or empty, so code falls back to Cloudinary

**Fix**: Need to check why `cachedUrl` is not available. Check logs for:
```
Available URLs - cachedUrl: False
```

If `cachedUrl` is False, the Alchemy API isn't returning it for this NFT.

### Issue 2: URL Works in Browser but Not Discord
**Symptom**: URL loads in browser but image doesn't show in Discord

**Possible causes**:
1. **Discord CDN can't access the URL** (CORS, authentication, etc.)
2. **Image is too large** (though embeds should handle this)
3. **Content-Type is wrong** (Discord expects image/*)
4. **URL requires special headers** (Discord doesn't send custom headers)

**Fix**: Try using a different image URL or hosting the image elsewhere

### Issue 3: No Image URLs Found
**Symptom**: Logs show `No images available for embed`

**Why**: NFT metadata doesn't have image URLs

**Fix**: This NFT might not have image metadata. Check on OpenSea or Etherscan.

## Next Steps Based on Logs

### If logs show Cloudinary URL:
1. Check why `cachedUrl` is None
2. Look for: `Available URLs - cachedUrl: False`
3. The NFT might not have Alchemy CDN URL available
4. May need to use IPFS or other source

### If logs show Alchemy CDN URL:
1. Copy the full URL from logs
2. Test it in browser
3. If it works in browser but not Discord:
   - Check Discord embed limits
   - Try a different image URL source
   - Check if URL requires authentication

### If no URLs found:
1. Check NFT metadata on OpenSea
2. Verify token ID is correct
3. NFT might not have image metadata

## Testing the URL

After running `/lastsale`, you'll see a log line like:
```
🔍 FULL EMBED IMAGE URL (copy this and test in browser):
🔍 https://nft-cdn.alchemy.com/eth-mainnet/9fc4fb9f25924441b12e97b2e57ceb24
```

**Test it:**
```bash
# In terminal
curl -I "https://nft-cdn.alchemy.com/eth-mainnet/9fc4fb9f25924441b12e97b2e57ceb24"

# Should return:
# HTTP/1.1 200 OK
# Content-Type: image/...
```

Or just open it in your browser - if it shows an image, the URL works.

## What to Share

If images still don't show, share:
1. **The full URL from logs** (after `🔍 FULL EMBED IMAGE URL`)
2. **What happens when you open it in browser** (works? error?)
3. **Which URL type was selected** (Alchemy CDN? Cloudinary?)
4. **Screenshot of Discord message** (showing the embed without image)

