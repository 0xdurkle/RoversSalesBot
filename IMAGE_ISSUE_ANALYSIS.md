# Image Display Issue Analysis

## Current Implementation

The bot uses **two methods** to display images in Discord:

### Method 1: Embed Image URL (Primary)
- Location: `bot.py:162` - `embed.set_image(url=image_url)`
- Uses the first image URL from `fetch_nft_images()`
- Set in `create_sale_embed()` function

### Method 2: File Attachment (Fallback/Enhancement)
- Location: `bot.py:321-346` (webhook sales) and `bot.py:579-605` (`/lastsale` command)
- Downloads image using `download_image_with_fallbacks()`
- Attaches as `discord.File()` if download succeeds
- More reliable than embed images, but requires successful download

## Image Fetching Flow

1. **`fetch_nft_images()`** (`sales_fetcher.py:283-522`)
   - Fetches NFT metadata from Alchemy API
   - Extracts image URLs from multiple sources:
     - `media[0].gateway` (can be string or dict)
     - `media[0].raw` (can be string or dict)
     - `metadata.image` (can be string or dict)
     - Top-level `image` field (can be string or dict)
   - Handles IPFS URLs (converts to Cloudflare gateway)
   - Returns list of image URLs

2. **`download_image_with_fallbacks()`** (`sales_fetcher.py:1026-1089`)
   - Gets all available URLs via `get_all_image_urls_for_token()`
   - Tries each URL in priority order
   - Downloads image data as bytes
   - Returns image bytes or None

3. **`get_all_image_urls_for_token()`** (`sales_fetcher.py:812-939`)
   - First tries IPFS direct fetching (most reliable)
   - Then gets Alchemy metadata URLs
   - Prioritizes: thumbnailUrl > pngUrl > cachedUrl > originalUrl
   - Returns URLs in priority order

## Potential Issues

### Issue 1: Embed Image URL Not Working
**Problem**: Discord may not display embed images if:
- URL is too long (>2000 chars - code handles this)
- URL is not accessible by Discord's servers
- URL requires authentication or special headers
- URL is a video file instead of image
- URL has invalid format

**Evidence**: Code sets embed image at line 162, but Discord might silently fail to load it.

### Issue 2: File Download Failing Silently
**Problem**: The file attachment code catches exceptions but only logs warnings:
- Line 346: `logger.warning(f"Failed to download image for webhook sale: {e}")`
- Line 605: `logger.warning(f"Failed to download image: {e}")`

If download fails, it falls back to embed image only, which might also fail.

### Issue 3: Image URL Validation
**Problem**: Code validates URLs start with `http://` or `https://`, but doesn't check:
- If URL is actually accessible
- If URL returns valid image content
- If URL is a video file (code tries to detect this but might miss some)

### Issue 4: Discord File Size Limits
**Problem**: Discord has file size limits:
- Free servers: 8MB
- Boosted servers: 50MB
- Code limits to 8MB (line 982), but might still fail if server has lower limits

### Issue 5: Image URL Source Priority
**Problem**: The code prioritizes certain URL types, but:
- PNG/thumbnail URLs might not exist for all NFTs
- Fallback URLs might be videos instead of images
- IPFS URLs might be slow or inaccessible

## Code Flow for Webhook Sales

1. `process_webhook_events_grouped()` (line 215)
2. Calls `fetch_nft_images()` to get URLs (line 315)
3. Creates embed with `create_sale_embed()` which sets embed image (line 319)
4. Tries to download image for file attachment (line 326)
5. Sends message with both embed and file (if file exists) (line 363)

## Code Flow for `/lastsale` Command

1. `lastsale()` command handler (line 541)
2. Fetches last sale
3. Calls `fetch_nft_images()` to get URLs (line 572)
4. Creates embed with `create_sale_embed()` (line 576)
5. Tries to download image for file attachment (line 584)
6. Sends message with both embed and file (if file exists) (line 609)

## Recommendations

1. **Add more logging** to track:
   - Which image URLs are being used
   - Whether embed image URL is valid
   - Whether file download succeeds or fails
   - Discord API responses when sending messages

2. **Validate image URLs** before using them:
   - Check if URL is accessible
   - Verify content type is image (not video)
   - Test URL with HEAD request before using

3. **Improve error handling**:
   - Don't silently fail - log all failures
   - Try multiple image sources if first fails
   - Provide better fallback behavior

4. **Test with actual sales**:
   - Check logs when a sale happens
   - Verify image URLs are being fetched
   - Check if Discord is receiving the images

## Next Steps

1. Check recent logs for image-related messages
2. Test with `/lastsale` command to see what happens
3. Add more detailed logging around image handling
4. Verify image URLs are valid and accessible
5. Check Discord API responses for image-related errors

