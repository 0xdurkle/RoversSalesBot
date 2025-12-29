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
    if image_urls:
        try:
            image_url = image_urls[0]
            # Ensure URL is complete and valid
            if not image_url.startswith(("http://", "https://")):
                logger.warning(f"Invalid image URL format: {image_url[:50]}")
            else:
                embed.set_image(url=image_url)
                logger.info(f"Set embed image (full URL): {image_url}")
                # Also log the URL length to ensure it's not truncated
                logger.info(f"Image URL length: {len(image_url)} characters")
        except Exception as e:
            logger.error(f"Error setting embed image: {e}", exc_info=True)
    else:
        logger.warning("No images available for embed")
    
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
        
        # Fetch images (limit to 20)
        image_urls = await sales_fetcher.fetch_nft_images(token_ids, max_images=20)
        logger.info(f"Fetched {len(image_urls)} image(s) for webhook sale")
        
        # Create embed
        embed = create_sale_embed(sale, image_urls)
        
        # Try to download and attach image as file (more reliable than embed images)
        file = None
        if token_ids and len(token_ids) > 0:
            try:
                # Use the new function that tries all URLs in priority order (5 second timeout)
                image_data = await sales_fetcher.download_image_with_fallbacks(token_ids[0], max_time=5.0)
                if image_data:
                    # Determine file extension from first successful URL
                    urls = await sales_fetcher.get_all_image_urls_for_token(token_ids[0])
                    ext = "png"  # Default
                    if urls:
                        url_lower = urls[0].lower()
                        if ".jpg" in url_lower or ".jpeg" in url_lower:
                            ext = "jpg"
                        elif ".gif" in url_lower:
                            ext = "gif"
                        elif ".webp" in url_lower:
                            ext = "webp"
                    
                    file = discord.File(
                        io.BytesIO(image_data),
                        filename=f"nft_{sale.token_id or 'image'}.{ext}"
                    )
                    logger.info(f"Downloaded image for webhook sale: {len(image_data)} bytes")
            except Exception as e:
                logger.warning(f"Failed to download image for webhook sale: {e}")
        
        # Get channel if not already set (in case it wasn't found at startup)
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
                if file:
                    await discord_channel.send(embed=embed, file=file)
                    logger.info("Posted sale with image attachment")
                else:
                    await discord_channel.send(embed=embed)
                    if not image_urls:
                        logger.warning(f"No images found for webhook sale token ID(s): {token_ids}")
                logger.info(
                    f"Posted sale: {sale.token_count} NFT(s) for {format_price(price, is_weth)} "
                    f"in tx {tx_hash}"
                )
            except discord.Forbidden:
                logger.error(f"Bot doesn't have permission to send messages in channel {DISCORD_CHANNEL_ID}")
            except discord.NotFound:
                logger.error(f"Channel {DISCORD_CHANNEL_ID} not found - bot may not be in the server")
            except Exception as e:
                logger.error(f"Error posting to Discord: {e}")
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
        logger.info(f"Received webhook: {webhook_id}")
        
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
        
        # Fetch images
        image_urls = await sales_fetcher.fetch_nft_images(token_ids, max_images=20)
        logger.info(f"Fetched {len(image_urls)} image(s) for /lastsale command")
        
        # Create embed
        embed = create_sale_embed(sale, image_urls)
        embed.set_footer(text=f"Requested by {interaction.user.display_name} | NFT Sales Monitor")
        
        # Try to download and attach image as file (more reliable than embed images)
        file = None
        if token_ids and len(token_ids) > 0:
            try:
                # Use the new function that tries all URLs in priority order (5 second timeout)
                image_data = await sales_fetcher.download_image_with_fallbacks(token_ids[0], max_time=5.0)
                if image_data:
                    # Determine file extension from first successful URL
                    # Get URLs to check extension
                    urls = await sales_fetcher.get_all_image_urls_for_token(token_ids[0])
                    ext = "png"  # Default
                    if urls:
                        url_lower = urls[0].lower()
                        if ".jpg" in url_lower or ".jpeg" in url_lower:
                            ext = "jpg"
                        elif ".gif" in url_lower:
                            ext = "gif"
                        elif ".webp" in url_lower:
                            ext = "webp"
                    
                    file = discord.File(
                        io.BytesIO(image_data),
                        filename=f"nft_{sale.token_id or 'image'}.{ext}"
                    )
                    logger.info(f"Successfully downloaded and attached image: {len(image_data)} bytes")
            except Exception as e:
                logger.warning(f"Failed to download image: {e}")
        
        # Send message with embed and file attachment
        if file:
            await interaction.followup.send(embed=embed, file=file)
            logger.info("Sent message with image attachment")
        else:
            # Fallback to embed image if download fails
            await interaction.followup.send(embed=embed)
            if image_urls:
                logger.warning("Could not download image, using embed image URL instead")
            else:
                logger.warning(f"No images found for token ID(s): {token_ids}")
        logger.info(f"Last sale command executed by {interaction.user}")
        
    except Exception as e:
        logger.error(f"Error in lastsale command: {e}", exc_info=True)
        await interaction.followup.send("Error fetching last sale. Please try again later.")


async def health_check(request: web.Request) -> web.Response:
    """Health check endpoint for Railway/webhook monitoring."""
    return web.Response(text="OK", status=200)

async def start_webhook_server():
    """Start aiohttp webhook server."""
    app = web.Application()
    app.router.add_post("/webhook", handle_alchemy_webhook)
    app.router.add_get("/", health_check)  # Health check endpoint
    app.router.add_get("/health", health_check)  # Alternative health check
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT)
    await site.start()
    logger.info(f"Webhook server started on port {WEBHOOK_PORT}")
    logger.info("IPFS image fetching enabled - prioritizing direct IPFS URLs")
    logger.info(f"Health check available at: http://0.0.0.0:{WEBHOOK_PORT}/health")


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

