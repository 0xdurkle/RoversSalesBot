"""
NFT Sales Discord Bot - Main entry point.
Monitors NFT sales via Alchemy webhooks and posts to Discord.
"""
import asyncio
import logging
import os
import ssl
from collections import defaultdict
from decimal import Decimal
from typing import Dict, List, Optional

import aiohttp
from aiohttp import web
import certifi
import ssl

# Patch aiohttp.TCPConnector to use certifi by default
_original_tcp_connector_init = aiohttp.TCPConnector.__init__

def _new_tcp_connector_init(self, *args, **kwargs):
    # If ssl is True or not specified, use certifi
    if 'ssl' not in kwargs:
        kwargs['ssl'] = ssl.create_default_context(cafile=certifi.where())
    elif kwargs.get('ssl') is True:
        kwargs['ssl'] = ssl.create_default_context(cafile=certifi.where())
    return _original_tcp_connector_init(self, *args, **kwargs)

aiohttp.TCPConnector.__init__ = _new_tcp_connector_init

import discord
from discord import app_commands
from dotenv import load_dotenv

from sales_fetcher import SalesFetcher, SaleEvent
import io

# Load environment variables
load_dotenv()

# Configure SSL certificates before importing discord
import certifi
import ssl

# Set default SSL context for asyncio
# Note: For production, ensure proper SSL certificates are installed
# This is a workaround for macOS SSL certificate issues
try:
    ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())
except Exception:
    # Fallback: disable SSL verification (NOT RECOMMENDED FOR PRODUCTION)
    # This is only for local testing when certificates aren't properly configured
    import warnings
    warnings.warn("SSL verification disabled - not recommended for production")
    ssl._create_default_https_context = ssl._create_unverified_context

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,  # Changed to DEBUG to see more details
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
NFT_CONTRACT_ADDRESS = os.getenv("NFT_CONTRACT_ADDRESS", "").lower()
ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY")
# Railway provides PORT env var, use that if available, otherwise default to 8080
WEBHOOK_PORT = int(os.getenv("PORT") or os.getenv("WEBHOOK_PORT", "8080"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# Discord client setup
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# Global state
sales_fetcher: Optional[SalesFetcher] = None
discord_channel: Optional[discord.TextChannel] = None
processed_sales: set = set()  # Track processed transaction hashes
webhook_events: Dict[str, List[dict]] = defaultdict(list)  # Group events by tx_hash


def format_price(price_wei: int, is_weth: bool) -> str:
    """
    Format price with max 4 decimals, remove trailing zeros.
    
    Args:
        price_wei: Price in wei
        is_weth: True if WETH, False if ETH
        
    Returns:
        Formatted price string (e.g., "0.0062 WETH", "1 ETH")
    """
    if price_wei == 0:
        return "0 ETH"
    
    # Convert wei to ETH
    eth_value = Decimal(price_wei) / Decimal(10**18)
    
    # Round to 4 decimal places
    eth_value = round(eth_value, 4)
    
    # Remove trailing zeros
    price_str = f"{eth_value:.4f}".rstrip('0').rstrip('.')
    
    currency = "WETH" if is_weth else "ETH"
    return f"{price_str} {currency}"


def get_sweep_category(token_count: int) -> tuple[str, int]:
    """
    Get sweep category and color.
    
    Args:
        token_count: Number of NFTs in sale
        
    Returns:
        Tuple of (category_name, color_code)
    """
    if token_count == 1:
        return ("Single NFT Sale", 0x3498db)  # Blue
    elif token_count <= 5:
        return ("Mini Sweep", 0x2ecc71)  # Green
    elif token_count <= 10:
        return ("Big Sweep", 0xe67e22)  # Orange
    else:
        return ("Huge Sweep", 0xe74c3c)  # Red


def create_sale_embed(sale: SaleEvent, image_urls: List[str]) -> discord.Embed:
    """
    Create Discord embed for sale notification.
    
    Args:
        sale: SaleEvent object
        image_urls: List of NFT image URLs
        
    Returns:
        Discord embed
    """
    category, color = get_sweep_category(sale.token_count)
    price_str = format_price(sale.total_price, sale.is_weth)
    
    embed = discord.Embed(
        title=category,
        description=price_str,
        color=color,
        timestamp=sale.timestamp if sale.timestamp else discord.utils.utcnow()
    )
    
    # Add first image if available
    if image_urls and len(image_urls) > 0:
        try:
            image_url = image_urls[0]
            logger.info(f"🔍 Processing image URL: {image_url[:100] if image_url else 'None'}...")
            
            # Ensure URL is complete and valid
            if not image_url or not isinstance(image_url, str):
                logger.error(f"✗ Invalid image URL: {type(image_url)} - {image_url}")
            elif not image_url.startswith(("http://", "https://")):
                logger.warning(f"✗ Invalid image URL format (doesn't start with http/https): {image_url[:100]}...")
            else:
                # Validate URL length (Discord has limits)
                if len(image_url) > 2000:
                    logger.warning(f"⚠ Image URL too long ({len(image_url)} chars), truncating to 2000")
                    image_url = image_url[:2000]
                
                # Clean URL - remove any trailing issues
                image_url = image_url.strip()
                
                # Quick validation: Check if URL looks valid
                if not image_url.startswith(("http://", "https://")):
                    logger.error(f"✗ Invalid image URL format after cleaning: {image_url[:100]}...")
                elif "cloudinary.com" in image_url:
                    logger.warning(f"⚠ Using Cloudinary URL (Discord can't fetch these): {image_url[:100]}...")
                    logger.warning("⚠ Discord CANNOT fetch Cloudinary URLs - image will be attached as file instead")
                    # Don't set embed image for Cloudinary - we'll attach as file
                    # Setting it here would just cause Discord to fail silently
                    logger.info("⚠ SKIPPING embed.set_image() for Cloudinary URL - will use file attachment only")
                    # DO NOT call embed.set_image() for Cloudinary URLs - skip it entirely
                else:
                    # Only set embed image for non-Cloudinary URLs
                    if "nft-cdn.alchemy.com" in image_url:
                        logger.info(f"✓ Using Alchemy CDN URL (should work): {image_url[:100]}...")
                    else:
                        logger.info(f"ℹ Using other URL source: {image_url[:100]}...")
                    
                    # Log the FULL URL before setting (for debugging)
                    logger.info(f"🔍 FULL IMAGE URL (before setting): {image_url}")
                    logger.info(f"🔍 URL length: {len(image_url)} chars")
                    
                    # Set embed image (only for non-Cloudinary URLs)
                    try:
                        embed.set_image(url=image_url)
                        logger.info(f"✓ Called embed.set_image() with URL: {image_url[:100]}...")
                    except Exception as set_error:
                        logger.error(f"✗ Error calling embed.set_image(): {set_error}", exc_info=True)
                        raise
                    
                    # Verify it was set correctly
                    if embed.image:
                        if embed.image.url:
                            logger.info(f"✓ Embed image verified: {embed.image.url[:100]}...")
                            logger.info(f"🔍 FULL EMBED IMAGE URL (copy this and test in browser):")
                            logger.info(f"🔍 {embed.image.url}")
                            logger.info(f"✓ Discord should fetch this URL when displaying the embed")
                            logger.info(f"💡 TIP: Copy the URL above and open it in your browser to verify it works")
                        else:
                            logger.error("✗ embed.image exists but embed.image.url is None")
                    else:
                        logger.error("✗ embed.image is None after calling set_image()")
                        logger.error(f"✗ Attempted URL was: {image_url[:200]}")
        except Exception as e:
            logger.error(f"✗ Error setting embed image: {e}", exc_info=True)
            logger.error(f"✗ Image URL that failed: {image_urls[0][:200] if image_urls and len(image_urls) > 0 else 'No URLs'}")
    else:
        logger.warning(f"⚠ No images available for embed (image_urls: {image_urls})")
        logger.warning(f"⚠ This means fetch_nft_images() returned empty list or None")
    
    # Add transaction link
    tx_url = f"https://etherscan.io/tx/{sale.tx_hash}"
    embed.add_field(
        name="Transaction",
        value=f"[View on Etherscan]({tx_url})",
        inline=False
    )
    
    # Add token IDs if multiple
    if sale.token_count > 1 and sale.token_ids:
        # Limit display to first 10 token IDs
        display_ids = sale.token_ids[:10]
        token_str = ", ".join(display_ids)
        if len(sale.token_ids) > 10:
            token_str += f" (+{len(sale.token_ids) - 10} more)"
        embed.add_field(
            name=f"Token IDs ({sale.token_count} NFTs)",
            value=token_str[:1024],  # Discord field limit
            inline=False
        )
    elif sale.token_id:
        embed.add_field(
            name="Token ID",
            value=sale.token_id,
            inline=True
        )
    
    # Add additional images as links if multiple
    if len(image_urls) > 1:
        image_links = []
        for i, url in enumerate(image_urls[1:6], 1):  # Limit to 5 additional images
            image_links.append(f"[Image {i}]({url})")
        if image_links:
            embed.add_field(
                name="Additional Images",
                value=" | ".join(image_links),
                inline=False
            )
    
    embed.set_footer(text="NFT Sales Monitor")
    
    # Final embed status log
    if embed.image and embed.image.url:
        logger.info(f"✅ EMBED CREATED SUCCESSFULLY with image URL: {embed.image.url[:100]}...")
    else:
        logger.error(f"❌ EMBED CREATED BUT NO IMAGE - embed.image: {embed.image}")
        logger.error(f"❌ image_urls passed to function: {len(image_urls) if image_urls else 0} URL(s)")
        if image_urls:
            logger.error(f"❌ First image_url was: {image_urls[0][:200] if image_urls[0] else 'None'}")
    
    return embed


async def process_webhook_events_grouped(tx_hash: str, events: List[dict]):
    """
    Process grouped webhook events for a transaction.
    Handles both single sales and sweeps.
    
    Args:
        tx_hash: Transaction hash
        events: List of webhook event dictionaries
    """
    try:
        # Check if already processed
        if tx_hash.lower() in processed_sales:
            logger.debug(f"Sale {tx_hash} already processed, skipping")
            return
        
        # Filter events for our contract
        contract_events = []
        for e in events:
            # Contract address can be in log.address or contractAddress
            log_data = e.get("log", {})
            contract_addr = log_data.get("address", "").lower() or e.get("contractAddress", "").lower()
            if contract_addr == NFT_CONTRACT_ADDRESS:
                contract_events.append(e)
        
        if not contract_events:
            logger.debug(f"No events for our contract in {tx_hash}")
            return
        
        # Extract token IDs and addresses
        token_ids = []
        buyers = set()
        sellers = set()
        
        for event in contract_events:
            from_addr = event.get("fromAddress", "").lower()
            to_addr = event.get("toAddress", "").lower()
            
            # Skip mints and burns
            if from_addr == "0x0000000000000000000000000000000000000000":
                continue
            if to_addr == "0x0000000000000000000000000000000000000000":
                continue
            
            # Extract token ID - can be in different places depending on token standard
            token_id = None
            event_data = event.get("event", {})
            
            # Check ERC-721 metadata
            erc721_meta = event_data.get("erc721Metadata")
            if erc721_meta:
                token_id = erc721_meta.get("tokenId", "")
            
            # Check ERC-1155 metadata
            if not token_id:
                erc1155_meta = event_data.get("erc1155Metadata", [])
                if erc1155_meta and len(erc1155_meta) > 0:
                    token_id = erc1155_meta[0].get("tokenId", "")
            
            # Fallback to top-level tokenId
            if not token_id:
                token_id = event.get("tokenId", "")
            
            if token_id:
                # Convert hex to decimal string if needed
                if isinstance(token_id, str) and token_id.startswith("0x"):
                    try:
                        token_id = str(int(token_id, 16))
                    except ValueError:
                        # If it's a very long hex string (ERC-1155), try to extract the numeric part
                        # ERC-1155 tokenIds can be complex, so we'll use the hex string as-is if conversion fails
                        logger.debug(f"Could not convert tokenId {token_id} to decimal, using as-is")
                elif not isinstance(token_id, str):
                    token_id = str(token_id)
                
                token_ids.append(token_id)
            
            buyers.add(to_addr)
            sellers.add(from_addr)
        
        if not token_ids:
            logger.debug(f"No valid token IDs in {tx_hash}")
            return
        
        # Get price
        price, is_weth = await sales_fetcher._get_transaction_price_simple(tx_hash)
        
        # Create sale event
        sale = SaleEvent(
            tx_hash=tx_hash,
            buyer=list(buyers)[0] if buyers else "",
            seller=list(sellers)[0] if sellers else "",
            token_id=token_ids[0] if len(token_ids) == 1 else None,
            token_ids=token_ids if len(token_ids) > 1 else None,
            token_count=len(token_ids),
            total_price=price,
            timestamp=None,  # Could parse from event if needed
            is_weth=is_weth
        )
        
        # Fetch images (limit to 20) - this gets the embed image URL
        image_urls = await sales_fetcher.fetch_nft_images(token_ids, max_images=20)
        logger.info(f"📸 Fetched {len(image_urls)} image(s) for webhook sale")
        if image_urls:
            logger.info(f"📸 First image URL: {image_urls[0][:150]}...")
        else:
            logger.error(f"❌ NO IMAGE URLS RETURNED for token IDs: {token_ids}")
            logger.error(f"❌ This means fetch_nft_images() returned empty list - check Alchemy API responses")
        
        # Create embed with the image URL
        embed = create_sale_embed(sale, image_urls)
        
        # Download image for file attachment - REQUIRED for Cloudinary URLs
        # Discord can't fetch Cloudinary URLs, so we MUST download and attach as file
        file = None
        image_data = None
        is_cloudinary = False
        if token_ids and len(token_ids) > 0 and image_urls:
            embed_url = image_urls[0]
            is_cloudinary = 'cloudinary.com' in embed_url
            
            if is_cloudinary:
                    logger.info(f"📥 MUST download Cloudinary image (Discord can't fetch these URLs): {embed_url[:80]}...")
                    # Try multiple Cloudinary URL variations if first fails
                    urls_to_try = [embed_url]
                    
                    # Also try to get pngUrl from metadata if available (different Cloudinary URL)
                    try:
                        if token_ids and len(token_ids) > 0:
                            metadata = await sales_fetcher.get_nft_metadata(token_ids[0])
                            if metadata:
                                top_image = metadata.get("image", {})
                                if isinstance(top_image, dict):
                                    png_url = top_image.get("pngUrl")
                                    if png_url and isinstance(png_url, str) and 'cloudinary.com' in png_url:
                                        if png_url not in urls_to_try:
                                            urls_to_try.append(png_url)
                                            logger.info(f"📥 Added pngUrl from metadata to try list: {png_url[:80]}...")
                    except Exception as e:
                        logger.debug(f"Could not fetch pngUrl from metadata: {e}")
                    
                    # Try thumbnail URL if we have it
                    if len(image_urls) > 1:
                        if image_urls[1] not in urls_to_try:
                            urls_to_try.append(image_urls[1])
                    
                    # Try constructing alternative Cloudinary URLs
                    if '/f_png' in embed_url:
                        # Try without f_png transformation
                        alt_url = embed_url.replace('/f_png,so_0/', '/').replace('/f_png/', '/')
                        if alt_url != embed_url and alt_url not in urls_to_try:
                            urls_to_try.append(alt_url)
            else:
                logger.info(f"📥 Attempting to download image for file attachment: {embed_url[:80]}...")
                urls_to_try = [embed_url]
            
            # Try downloading from multiple URLs
            for attempt, url_to_try in enumerate(urls_to_try, 1):
                try:
                    logger.info(f"📥 Attempt {attempt}/{len(urls_to_try)}: Downloading from {url_to_try[:80]}...")
                    # Download the image - CRITICAL for Cloudinary URLs
                    image_data = await sales_fetcher.download_image(url_to_try)
                    if image_data:
                        # Determine file extension from URL or content type
                        ext = "png"  # Default
                        url_lower = url_to_try.lower()
                        if ".jpg" in url_lower or ".jpeg" in url_lower:
                            ext = "jpg"
                        elif ".gif" in url_lower:
                            ext = "gif"
                        elif ".webp" in url_lower:
                            ext = "webp"
                        elif ".png" in url_lower or "f_png" in url_lower:
                            ext = "png"
                        
                        file = discord.File(
                            io.BytesIO(image_data),
                            filename=f"nft_{sale.token_id or 'image'}.{ext}"
                        )
                        logger.info(f"✅ Successfully downloaded image: {len(image_data)} bytes, extension: {ext}")
                        if is_cloudinary:
                            logger.info(f"✅ Cloudinary image downloaded - will attach as file (Discord can't fetch Cloudinary URLs)")
                        break  # Success - stop trying other URLs
                    else:
                        logger.warning(f"⚠️ Attempt {attempt} failed: No image data returned from {url_to_try[:80]}...")
                        if attempt < len(urls_to_try):
                            logger.info(f"🔄 Trying next URL...")
                except Exception as e:
                    logger.warning(f"⚠️ Attempt {attempt} failed: {e}")
                    if attempt < len(urls_to_try):
                        logger.info(f"🔄 Trying next URL...")
                    continue
            
            # If all Cloudinary URLs failed, extract frame from video (MOST RELIABLE)
            if not image_data and is_cloudinary and token_ids:
                logger.warning(f"⚠️ All Cloudinary URLs failed, extracting frame from video...")
                try:
                    # Get video URL from metadata
                    metadata = await sales_fetcher.get_nft_metadata(token_ids[0])
                    if metadata:
                        top_image = metadata.get("image", {})
                        if isinstance(top_image, dict):
                            original_url = top_image.get("originalUrl", "")
                            if original_url and '/ipfs/' in original_url and ('.mp4' in original_url.lower() or 'video' in original_url.lower()):
                                logger.info(f"🎬 Found video URL: {original_url[:80]}...")
                                # Extract frame from video
                                frame_data = await sales_fetcher.extract_video_frame(original_url, token_ids[0])
                                if frame_data:
                                    file = discord.File(
                                        io.BytesIO(frame_data),
                                        filename=f"nft_{sale.token_id or 'image'}.png"
                                    )
                                    image_data = frame_data
                                    logger.info(f"✅ Successfully extracted frame from video: {len(frame_data)} bytes")
                                else:
                                    logger.warning(f"⚠️ Frame extraction failed")
                            else:
                                logger.warning(f"⚠️ No video URL found in metadata")
                        else:
                            logger.warning(f"⚠️ Image metadata is not a dict")
                    else:
                        logger.warning(f"⚠️ Could not fetch metadata for video extraction")
                except Exception as video_error:
                    logger.warning(f"⚠️ Video frame extraction failed: {video_error}")
            
            if not image_data:
                if is_cloudinary:
                    logger.error(f"❌ CRITICAL: All image download attempts failed - Discord won't be able to display image!")
                    logger.error(f"❌ Tried {len(urls_to_try)} Cloudinary URL(s) and IPFS fallback")
                else:
                    logger.debug(f"Could not download image from embed URL (may be too large)")
        
        # Get channel if not already set (in case it wasn't found at startup)
        global discord_channel
        if not discord_channel:
            discord_channel = client.get_channel(DISCORD_CHANNEL_ID)
            if not discord_channel:
                # Try fetching from all guilds
                for guild in client.guilds:
                    channel = guild.get_channel(DISCORD_CHANNEL_ID)
                    if channel:
                        discord_channel = channel
                        logger.info(f"Found channel in guild: {guild.name}")
                        break
        
        if discord_channel:
            try:
                # Log image information for debugging
                if image_urls:
                    logger.info(f"Using embed image URL: {image_urls[0][:100]}... (length: {len(image_urls[0])})")
                else:
                    logger.warning(f"No image URLs available for token ID(s): {token_ids}")
                
                # Verify file is valid before sending
                if file:
                    if image_data and len(image_data) > 0:
                        logger.info(f"📎 Sending message with embed + file attachment ({len(image_data)} bytes)")
                        logger.info(f"📎 File object: {file.filename}, size: {len(image_data)} bytes")
                        try:
                            message = await discord_channel.send(embed=embed, file=file)
                            logger.info(f"✅ Posted sale with image attachment - Message ID: {message.id}")
                            # Verify the message was sent with attachment
                            if message.attachments:
                                logger.info(f"✅ Message has {len(message.attachments)} attachment(s) - image should be visible!")
                                for att in message.attachments:
                                    logger.info(f"✅ Attachment: {att.filename}, size: {att.size} bytes, URL: {att.url[:80]}...")
                            else:
                                logger.warning(f"⚠️ Message sent but has no attachments - file may not have been attached!")
                        except discord.HTTPException as e:
                            logger.error(f"❌ Discord API error sending message with file: {e.status} - {e.text}")
                            if e.status == 413:
                                logger.error(f"❌ File too large ({len(image_data)} bytes) - Discord limit is 8MB")
                            # Try sending without file as fallback
                            logger.warning(f"⚠️ Attempting to send message without file attachment...")
                            message = await discord_channel.send(embed=embed)
                            logger.info(f"✅ Posted sale without image - Message ID: {message.id}")
                    else:
                        logger.error(f"❌ File object exists but image_data is empty or None!")
                        file = None  # Don't send invalid file
                else:
                    if image_urls:
                        is_cloudinary = 'cloudinary.com' in image_urls[0]
                        if is_cloudinary:
                            logger.error(f"❌ CRITICAL: Cloudinary image download failed - Discord won't be able to display image!")
                            logger.error(f"❌ Image URL: {image_urls[0][:100]}...")
                        else:
                            logger.info(f"📤 Sending message with embed image URL only (file download failed or not attempted)")
                    else:
                        logger.warning(f"⚠️ Sending message without image - no image URLs found for token ID(s): {token_ids}")
                    message = await discord_channel.send(embed=embed)
                    logger.info(f"✅ Posted sale - Message ID: {message.id}")
                
                # Log embed image status
                if embed.image:
                    logger.info(f"Embed image URL set: {embed.image.url[:100] if embed.image.url else 'None'}...")
                else:
                    logger.warning("Embed image URL was NOT set - check image URL fetching")
                
                logger.info(
                    f"Posted sale: {sale.token_count} NFT(s) for {format_price(price, is_weth)} "
                    f"in tx {tx_hash}"
                )
            except discord.Forbidden:
                logger.error(f"Bot doesn't have permission to send messages in channel {DISCORD_CHANNEL_ID}")
            except discord.NotFound:
                logger.error(f"Channel {DISCORD_CHANNEL_ID} not found - bot may not be in the server")
            except discord.HTTPException as e:
                logger.error(f"Discord API error posting message: {e.status} - {e.text}")
                if e.status == 400:
                    logger.error("Bad request - check embed/image URL format")
                elif e.status == 413:
                    logger.error("File too large - image exceeds Discord size limit")
            except Exception as e:
                logger.error(f"Error posting to Discord: {e}", exc_info=True)
        else:
            logger.error(f"Discord channel {DISCORD_CHANNEL_ID} not available - check bot is in server and has access")
        
        # Mark as processed
        processed_sales.add(tx_hash.lower())
        
    except Exception as e:
        logger.error(f"Error processing sale {tx_hash}: {e}", exc_info=True)


async def process_webhook_sale_with_timeout(tx_hash: str, event: dict):
    """
    Process a webhook sale with grouping timeout.
    Waits 2 seconds to group multiple events for the same transaction.
    
    Args:
        tx_hash: Transaction hash
        event: Webhook event dictionary
    """
    try:
        # Add event to grouping dict
        webhook_events[tx_hash.lower()].append(event)
        
        # Wait 2 seconds for all events to arrive
        await asyncio.sleep(2)
        
        # Process all events for this transaction
        events = webhook_events.pop(tx_hash.lower(), [])
        if events:
            await asyncio.wait_for(
                process_webhook_events_grouped(tx_hash, events),
                timeout=60.0
            )
    except asyncio.TimeoutError:
        logger.error(f"Timeout processing sale {tx_hash}")
    except Exception as e:
        logger.error(f"Error in process_webhook_sale_with_timeout: {e}", exc_info=True)


async def handle_alchemy_webhook(request: web.Request) -> web.Response:
    """
    Handle incoming Alchemy webhook.
    CRITICAL: Always returns 200 OK (except auth failures).
    
    Args:
        request: aiohttp request object
        
    Returns:
        HTTP response
    """
    # Log that we received a request (even if it's not a valid webhook)
    logger.info(f"Webhook endpoint hit: {request.method} {request.path} from {request.remote}")
    logger.info(f"Request headers: {dict(request.headers)}")
    
    # Optional webhook authentication
    if WEBHOOK_SECRET:
        signature = request.headers.get("X-Alchemy-Signature", "")
        if signature != WEBHOOK_SECRET:
            logger.warning("Webhook authentication failed")
            return web.Response(status=401, text="Unauthorized")
    
    try:
        # Parse JSON payload
        data = await request.json()
        webhook_id = data.get('webhookId', 'unknown')
        logger.info(f"✅ Received webhook from Alchemy: {webhook_id}")
        
        # Alchemy can send events in two formats:
        # 1. Array format: {"activity": [event1, event2, ...]}
        # 2. Single event format: {event data at top level}
        
        events_to_process = []
        
        # Check for activity array first
        activity = data.get("activity", [])
        if activity:
            events_to_process = activity
        else:
            # Check if this is a single event at top level
            event_type = data.get("type", "")
            if event_type == "NFT_ACTIVITY":
                events_to_process = [data]
        
        if not events_to_process:
            logger.debug("No events found in webhook payload")
            return web.Response(status=200, text="OK")
        
        logger.info(f"Processing {len(events_to_process)} event(s) from webhook")
        
        # Process events asynchronously (fire-and-forget)
        for event in events_to_process:
            # Check event type - Alchemy uses "type": "NFT_ACTIVITY"
            event_type = event.get("type", "")
            if event_type != "NFT_ACTIVITY":
                logger.debug(f"Skipping event type: {event_type}")
                continue
            
            # Get transaction hash (can be "transactionHash" or "hash")
            tx_hash = event.get("transactionHash") or event.get("hash", "")
            if not tx_hash:
                logger.warning("Event missing transaction hash, skipping")
                continue
            
            # Get contract address from log.address
            log_data = event.get("log", {})
            contract_address = log_data.get("address", "").lower()
            
            # Verify it's for our contract
            if contract_address != NFT_CONTRACT_ADDRESS:
                logger.debug(f"Event for different contract: {contract_address}, skipping")
                continue
            
            # Create async task for processing (don't await)
            asyncio.create_task(process_webhook_sale_with_timeout(tx_hash, event))
        
        # Always return 200 OK immediately
        return web.Response(status=200, text="OK")
        
    except Exception as e:
        logger.error(f"Error handling webhook: {e}", exc_info=True)
        # Still return 200 OK to keep webhook healthy
        return web.Response(status=200, text="OK")


@client.event
async def on_ready():
    """Called when bot is ready."""
    global sales_fetcher, discord_channel
    
    logger.info(f"Bot logged in as {client.user}")
    
    # Initialize sales fetcher
    sales_fetcher = SalesFetcher(ALCHEMY_API_KEY, NFT_CONTRACT_ADDRESS)
    
    # Get Discord channel - try multiple methods
    try:
        # Method 1: Direct channel lookup (works if bot can see the channel)
        discord_channel = client.get_channel(DISCORD_CHANNEL_ID)
        
        # Method 2: If not found, try fetching from all guilds
        if discord_channel is None:
            logger.warning(f"Channel {DISCORD_CHANNEL_ID} not found via direct lookup, trying guild search...")
            for guild in client.guilds:
                channel = guild.get_channel(DISCORD_CHANNEL_ID)
                if channel:
                    discord_channel = channel
                    logger.info(f"Found channel in guild: {guild.name}")
                    break
        
        # Method 3: If still not found, try fetching via API
        if discord_channel is None:
            logger.warning(f"Channel {DISCORD_CHANNEL_ID} still not found, will try to fetch on first use")
        else:
            logger.info(f"Monitoring channel: {discord_channel.name} (ID: {discord_channel.id})")
    except Exception as e:
        logger.error(f"Error getting channel: {e}")
        logger.warning("Bot will continue but may not be able to post to channel until it's accessible")
    
    # Sync slash commands
    try:
        synced = await tree.sync()
        logger.info(f"Synced {len(synced)} command(s)")
    except Exception as e:
        logger.error(f"Error syncing commands: {e}")


@tree.command(name="lastsale", description="Fetch and display the most recent sale")
async def lastsale(interaction: discord.Interaction):
    """Slash command to fetch the last sale."""
    await interaction.response.defer()
    
    try:
        if not sales_fetcher:
            await interaction.followup.send("Bot not ready yet. Please try again in a moment.")
            return
        
        # Fetch last sale with timeout
        try:
            sales = await asyncio.wait_for(
                sales_fetcher.fetch_last_n_sales(n=1),
                timeout=45.0  # Increased timeout
            )
        except asyncio.TimeoutError:
            await interaction.followup.send("Request timed out. Please try again.")
            logger.error("lastsale command timed out")
            return
        
        if not sales:
            await interaction.followup.send("No recent sales found.")
            return
        
        sale = sales[0]
        
        # Get token IDs
        token_ids = sale.token_ids if sale.token_ids else ([sale.token_id] if sale.token_id else [])
        
        # Fetch images - this gets the embed image URL (only 1 API call per token)
        image_urls = await sales_fetcher.fetch_nft_images(token_ids, max_images=20)
        logger.info(f"📸 Fetched {len(image_urls)} image(s) for /lastsale command")
        if image_urls:
            logger.info(f"📸 First image URL: {image_urls[0][:150]}...")
        else:
            logger.error(f"❌ NO IMAGE URLS RETURNED for token IDs: {token_ids}")
            logger.error(f"❌ This means fetch_nft_images() returned empty list - check Alchemy API responses")
        
        # Create embed with the image URL
        embed = create_sale_embed(sale, image_urls)
        embed.set_footer(text=f"Requested by {interaction.user.display_name} | NFT Sales Monitor")
        
        # Download image for file attachment - REQUIRED for Cloudinary URLs
        # Discord can't fetch Cloudinary URLs, so we MUST download and attach as file
        file = None
        image_data = None
        is_cloudinary = False
        if token_ids and len(token_ids) > 0 and image_urls:
            embed_url = image_urls[0]
            is_cloudinary = 'cloudinary.com' in embed_url
            
            if is_cloudinary:
                    logger.info(f"📥 MUST download Cloudinary image (Discord can't fetch these URLs): {embed_url[:80]}...")
                    # Try multiple Cloudinary URL variations if first fails
                    urls_to_try = [embed_url]
                    
                    # Also try to get pngUrl from metadata if available (different Cloudinary URL)
                    try:
                        if token_ids and len(token_ids) > 0:
                            metadata = await sales_fetcher.get_nft_metadata(token_ids[0])
                            if metadata:
                                top_image = metadata.get("image", {})
                                if isinstance(top_image, dict):
                                    png_url = top_image.get("pngUrl")
                                    if png_url and isinstance(png_url, str) and 'cloudinary.com' in png_url:
                                        if png_url not in urls_to_try:
                                            urls_to_try.append(png_url)
                                            logger.info(f"📥 Added pngUrl from metadata to try list: {png_url[:80]}...")
                    except Exception as e:
                        logger.debug(f"Could not fetch pngUrl from metadata: {e}")
                    
                    # Try thumbnail URL if we have it
                    if len(image_urls) > 1:
                        if image_urls[1] not in urls_to_try:
                            urls_to_try.append(image_urls[1])
                    
                    # Try constructing alternative Cloudinary URLs
                    if '/f_png' in embed_url:
                        # Try without f_png transformation
                        alt_url = embed_url.replace('/f_png,so_0/', '/').replace('/f_png/', '/')
                        if alt_url != embed_url and alt_url not in urls_to_try:
                            urls_to_try.append(alt_url)
            else:
                logger.info(f"📥 Attempting to download image for file attachment: {embed_url[:80]}...")
                urls_to_try = [embed_url]
            
            # Try downloading from multiple URLs
            for attempt, url_to_try in enumerate(urls_to_try, 1):
                try:
                    logger.info(f"📥 Attempt {attempt}/{len(urls_to_try)}: Downloading from {url_to_try[:80]}...")
                    # Download the image - CRITICAL for Cloudinary URLs
                    image_data = await sales_fetcher.download_image(url_to_try)
                    if image_data:
                        # Determine file extension from URL or content type
                        ext = "png"  # Default
                        url_lower = url_to_try.lower()
                        if ".jpg" in url_lower or ".jpeg" in url_lower:
                            ext = "jpg"
                        elif ".gif" in url_lower:
                            ext = "gif"
                        elif ".webp" in url_lower:
                            ext = "webp"
                        elif ".png" in url_lower or "f_png" in url_lower:
                            ext = "png"
                        
                        file = discord.File(
                            io.BytesIO(image_data),
                            filename=f"nft_{sale.token_id or 'image'}.{ext}"
                        )
                        logger.info(f"✅ Successfully downloaded image: {len(image_data)} bytes, extension: {ext}")
                        if is_cloudinary:
                            logger.info(f"✅ Cloudinary image downloaded - will attach as file (Discord can't fetch Cloudinary URLs)")
                        break  # Success - stop trying other URLs
                    else:
                        logger.warning(f"⚠️ Attempt {attempt} failed: No image data returned from {url_to_try[:80]}...")
                        if attempt < len(urls_to_try):
                            logger.info(f"🔄 Trying next URL...")
                except Exception as e:
                    logger.warning(f"⚠️ Attempt {attempt} failed: {e}")
                    if attempt < len(urls_to_try):
                        logger.info(f"🔄 Trying next URL...")
                    continue
            
            # If all Cloudinary URLs failed, extract frame from video (MOST RELIABLE)
            if not image_data and is_cloudinary and token_ids:
                logger.warning(f"⚠️ All Cloudinary URLs failed, extracting frame from video...")
                try:
                    # Get video URL from metadata
                    metadata = await sales_fetcher.get_nft_metadata(token_ids[0])
                    if metadata:
                        top_image = metadata.get("image", {})
                        if isinstance(top_image, dict):
                            original_url = top_image.get("originalUrl", "")
                            if original_url and '/ipfs/' in original_url and ('.mp4' in original_url.lower() or 'video' in original_url.lower()):
                                logger.info(f"🎬 Found video URL: {original_url[:80]}...")
                                # Extract frame from video
                                frame_data = await sales_fetcher.extract_video_frame(original_url, token_ids[0])
                                if frame_data:
                                    file = discord.File(
                                        io.BytesIO(frame_data),
                                        filename=f"nft_{sale.token_id or 'image'}.png"
                                    )
                                    image_data = frame_data
                                    logger.info(f"✅ Successfully extracted frame from video: {len(frame_data)} bytes")
                                else:
                                    logger.warning(f"⚠️ Frame extraction failed")
                            else:
                                logger.warning(f"⚠️ No video URL found in metadata")
                        else:
                            logger.warning(f"⚠️ Image metadata is not a dict")
                    else:
                        logger.warning(f"⚠️ Could not fetch metadata for video extraction")
                except Exception as video_error:
                    logger.warning(f"⚠️ Video frame extraction failed: {video_error}")
            
            if not image_data:
                if is_cloudinary:
                    logger.error(f"❌ CRITICAL: All image download attempts failed - Discord won't be able to display image!")
                    logger.error(f"❌ Tried {len(urls_to_try)} Cloudinary URL(s) and IPFS fallback")
                else:
                    logger.debug(f"Could not download image from embed URL (may be too large)")
        
        # Log image information for debugging
        if image_urls:
            logger.info(f"Using embed image URL: {image_urls[0][:100]}... (length: {len(image_urls[0])})")
        else:
            logger.warning(f"No image URLs available for token ID(s): {token_ids}")
        
        # Send message with embed and file attachment
        if file:
            if image_data and len(image_data) > 0:
                logger.info(f"📎 Sending message with embed + file attachment ({len(image_data)} bytes)")
                logger.info(f"📎 File object: {file.filename}, size: {len(image_data)} bytes")
                try:
                    message = await interaction.followup.send(embed=embed, file=file)
                    logger.info(f"✅ Sent message with image attachment - Message ID: {message.id if hasattr(message, 'id') else 'N/A'}")
                    # Verify the message was sent with attachment
                    if hasattr(message, 'attachments') and message.attachments:
                        logger.info(f"✅ Message has {len(message.attachments)} attachment(s) - image should be visible!")
                        for att in message.attachments:
                            logger.info(f"✅ Attachment: {att.filename}, size: {att.size} bytes, URL: {att.url[:80]}...")
                    else:
                        logger.warning(f"⚠️ Message sent but has no attachments - file may not have been attached!")
                except discord.HTTPException as e:
                    logger.error(f"❌ Discord API error sending message with file: {e.status} - {e.text}")
                    if e.status == 413:
                        logger.error(f"❌ File too large ({len(image_data)} bytes) - Discord limit is 8MB")
                    # Try sending without file as fallback
                    logger.warning(f"⚠️ Attempting to send message without file attachment...")
                    message = await interaction.followup.send(embed=embed)
                    logger.info(f"✅ Sent message without image - Message ID: {message.id if hasattr(message, 'id') else 'N/A'}")
            else:
                logger.error(f"❌ File object exists but image_data is empty or None!")
                file = None  # Don't send invalid file
        else:
            if image_urls:
                is_cloudinary = 'cloudinary.com' in image_urls[0]
                if is_cloudinary:
                    logger.error(f"❌ CRITICAL: Cloudinary image download failed - Discord won't be able to display image!")
                    logger.error(f"❌ Image URL: {image_urls[0][:100]}...")
                else:
                    logger.info(f"📤 Sending message with embed image URL only (file download failed or not attempted)")
            else:
                logger.warning(f"⚠️ Sending message without image - no image URLs found for token ID(s): {token_ids}")
            message = await interaction.followup.send(embed=embed)
            logger.info(f"✅ Sent message - Message ID: {message.id if hasattr(message, 'id') else 'N/A'}")
        
        # Log embed image status
        if embed.image:
            logger.info(f"✓ Embed image URL set: {embed.image.url[:100] if embed.image.url else 'None'}...")
        else:
            logger.warning("✗ Embed image URL was NOT set - check image URL fetching")
        logger.info(f"Last sale command executed by {interaction.user}")
        
    except Exception as e:
        logger.error(f"❌ Error in lastsale command: {e}", exc_info=True)
        error_msg = f"Error fetching last sale: {str(e)[:200]}"
        try:
            await interaction.followup.send(error_msg)
        except Exception as send_error:
            logger.error(f"❌ Failed to send error message: {send_error}")
            # Try sending a generic message
            try:
                await interaction.followup.send("Error fetching last sale. Please try again later.")
            except:
                pass


async def health_check(request: web.Request) -> web.Response:
    """Health check endpoint for Railway/webhook monitoring."""
    return web.Response(text="OK", status=200)

async def webhook_test(request: web.Request) -> web.Response:
    """Test endpoint to verify webhook is accessible."""
    logger.info(f"Webhook test endpoint hit from {request.remote}")
    return web.Response(
        text="Webhook endpoint is accessible! Configure this URL in Alchemy: https://your-domain.com/webhook",
        status=200
    )

async def start_webhook_server():
    """Start aiohttp webhook server."""
    app = web.Application()
    app.router.add_post("/webhook", handle_alchemy_webhook)
    app.router.add_get("/", health_check)  # Health check endpoint
    app.router.add_get("/health", health_check)  # Alternative health check
    app.router.add_get("/webhook-test", webhook_test)  # Webhook test endpoint
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT)
    await site.start()
    logger.info(f"✅ Webhook server started on port {WEBHOOK_PORT}")
    logger.info(f"✅ Webhook endpoint: http://0.0.0.0:{WEBHOOK_PORT}/webhook")
    logger.info(f"✅ Health check: http://0.0.0.0:{WEBHOOK_PORT}/health")
    logger.info(f"✅ Webhook test: http://0.0.0.0:{WEBHOOK_PORT}/webhook-test")
    logger.info("⚠️  IMPORTANT: Configure this webhook URL in Alchemy Dashboard!")
    logger.info("⚠️  For local testing, use ngrok: ngrok http 8080")
    logger.info("⚠️  For production, use your Railway/public URL: https://your-app.railway.app/webhook")


async def main():
    """Main entry point."""
    global sales_fetcher
    
    # Validate configuration
    if not DISCORD_BOT_TOKEN:
        logger.error("DISCORD_BOT_TOKEN not set")
        return
    if not DISCORD_CHANNEL_ID:
        logger.error("DISCORD_CHANNEL_ID not set")
        return
    if not NFT_CONTRACT_ADDRESS:
        logger.error("NFT_CONTRACT_ADDRESS not set")
        return
    if not ALCHEMY_API_KEY:
        logger.error("ALCHEMY_API_KEY not set")
        return
    
    # Start webhook server
    await start_webhook_server()
    
    # Start Discord bot (this will run until stopped)
    try:
        await client.start(DISCORD_BOT_TOKEN)
    finally:
        # Cleanup
        if sales_fetcher:
            await sales_fetcher.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)

