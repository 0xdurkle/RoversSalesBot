# Cloudinary URL Issue - Fix Applied

## Problem Identified

From the logs, the issue was clear:

1. **Cloudinary URLs returning HTTP 400**: The Cloudinary PNG and thumbnail URLs were returning `HTTP 400` errors, meaning they're malformed or incomplete
2. **Embed images using broken URLs**: The embed image was being set to Cloudinary URLs that Discord couldn't fetch
3. **Alchemy CDN URLs work but are large**: The Alchemy CDN URLs (`cachedUrl`) work fine but are >8MB, too large for file attachments

## Root Cause

The code was prioritizing Cloudinary URLs (`pngUrl`, `thumbnailUrl`) over Alchemy CDN URLs (`cachedUrl`) for embed images. However:
- Cloudinary URLs often return HTTP 400 errors
- Alchemy CDN URLs work reliably but are large

## Fix Applied

### 1. Changed Image URL Priority for Embeds
**File**: `sales_fetcher.py`

Changed the priority order in `fetch_nft_images()` to prefer `cachedUrl` (Alchemy CDN) over Cloudinary URLs:

**Before**:
- Priority: `pngUrl` → `thumbnailUrl` → `cachedUrl` → `originalUrl`

**After**:
- Priority: `cachedUrl` → `pngUrl` → `thumbnailUrl` → `originalUrl`

This ensures embed images use working Alchemy CDN URLs instead of broken Cloudinary URLs.

### 2. Skip Cloudinary URLs for File Downloads
**File**: `sales_fetcher.py` - `download_image_with_fallbacks()`

Added logic to skip Cloudinary URLs when downloading images for file attachments, since they consistently return 400 errors.

### 3. Improved Error Handling
**File**: `sales_fetcher.py` - `download_image()`

Improved logging for Cloudinary 400 errors to make debugging easier.

## Expected Behavior After Fix

1. **Embed Images**: Will use Alchemy CDN URLs (`cachedUrl`) which work reliably
   - These URLs may be large (>8MB) but Discord can fetch them for embeds
   - Images should now display in Discord embeds

2. **File Attachments**: Will skip Cloudinary URLs and try Alchemy CDN
   - If Alchemy CDN images are too large (>8MB), file attachment will fail
   - Bot will fall back to embed image only (which should work now)

3. **Logs**: Will show:
   - `Using cached URL for embed (Alchemy CDN): ...` - Good, this works
   - `Skipping Cloudinary URL for file download` - Expected, avoiding 400 errors
   - `Image too large` - Expected for large images, but embed should still work

## Testing

Run `/lastsale` command and check:

1. **Logs should show**:
   ```
   Using cached URL for embed (Alchemy CDN): https://nft-cdn.alchemy.com/...
   ```

2. **Discord embed should display the image** (using Alchemy CDN URL)

3. **File attachment may fail** (if image >8MB) but that's OK - embed image should work

## Files Modified

- `sales_fetcher.py`:
  - `fetch_nft_images()` - Changed priority to prefer `cachedUrl`
  - `download_image_with_fallbacks()` - Skip Cloudinary URLs for downloads
  - `download_image()` - Improved Cloudinary error handling

## Notes

- Alchemy CDN URLs work for Discord embeds even if they're large
- File attachments require images <8MB, so large images won't work as attachments
- This is fine - embed images are the primary method and should now work
- If you need file attachments for large images, you'd need to implement image resizing/compression

