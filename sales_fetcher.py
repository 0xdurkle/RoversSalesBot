"""
Alchemy API integration module for fetching NFT sales data.
Includes IPFS direct image fetching for improved reliability.
"""
import asyncio
import logging
import ssl
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Tuple
from decimal import Decimal

import aiohttp
import certifi

logger = logging.getLogger(__name__)

# WETH contract address on Ethereum mainnet
WETH_CONTRACT = "0xc02aa39b223fe8d0a0e5c4f27ead9083c756cc2"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


@dataclass
class SaleEvent:
    """Represents an NFT sale event."""
    tx_hash: str
    buyer: str
    seller: str
    token_id: Optional[str]
    token_ids: Optional[List[str]]
    token_count: int
    total_price: int  # Price in wei
    timestamp: Optional[datetime]
    is_weth: bool


class SalesFetcher:
    """Handles all Alchemy API calls for NFT sales data."""
    
    def __init__(self, api_key: str, contract_address: str):
        """
        Initialize SalesFetcher.
        
        Args:
            api_key: Alchemy API key
            contract_address: NFT contract address (lowercase)
        """
        self.api_key = api_key
        self.contract_address = contract_address.lower()
        self.rpc_url = f"https://eth-mainnet.g.alchemy.com/v2/{api_key}"
        self.nft_api_url = f"https://eth-mainnet.g.alchemy.com/nft/v3/{api_key}"
        self.session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self.session is None or self.session.closed:
            # Create SSL context with certifi
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            self.session = aiohttp.ClientSession(connector=connector)
        return self.session
    
    async def close(self):
        """Close HTTP session."""
        if self.session and not self.session.closed:
            await self.session.close()
    
    async def _rpc_call(self, method: str, params: List) -> dict:
        """
        Make JSON-RPC call to Alchemy.
        
        Args:
            method: RPC method name
            params: Method parameters
            
        Returns:
            Response data
        """
        session = await self._get_session()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params
        }
        
        try:
            async with session.post(
                self.rpc_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                response.raise_for_status()
                data = await response.json()
                if "error" in data:
                    logger.error(f"RPC error: {data['error']}")
                    return {}
                return data.get("result", {})
        except Exception as e:
            logger.error(f"RPC call failed for {method}: {e}")
            return {}
    
    async def _nft_api_call(self, endpoint: str, params: dict) -> dict:
        """
        Make call to Alchemy NFT API.
        
        Args:
            endpoint: API endpoint (e.g., "getNFTMetadata")
            params: Query parameters
            
        Returns:
            Response data
        """
        session = await self._get_session()
        url = f"{self.nft_api_url}/{endpoint}"
        
        try:
            async with session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                response.raise_for_status()
                return await response.json()
        except Exception as e:
            logger.error(f"NFT API call failed for {endpoint}: {e}")
            return {}
    
    async def get_transaction(self, tx_hash: str) -> dict:
        """
        Get transaction details by hash.
        
        Args:
            tx_hash: Transaction hash
            
        Returns:
            Transaction data
        """
        return await self._rpc_call("eth_getTransactionByHash", [tx_hash])
    
    async def get_asset_transfers(
        self,
        from_address: Optional[str] = None,
        to_address: Optional[str] = None,
        contract_address: Optional[str] = None,
        category: List[str] = None,
        from_block: Optional[str] = None,
        to_block: Optional[str] = None,
        page_key: Optional[str] = None
    ) -> dict:
        """
        Get asset transfers using alchemy_getAssetTransfers.
        
        Args:
            from_address: From address filter
            to_address: To address filter
            contract_address: Contract address filter
            category: Transfer categories (e.g., ["erc20", "erc721"])
            from_block: Starting block (hex)
            to_block: Ending block (hex)
            page_key: Pagination key
            
        Returns:
            Transfer data
        """
        params = {}
        if from_address:
            params["fromAddress"] = from_address
        if to_address:
            params["toAddress"] = to_address
        if contract_address:
            params["contractAddresses"] = [contract_address]
        if category:
            params["category"] = category
        if from_block:
            params["fromBlock"] = from_block
        if to_block:
            params["toBlock"] = to_block
        if page_key:
            params["pageKey"] = page_key
        
        return await self._rpc_call("alchemy_getAssetTransfers", [params])
    
    async def get_nft_metadata(self, token_id: str) -> dict:
        """
        Get NFT metadata including image.
        
        Args:
            token_id: Token ID (hex or decimal string)
            
        Returns:
            NFT metadata
        """
        # Convert token_id to decimal if it's hex
        if token_id.startswith("0x"):
            token_id = str(int(token_id, 16))
        
        params = {
            "contractAddress": self.contract_address,
            "tokenId": token_id
        }
        return await self._nft_api_call("getNFTMetadata", params)
    
    async def get_current_block(self) -> int:
        """
        Get current block number.
        
        Returns:
            Current block number (decimal)
        """
        result = await self._rpc_call("eth_blockNumber", [])
        if result:
            return int(result, 16)
        return 0
    
    async def _get_transaction_price_simple(
        self,
        tx_hash: str
    ) -> Tuple[int, bool]:
        """
        Get transaction price in wei.
        Supports both ETH and WETH.
        
        Args:
            tx_hash: Transaction hash
            
        Returns:
            Tuple of (price in wei, is_weth: bool)
        """
        try:
            # Get transaction details
            tx = await self.get_transaction(tx_hash)
            if not tx:
                return (0, False)
            
            # Check direct ETH value
            eth_value_hex = tx.get("value", "0x0")
            eth_value = int(eth_value_hex, 16) if eth_value_hex != "0x0" else 0
            
            if eth_value > 0:
                return (eth_value, False)
            
            # If ETH value is 0, check for WETH transfers
            # Get block number from transaction
            block_hex = tx.get("blockNumber")
            if not block_hex:
                return (0, False)
            
            block_num = int(block_hex, 16)
            block_hex_str = hex(block_num)
            
            # Get ERC-20 transfers for this block (WETH only)
            transfers = await self.get_asset_transfers(
                contract_address=WETH_CONTRACT,
                category=["erc20"],
                from_block=block_hex_str,
                to_block=block_hex_str
            )
            
            weth_total = 0
            transfers_list = transfers.get("transfers", [])
            
            # Filter transfers for this specific transaction
            for transfer in transfers_list:
                transfer_hash = transfer.get("hash", "")
                if transfer_hash and transfer_hash.lower() == tx_hash.lower():
                    # Get WETH amount
                    value_hex = transfer.get("value", "0x0")
                    if value_hex and value_hex != "0x0":
                        try:
                            weth_total += int(value_hex, 16)
                        except (ValueError, TypeError):
                            pass
            
            if weth_total > 0:
                return (weth_total, True)
            
            return (0, False)
        except Exception as e:
            logger.error(f"Error fetching price for {tx_hash}: {e}")
            return (0, False)
    
    async def fetch_nft_images(
        self,
        token_ids: List[str],
        max_images: int = 20
    ) -> List[str]:
        """
        Fetch NFT images for given token IDs.
        Batches requests to avoid rate limits.
        
        Args:
            token_ids: List of token IDs
            max_images: Maximum number of images to fetch
            
        Returns:
            List of image URLs
        """
        if not token_ids:
            logger.debug("No token IDs provided for image fetching")
            return []
        
        # Limit to max_images
        token_ids = token_ids[:max_images]
        logger.info(f"Fetching images for {len(token_ids)} token(s): {token_ids[:5]}{'...' if len(token_ids) > 5 else ''}")
        image_urls = []
        batch_size = 5
        
        for i in range(0, len(token_ids), batch_size):
            batch = token_ids[i:i + batch_size]
            
            # Fetch metadata for batch in parallel
            tasks = [self.get_nft_metadata(token_id) for token_id in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for result in results:
                if isinstance(result, Exception):
                    logger.warning(f"Error fetching NFT metadata: {result}")
                    continue
                
                if not result:
                    continue
                
                # Try different image sources in priority order
                image_url = None
                
                # 1. Try media[0].gateway (can be string or dict)
                media = result.get("media", [])
                logger.info(f"Media array length: {len(media) if media else 0}")
                if media and len(media) > 0:
                    media_item = media[0]
                    # Log the full media item structure for debugging
                    logger.info(f"Media item type: {type(media_item)}")
                    if isinstance(media_item, dict):
                        logger.info(f"Media item keys: {list(media_item.keys())}")
                        # Log the full media item (truncated)
                        logger.info(f"Media item (first 500 chars): {str(media_item)[:500]}")
                    
                    gateway_value = media_item.get('gateway') if isinstance(media_item, dict) else None
                    raw_value = media_item.get('raw') if isinstance(media_item, dict) else None
                    
                    logger.info(f"Gateway type: {type(gateway_value)}, value: {str(gateway_value)[:100] if gateway_value else 'None'}")
                    logger.info(f"Raw type: {type(raw_value)}, is_dict: {isinstance(raw_value, dict) if raw_value else False}")
                    
                    if isinstance(gateway_value, dict):
                        logger.info(f"Gateway dict keys: {list(gateway_value.keys())}")
                        logger.info(f"Gateway dict has pngUrl: {bool(gateway_value.get('pngUrl'))}")
                        logger.info(f"Gateway dict has thumbnailUrl: {bool(gateway_value.get('thumbnailUrl'))}")
                        if gateway_value.get('pngUrl'):
                            logger.info(f"PNG URL found in gateway: {gateway_value.get('pngUrl')[:80]}...")
                        if gateway_value.get('thumbnailUrl'):
                            logger.info(f"Thumbnail URL found in gateway: {gateway_value.get('thumbnailUrl')[:80]}...")
                    
                    if isinstance(raw_value, dict):
                        logger.info(f"Raw dict keys: {list(raw_value.keys())}")
                        if raw_value.get('pngUrl'):
                            logger.info(f"PNG URL found in raw: {raw_value.get('pngUrl')[:80]}...")
                        if raw_value.get('thumbnailUrl'):
                            logger.info(f"Thumbnail URL found in raw: {raw_value.get('thumbnailUrl')[:80]}...")
                    
                    # Check content type at media item level first
                    content_type = media_item.get("contentType", "")
                    is_video = "video" in content_type.lower() if content_type else False
                    logger.debug(f"Content type: {content_type}, is_video: {is_video}")
                    
                    # Handle case where gateway is a dict with multiple URL options
                    if isinstance(media_item.get("gateway"), dict):
                        gateway_dict = media_item.get("gateway")
                        # Also check contentType in the dict
                        if not is_video:
                            is_video = "video" in gateway_dict.get("contentType", "").lower()
                        
                        # Log what's available in the dict for debugging
                        logger.debug(f"Gateway dict keys: {list(gateway_dict.keys())}")
                        logger.debug(f"Has pngUrl: {bool(gateway_dict.get('pngUrl'))}")
                        logger.debug(f"Has thumbnailUrl: {bool(gateway_dict.get('thumbnailUrl'))}")
                        logger.debug(f"ContentType: {gateway_dict.get('contentType', 'unknown')}")
                        
                        # ALWAYS prefer PNG/thumbnail URLs if available (works for both videos and images)
                        # Discord embeds work better with static images
                        if gateway_dict.get("pngUrl"):
                            image_url = gateway_dict.get("pngUrl")
                            logger.info(f"Using PNG URL for embed: {image_url[:60]}...")
                        elif gateway_dict.get("thumbnailUrl"):
                            image_url = gateway_dict.get("thumbnailUrl")
                            logger.info(f"Using thumbnail URL for embed: {image_url[:60]}...")
                        elif is_video:
                            # For videos without PNG/thumbnail, log warning
                            logger.warning(f"Video detected but no PNG/thumbnail available. Available keys: {list(gateway_dict.keys())}")
                            image_url = gateway_dict.get("cachedUrl") or gateway_dict.get("originalUrl")
                        else:
                            # For images, prefer cachedUrl, then originalUrl
                            image_url = gateway_dict.get("cachedUrl") or gateway_dict.get("originalUrl")
                    else:
                        # Gateway is a string URL directly
                        image_url = media_item.get("gateway")
                        logger.info(f"Gateway is a string URL: {image_url[:80] if image_url else 'None'}...")
                        # Check if it's a video URL - if so, try to get PNG from raw
                        if image_url and ("video" in content_type.lower() or ".mp4" in image_url.lower()):
                            logger.info("Video URL detected in string gateway, checking raw for PNG/thumbnail")
                            raw_item = media_item.get("raw")
                            if isinstance(raw_item, dict):
                                if raw_item.get("pngUrl"):
                                    image_url = raw_item.get("pngUrl")
                                    logger.info(f"Found PNG URL in raw: {image_url[:80]}...")
                                elif raw_item.get("thumbnailUrl"):
                                    image_url = raw_item.get("thumbnailUrl")
                                    logger.info(f"Found thumbnail URL in raw: {image_url[:80]}...")
                    
                    # Fallback to raw if gateway didn't work
                    if not image_url:
                        raw_item = media_item.get("raw")
                        if isinstance(raw_item, dict):
                            if not is_video:
                                is_video = "video" in raw_item.get("contentType", "").lower()
                            
                            # ALWAYS prefer PNG/thumbnail URLs if available
                            if raw_item.get("pngUrl"):
                                image_url = raw_item.get("pngUrl")
                                logger.info(f"Using PNG URL from raw: {image_url[:60]}...")
                            elif raw_item.get("thumbnailUrl"):
                                image_url = raw_item.get("thumbnailUrl")
                                logger.info(f"Using thumbnail URL from raw: {image_url[:60]}...")
                            elif is_video:
                                logger.warning("Video in raw but no PNG/thumbnail available")
                                image_url = raw_item.get("cachedUrl") or raw_item.get("originalUrl")
                            else:
                                image_url = raw_item.get("cachedUrl") or raw_item.get("originalUrl")
                        else:
                            image_url = raw_item
                
                # 2. Try metadata.image
                if not image_url:
                    metadata = result.get("metadata", {})
                    logger.info(f"Metadata type: {type(metadata)}, keys: {list(metadata.keys()) if isinstance(metadata, dict) else 'N/A'}")
                    if metadata:
                        meta_image = metadata.get("image")
                        logger.info(f"Metadata image type: {type(meta_image)}, value: {str(meta_image)[:200] if meta_image else 'None'}")
                        # Handle dict case
                        if isinstance(meta_image, dict):
                            logger.info(f"Metadata image dict keys: {list(meta_image.keys())}")
                            if meta_image.get("pngUrl"):
                                image_url = meta_image.get("pngUrl")
                                logger.info(f"Using PNG URL from metadata.image: {image_url[:80]}...")
                            elif meta_image.get("thumbnailUrl"):
                                image_url = meta_image.get("thumbnailUrl")
                                logger.info(f"Using thumbnail URL from metadata.image: {image_url[:80]}...")
                            else:
                                image_url = meta_image.get("cachedUrl") or meta_image.get("originalUrl")
                        else:
                            image_url = meta_image
                
                # 3. Try top-level image
                if not image_url:
                    top_image = result.get("image")
                    logger.info(f"Top-level image type: {type(top_image)}, value: {str(top_image)[:200] if top_image else 'None'}")
                    if isinstance(top_image, dict):
                        logger.info(f"Top-level image dict keys: {list(top_image.keys())}")
                        # Try PNG URL first (more reliable than thumbnail)
                        if top_image.get("pngUrl"):
                            image_url = top_image.get("pngUrl")
                            logger.info(f"Using PNG URL from top-level image: {image_url}")
                        elif top_image.get("thumbnailUrl"):
                            image_url = top_image.get("thumbnailUrl")
                            logger.info(f"Using thumbnail URL from top-level image: {image_url}")
                        elif top_image.get("cachedUrl"):
                            image_url = top_image.get("cachedUrl")
                            logger.info(f"Using cached URL from top-level image: {image_url[:80]}...")
                        else:
                            image_url = top_image.get("originalUrl")
                            logger.info(f"Using original URL from top-level image: {image_url[:80] if image_url else 'None'}...")
                    else:
                        image_url = top_image
                
                # Log where the image URL came from
                if image_url:
                    logger.info(f"Final image URL source determined, type: {type(image_url)}")
                
                # Ensure image_url is a string (not a dict or other type)
                if image_url and isinstance(image_url, str):
                    # Convert IPFS URLs - try multiple gateways
                    if image_url.startswith("ipfs://"):
                        # Extract IPFS hash
                        ipfs_hash = image_url.replace("ipfs://", "").replace("ipfs/", "")
                        # Use Cloudflare IPFS gateway (more reliable than ipfs.io)
                        image_url = f"https://cloudflare-ipfs.com/ipfs/{ipfs_hash}"
                        logger.debug(f"Converted IPFS URL to: {image_url[:50]}...")
                    elif "/ipfs/" in image_url and not image_url.startswith("http"):
                        # Handle IPFS URLs that might be missing protocol
                        if image_url.startswith("ipfs/"):
                            image_url = f"https://cloudflare-ipfs.com/{image_url}"
                    
                    # Clean up URL (remove query params that might cause issues)
                    if "?" in image_url:
                        image_url = image_url.split("?")[0]
                    
                    # Validate URL
                    if image_url.startswith(("http://", "https://")):
                        # Discord has issues with very long URLs, truncate if needed
                        if len(image_url) > 2000:
                            logger.warning(f"Image URL too long ({len(image_url)} chars), truncating")
                            image_url = image_url[:2000]
                        
                        image_urls.append(image_url)
                        logger.info(f"Found image URL: {image_url[:80]}...")
                    else:
                        logger.warning(f"Invalid image URL format: {image_url[:50] if image_url else 'None'}")
                elif image_url:
                    # Log if we got a non-string image URL
                    logger.warning(f"Image URL is not a string (type: {type(image_url)}): {image_url}")
                else:
                    logger.debug("No image URL found in NFT metadata")
            
            # Small delay between batches
            if i + batch_size < len(token_ids):
                await asyncio.sleep(0.1)
        
        logger.info(f"Fetched {len(image_urls)} image(s) for {len(token_ids)} token(s)")
        if not image_urls:
            logger.warning("No images found for any tokens!")
        
        return image_urls
    
    def _extract_ipfs_hash(self, url_or_hash: str) -> Optional[str]:
        """
        Extract IPFS hash from various URL formats.
        
        Args:
            url_or_hash: IPFS URL or hash (e.g., "ipfs://Qm...", "https://ipfs.io/ipfs/Qm...", "Qm...")
            
        Returns:
            IPFS hash (CID) or None
        """
        if not url_or_hash:
            return None
        
        # Remove common IPFS URL prefixes
        hash_str = url_or_hash
        if hash_str.startswith("ipfs://"):
            hash_str = hash_str.replace("ipfs://", "")
        elif "ipfs/" in hash_str:
            # Extract hash from URL like "https://ipfs.io/ipfs/Qm..." or "/ipfs/Qm..."
            parts = hash_str.split("ipfs/")
            if len(parts) > 1:
                hash_str = parts[1].split("?")[0].split("#")[0]  # Remove query params and fragments
        elif hash_str.startswith("Qm") or hash_str.startswith("baf"):
            # Already a hash
            pass
        else:
            return None
        
        # Clean up - remove any trailing slashes or paths
        hash_str = hash_str.strip("/").split("/")[0]
        
        # Validate it looks like an IPFS hash (Qm... for CIDv0, baf... for CIDv1)
        if hash_str.startswith(("Qm", "baf")) and len(hash_str) > 10:
            return hash_str
        
        return None
    
    async def _fetch_metadata_from_ipfs(self, ipfs_hash: str) -> Optional[dict]:
        """
        Fetch NFT metadata JSON directly from IPFS.
        
        Args:
            ipfs_hash: IPFS hash (CID) of the metadata
            
        Returns:
            Metadata JSON as dict, or None if failed
        """
        if not ipfs_hash:
            return None
        
        # Try multiple IPFS gateways (Cloudflare first - fastest)
        gateways = [
            "https://cloudflare-ipfs.com/ipfs/",
            "https://ipfs.io/ipfs/",
        ]
        
        session = await self._get_session()
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
        }
        
        for gateway in gateways:
            try:
                url = f"{gateway}{ipfs_hash}"
                logger.debug(f"Trying to fetch metadata from IPFS: {url[:80]}...")
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=2)  # Shorter timeout - 2 seconds max
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info(f"Successfully fetched metadata from IPFS gateway: {gateway}")
                        return data
                    else:
                        logger.debug(f"IPFS gateway {gateway} returned {response.status}")
            except asyncio.TimeoutError:
                logger.debug(f"IPFS gateway {gateway} timed out")
                continue
            except Exception as e:
                logger.debug(f"Failed to fetch from {gateway}: {e}")
                continue
        
        logger.debug(f"Failed to fetch metadata from IPFS hash: {ipfs_hash}")
        return None
    
    async def get_ipfs_image_urls(self, token_id: str, timeout: float = 3.0) -> List[str]:
        """
        Get image URLs directly from IPFS by fetching the metadata JSON.
        This bypasses Alchemy's CDN and goes straight to the source.
        
        Args:
            token_id: Token ID to get IPFS image for
            timeout: Maximum time to spend fetching from IPFS (seconds)
            
        Returns:
            List of IPFS image URLs (via gateways)
        """
        try:
            # Use asyncio.wait_for to enforce timeout
            return await asyncio.wait_for(
                self._get_ipfs_image_urls_internal(token_id),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.warning(f"IPFS image fetch timed out for token {token_id} (>{timeout}s), skipping")
            return []
        except Exception as e:
            logger.debug(f"Error in get_ipfs_image_urls for token {token_id}: {e}")
            return []
    
    async def _get_ipfs_image_urls_internal(self, token_id: str) -> List[str]:
        """
        Internal method to get IPFS image URLs (without timeout wrapper).
        """
        try:
            # First, get metadata from Alchemy to find the tokenURI/IPFS hash
            metadata = await self.get_nft_metadata(token_id)
            if not metadata:
                return []
            
            ipfs_hashes = []
            
            # 1. Check tokenUri field (most common)
            token_uri = metadata.get("tokenUri", {})
            if isinstance(token_uri, dict):
                raw_uri = token_uri.get("raw", "")
                if raw_uri:
                    ipfs_hash = self._extract_ipfs_hash(raw_uri)
                    if ipfs_hash:
                        ipfs_hashes.append(ipfs_hash)
            elif isinstance(token_uri, str):
                ipfs_hash = self._extract_ipfs_hash(token_uri)
                if ipfs_hash:
                    ipfs_hashes.append(ipfs_hash)
            
            # 2. Check metadata.raw.originalUrl (might contain IPFS hash)
            meta_raw = metadata.get("metadata", {}).get("raw", {})
            if isinstance(meta_raw, dict):
                original_url = meta_raw.get("originalUrl", "")
                if original_url:
                    ipfs_hash = self._extract_ipfs_hash(original_url)
                    if ipfs_hash:
                        ipfs_hashes.append(ipfs_hash)
            
            # 3. Check media[0].raw.originalUrl
            media = metadata.get("media", [])
            if media and len(media) > 0:
                media_raw = media[0].get("raw", {})
                if isinstance(media_raw, dict):
                    original_url = media_raw.get("originalUrl", "")
                    if original_url:
                        ipfs_hash = self._extract_ipfs_hash(original_url)
                        if ipfs_hash:
                            ipfs_hashes.append(ipfs_hash)
            
            # 4. Check top-level image.originalUrl
            top_image = metadata.get("image", {})
            if isinstance(top_image, dict):
                original_url = top_image.get("originalUrl", "")
                if original_url:
                    ipfs_hash = self._extract_ipfs_hash(original_url)
                    if ipfs_hash:
                        ipfs_hashes.append(ipfs_hash)
            
            # Remove duplicates
            ipfs_hashes = list(set(ipfs_hashes))
            
            if not ipfs_hashes:
                logger.debug(f"No IPFS hashes found in metadata for token {token_id}")
                return []
            
            # Fetch metadata JSON from IPFS
            image_urls = []
            for ipfs_hash in ipfs_hashes:
                try:
                    ipfs_metadata = await self._fetch_metadata_from_ipfs(ipfs_hash)
                    if ipfs_metadata:
                        # PRIORITY 1: Look for thumbnail/preview fields first (for video NFTs)
                        thumbnail_fields = [
                            "thumbnail", "thumbnail_image", "thumbnailImage", 
                            "preview", "preview_image", "previewImage",
                            "image_thumbnail", "imageThumbnail",
                            "poster", "poster_image", "posterImage"
                        ]
                        
                        thumbnail_found = False
                        for thumb_field in thumbnail_fields:
                            thumb_value = ipfs_metadata.get(thumb_field)
                            if thumb_value:
                                thumb_hash = self._extract_ipfs_hash(thumb_value)
                                if thumb_hash:
                                    image_urls.append(f"https://cloudflare-ipfs.com/ipfs/{thumb_hash}")
                                    logger.info(f"Found IPFS thumbnail hash for token {token_id} from field '{thumb_field}': {thumb_hash[:20]}...")
                                    thumbnail_found = True
                                    break
                        
                        # PRIORITY 2: Check if image field is a video, if so skip it
                        image_field = ipfs_metadata.get("image", "")
                        if image_field:
                            # Check if it's a video file
                            is_video = False
                            if isinstance(image_field, str):
                                is_video = any(ext in image_field.lower() for ext in ['.mp4', '.webm', '.mov', '.avi', 'video'])
                            
                            # Also check animation_url (often used for videos)
                            animation_url = ipfs_metadata.get("animation_url", "") or ipfs_metadata.get("animationUrl", "")
                            if animation_url and image_field == animation_url:
                                is_video = True
                                logger.info(f"Image field matches animation_url (likely video), skipping for token {token_id}")
                            
                            # Only use image field if it's NOT a video (or if we didn't find a thumbnail)
                            if not is_video or not thumbnail_found:
                                image_hash = self._extract_ipfs_hash(image_field)
                                if image_hash:
                                    # Skip if it's clearly a video file
                                    if not any(ext in image_hash.lower() for ext in ['.mp4', '.webm', '.mov']):
                                        image_urls.append(f"https://cloudflare-ipfs.com/ipfs/{image_hash}")
                                        logger.info(f"Found IPFS image hash for token {token_id}: {image_hash[:20]}...")
                                    else:
                                        logger.debug(f"Skipping video file from image field: {image_hash[:20]}...")
                except Exception as e:
                    logger.debug(f"Error fetching IPFS metadata for hash {ipfs_hash}: {e}")
                    continue
            
            # Also try to extract thumbnail/IPFS hash directly from Alchemy metadata
            # Check for thumbnail URLs in Alchemy's processed metadata (these are often more reliable)
            for source_name in ["image", "metadata.image", "media.raw.originalUrl"]:
                try:
                    if source_name == "image":
                        img_data = metadata.get("image", {})
                    elif source_name == "metadata.image":
                        img_data = metadata.get("metadata", {}).get("image", {})
                    else:
                        media = metadata.get("media", [])
                        img_data = media[0].get("raw", {}).get("originalUrl", "") if media else ""
                    
                    # If it's a dict, prioritize thumbnailUrl and pngUrl over originalUrl
                    if isinstance(img_data, dict):
                        # Check for thumbnail first
                        thumbnail_url = img_data.get("thumbnailUrl") or img_data.get("thumbnail")
                        if thumbnail_url:
                            ipfs_hash = self._extract_ipfs_hash(thumbnail_url)
                            if ipfs_hash:
                                image_urls.append(f"https://cloudflare-ipfs.com/ipfs/{ipfs_hash}")
                                logger.info(f"Found thumbnail from {source_name} for token {token_id}")
                                continue
                        
                        # Then check PNG URL
                        png_url = img_data.get("pngUrl")
                        if png_url:
                            ipfs_hash = self._extract_ipfs_hash(png_url)
                            if ipfs_hash:
                                image_urls.append(f"https://cloudflare-ipfs.com/ipfs/{ipfs_hash}")
                                logger.info(f"Found PNG from {source_name} for token {token_id}")
                                continue
                        
                        # Last resort: originalUrl (but skip if it's a video)
                        original_url = img_data.get("originalUrl", "")
                        if original_url:
                            # Skip if it's clearly a video
                            if not any(ext in original_url.lower() for ext in ['.mp4', '.webm', '.mov', 'video']):
                                ipfs_hash = self._extract_ipfs_hash(original_url)
                                if ipfs_hash:
                                    image_urls.append(f"https://cloudflare-ipfs.com/ipfs/{ipfs_hash}")
                    elif isinstance(img_data, str):
                        # Skip if it's a video file
                        if not any(ext in img_data.lower() for ext in ['.mp4', '.webm', '.mov']):
                            ipfs_hash = self._extract_ipfs_hash(img_data)
                            if ipfs_hash:
                                image_urls.append(f"https://cloudflare-ipfs.com/ipfs/{ipfs_hash}")
                except Exception as e:
                    logger.debug(f"Error extracting IPFS hash from {source_name}: {e}")
            
            # Remove duplicates
            image_urls = list(dict.fromkeys(image_urls))  # Preserves order
            
            if image_urls:
                logger.info(f"Found {len(image_urls)} IPFS image URL(s) for token {token_id}")
            else:
                logger.debug(f"No IPFS image URLs found for token {token_id}")
            
            return image_urls
        except Exception as e:
            logger.error(f"Error getting IPFS image URLs for token {token_id}: {e}")
            return []
    
    async def get_all_image_urls_for_token(self, token_id: str) -> List[str]:
        """
        Get all available image URLs for a token in priority order.
        Prioritizes IPFS URLs (most reliable), then Alchemy CDN URLs.
        Returns URLs from smallest to largest (thumbnail -> PNG -> cached -> original).
        
        Args:
            token_id: Token ID to get URLs for
            
        Returns:
            List of image URLs in priority order (best first)
        """
        ipfs_urls = []  # Initialize outside try block for exception handler
        try:
            # FIRST: Try to get IPFS URLs directly (most reliable source)
            ipfs_urls = await self.get_ipfs_image_urls(token_id)
            
            # SECOND: Get Alchemy metadata URLs as fallback
            metadata = await self.get_nft_metadata(token_id)
            if not metadata:
                # If no metadata but we have IPFS URLs, return those
                return ipfs_urls if ipfs_urls else []
            
            urls = []
            
            # Extract URLs from all possible sources
            def extract_urls_from_dict(d: dict, source_name: str = ""):
                """Helper to extract URLs from a dict in priority order"""
                if not isinstance(d, dict):
                    return []
                found = []
                # Priority: thumbnailUrl (smallest) -> pngUrl -> cachedUrl -> originalUrl
                if d.get("thumbnailUrl"):
                    found.append(("thumbnailUrl", d.get("thumbnailUrl")))
                if d.get("pngUrl"):
                    found.append(("pngUrl", d.get("pngUrl")))
                if d.get("cachedUrl"):
                    found.append(("cachedUrl", d.get("cachedUrl")))
                if d.get("originalUrl"):
                    found.append(("originalUrl", d.get("originalUrl")))
                return found
            
            # 1. Check media[0].gateway
            media = metadata.get("media", [])
            if media and len(media) > 0:
                media_item = media[0]
                gateway = media_item.get("gateway")
                if isinstance(gateway, dict):
                    urls.extend(extract_urls_from_dict(gateway, "media.gateway"))
                elif isinstance(gateway, str):
                    urls.append(("media.gateway", gateway))
                
                # Also check raw
                raw = media_item.get("raw")
                if isinstance(raw, dict):
                    urls.extend(extract_urls_from_dict(raw, "media.raw"))
                elif isinstance(raw, str):
                    urls.append(("media.raw", raw))
            
            # 2. Check metadata.image
            meta_image = metadata.get("metadata", {}).get("image")
            if isinstance(meta_image, dict):
                urls.extend(extract_urls_from_dict(meta_image, "metadata.image"))
            elif isinstance(meta_image, str):
                urls.append(("metadata.image", meta_image))
            
            # 3. Check top-level image
            top_image = metadata.get("image")
            if isinstance(top_image, dict):
                urls.extend(extract_urls_from_dict(top_image, "image"))
            elif isinstance(top_image, str):
                urls.append(("image", top_image))
            
            # Remove duplicates while preserving order (priority: thumbnailUrl > pngUrl > cachedUrl > originalUrl)
            seen = set()
            priority_order = []
            for source, url in urls:
                if url and url not in seen:
                    seen.add(url)
                    priority_order.append((source, url))
            
            # Sort by priority: thumbnailUrl first, then pngUrl, then cachedUrl, then originalUrl
            def get_priority(item):
                source, url = item
                if "thumbnailUrl" in source.lower() or "thumbnail" in source.lower():
                    return 0
                elif "pngUrl" in source.lower() or "png" in source.lower():
                    return 1
                elif "cachedUrl" in source.lower() or "cached" in source.lower():
                    return 2
                elif "originalUrl" in source.lower() or "original" in source.lower():
                    return 3
                else:
                    return 4
            
            priority_order.sort(key=get_priority)
            
            # Return just the URLs
            result = [url for _, url in priority_order]
            
            # Convert IPFS URLs
            for i, url in enumerate(result):
                if url and isinstance(url, str):
                    if url.startswith("ipfs://"):
                        ipfs_hash = url.replace("ipfs://", "").replace("ipfs/", "")
                        result[i] = f"https://cloudflare-ipfs.com/ipfs/{ipfs_hash}"
                    elif "/ipfs/" in url and not url.startswith("http"):
                        if url.startswith("ipfs/"):
                            result[i] = f"https://cloudflare-ipfs.com/ipfs/{url.replace('ipfs/', '')}"
            
            # Combine: IPFS URLs first (most reliable), then Alchemy URLs
            # Remove duplicates while preserving order
            all_urls = ipfs_urls + result
            seen = set()
            final_urls = []
            for url in all_urls:
                if url and url not in seen:
                    seen.add(url)
                    final_urls.append(url)
            
            logger.info(f"Found {len(final_urls)} image URL(s) for token {token_id} ({len(ipfs_urls)} IPFS, {len(result)} Alchemy) in priority order")
            return final_urls
        except Exception as e:
            logger.error(f"Error getting image URLs for token {token_id}: {e}")
            # Return IPFS URLs if we have them, even if Alchemy failed
            if ipfs_urls:
                return ipfs_urls
            return []
    
    async def download_image(self, image_url: str) -> Optional[bytes]:
        """
        Download image from URL and return as bytes.
        More reliable than using embed images.
        
        Args:
            image_url: Image URL to download
            
        Returns:
            Image bytes, or None if download fails
        """
        try:
            session = await self._get_session()
            # Add comprehensive headers to avoid blocking
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Referer': 'https://alchemy.com/',
            }
            
            # For Cloudinary URLs, try to fix common issues
            if 'cloudinary.com' in image_url:
                # Ensure URL is complete - sometimes they're truncated
                if not image_url.endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.mp4')):
                    # Try to append common Cloudinary transformations if missing
                    if '/f_png' in image_url and '/thumbn' in image_url:
                        # This looks like a thumbnail URL that might be incomplete
                        logger.debug(f"Cloudinary URL might be incomplete: {image_url[:100]}...")
            
            async with session.get(
                image_url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
                allow_redirects=True
            ) as response:
                if response.status == 200:
                    content_type = response.headers.get('Content-Type', '')
                    # For video files, try to read only first few MB to check size
                    # Read in chunks to avoid loading huge files into memory
                    image_data = b''
                    max_size = 8 * 1024 * 1024  # 8MB limit
                    chunk_size = 1024 * 1024  # 1MB chunks
                    
                    async for chunk in response.content.iter_chunked(chunk_size):
                        image_data += chunk
                        if len(image_data) > max_size:
                            logger.warning(f"Image too large ({len(image_data)} bytes), stopping download")
                            return None
                    
                    # Basic validation - check if it looks like image data
                    if len(image_data) < 100:
                        logger.warning(f"Image data too small ({len(image_data)} bytes), might not be valid")
                        return None
                    
                    # Check if it's actually a video file by content type, magic bytes, or URL
                    is_video_file = False
                    if 'video' in content_type.lower():
                        is_video_file = True
                    elif image_data.startswith(b'\x00\x00\x00\x18ftyp') or image_data.startswith(b'\x1a\x45\xdf\xa3'):
                        # MP4 or WebM magic bytes
                        is_video_file = True
                    elif any(ext in image_url.lower() for ext in ['.mp4', '.webm', '.mov', '.avi', '.mkv']):
                        is_video_file = True
                    
                    if is_video_file:
                        logger.warning(f"URL returned video content (Content-Type: {content_type}, URL: {image_url[:60]}...), skipping")
                        return None
                    
                    logger.info(f"Downloaded image: {len(image_data)} bytes, Content-Type: {content_type} from {image_url[:60]}...")
                    return image_data
                elif response.status == 400:
                    # For Cloudinary 400 errors, log the full URL for debugging
                    logger.warning(f"HTTP 400 from Cloudinary - URL might be malformed: {image_url[:120]}...")
                    return None
                else:
                    logger.warning(f"Failed to download image: HTTP {response.status} from {image_url[:60]}...")
                    return None
        except asyncio.TimeoutError:
            logger.warning(f"Timeout downloading image from {image_url[:60]}...")
            return None
        except Exception as e:
            logger.error(f"Error downloading image from {image_url[:60]}...: {e}")
            return None
    
    async def download_image_with_fallbacks(self, token_id: str, max_time: float = 5.0) -> Optional[bytes]:
        """
        Download image for a token, trying all available URLs in priority order.
        Also tries alternative thumbnail URLs if primary URLs fail.
        
        Args:
            token_id: Token ID to download image for
            max_time: Maximum time to spend downloading (seconds)
            
        Returns:
            Image bytes, or None if all downloads fail
        """
        try:
            # Get URLs with timeout
            urls = await asyncio.wait_for(
                self.get_all_image_urls_for_token(token_id),
                timeout=2.0  # Quick timeout for URL fetching
            )
        except asyncio.TimeoutError:
            logger.warning(f"Timeout getting image URLs for token {token_id}")
            return None
        
        if not urls:
            logger.warning(f"No image URLs found for token {token_id}")
            return None
        
        # Try all primary URLs first (with overall timeout)
        start_time = asyncio.get_event_loop().time()
        for i, url in enumerate(urls):
            # Check if we've exceeded max time
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > max_time:
                logger.warning(f"Image download timeout for token {token_id} after {elapsed:.1f}s")
                break
            
            logger.info(f"Trying image URL {i+1}/{len(urls)} for token {token_id}: {url[:80]}...")
            image_data = await self.download_image(url)
            if image_data:
                logger.info(f"Successfully downloaded image from URL {i+1} for token {token_id}")
                return image_data
        
        # If all primary URLs failed, try to construct alternative thumbnail URLs
        # from Alchemy CDN URLs
        logger.info(f"Primary URLs failed, trying alternative thumbnail URLs for token {token_id}")
        for url in urls:
            if 'nft-cdn.alchemy.com' in url:
                # Try to get metadata to construct a thumbnail URL
                try:
                    metadata = await self.get_nft_metadata(token_id)
                    if metadata:
                        # Try to use the embed image URL from fetch_nft_images
                        # which might have better URL construction
                        embed_urls = await self.fetch_nft_images([token_id], max_images=1)
                        if embed_urls:
                            logger.info(f"Trying embed image URL as fallback: {embed_urls[0][:80]}...")
                            image_data = await self.download_image(embed_urls[0])
                            if image_data:
                                logger.info(f"Successfully downloaded image from embed URL for token {token_id}")
                                return image_data
                except Exception as e:
                    logger.debug(f"Error trying alternative URLs: {e}")
        
        logger.warning(f"Failed to download image from all {len(urls)} URL(s) for token {token_id}")
        return None
    
    async def fetch_last_n_sales(self, n: int = 1) -> List[SaleEvent]:
        """
        Fetch the last N sales for the collection.
        
        Args:
            n: Number of recent sales to fetch
            
        Returns:
            List of SaleEvent objects
        """
        try:
            # Get current block
            current_block = await self.get_current_block()
            if current_block == 0:
                logger.error("Failed to get current block")
                return []
            
            # Strategy: Start from recent blocks and work backwards
            # Fetch transfers in smaller chunks, starting from most recent
            sales = []
            block_chunk_size = 3000  # Smaller chunks for faster processing
            max_chunks = 3  # Check up to 3 chunks (9k blocks = ~1 day)
            
            # Start from current block and work backwards
            for chunk in range(max_chunks):
                to_block = current_block - (chunk * block_chunk_size)
                from_block = max(0, to_block - block_chunk_size)
                
                if from_block >= to_block:
                    break
                
                logger.info(f"Checking blocks {from_block} to {to_block} (chunk {chunk + 1}/{max_chunks})")
                
                # Get transfers for this chunk
                transfers_data = await self.get_asset_transfers(
                    contract_address=self.contract_address,
                    category=["erc721", "erc1155"],
                    from_block=hex(from_block),
                    to_block=hex(to_block)
                )
                
                transfers = transfers_data.get("transfers", [])
                logger.info(f"Found {len(transfers)} transfers in blocks {from_block}-{to_block}")
                if not transfers:
                    continue
                
                # Sort transfers by block number (most recent first) before processing
                # Extract block numbers for sorting
                transfers_with_blocks = []
                for transfer in transfers:
                    block_hex = transfer.get("blockNum", "0x0")
                    block_num = 0
                    if block_hex and block_hex != "0x0":
                        try:
                            block_num = int(block_hex, 16)
                        except (ValueError, TypeError):
                            pass
                    transfers_with_blocks.append((block_num, transfer))
                
                # Sort by block number descending (most recent first)
                transfers_with_blocks.sort(key=lambda x: x[0], reverse=True)
                transfers = [t[1] for t in transfers_with_blocks]
                
                # Process ALL transfers in this chunk (don't limit - we need the most recent)
                # But limit to reasonable number to avoid timeout
                max_transfers_per_chunk = 100
                if len(transfers) > max_transfers_per_chunk:
                    logger.info(f"Limiting to first {max_transfers_per_chunk} most recent transfers from {len(transfers)} total")
                    transfers = transfers[:max_transfers_per_chunk]
                
                # Process transfers - collect all first, then check prices in batch
                transfer_candidates = []
                for transfer in transfers:
                    from_addr = transfer.get("from", "").lower()
                    to_addr = transfer.get("to", "").lower()
                    
                    # Skip mints and burns
                    if from_addr == ZERO_ADDRESS or to_addr == ZERO_ADDRESS:
                        continue
                    
                    tx_hash = transfer.get("hash", "")
                    if not tx_hash:
                        continue
                    
                    # Get block number for sorting
                    block_num_hex = transfer.get("blockNum", "0x0")
                    block_number = 0
                    if block_num_hex and block_num_hex != "0x0":
                        try:
                            block_number = int(block_num_hex, 16)
                        except (ValueError, TypeError):
                            pass
                    
                    token_id_raw = transfer.get("tokenId", "")
                    
                    # Convert token ID from hex to decimal string if needed
                    if token_id_raw:
                        if isinstance(token_id_raw, str) and token_id_raw.startswith("0x"):
                            try:
                                token_id = str(int(token_id_raw, 16))
                            except (ValueError, TypeError):
                                token_id = token_id_raw
                        else:
                            token_id = str(token_id_raw)
                    else:
                        token_id = ""
                    
                    transfer_candidates.append({
                        "tx_hash": tx_hash,
                        "from_addr": from_addr,
                        "to_addr": to_addr,
                        "token_id": token_id,
                        "block_number": block_number
                    })
                
                # Check prices for all candidates in parallel (batch)
                if transfer_candidates:
                    price_tasks = [
                        self._get_transaction_price_simple(candidate["tx_hash"])
                        for candidate in transfer_candidates
                    ]
                    price_results = await asyncio.gather(*price_tasks, return_exceptions=True)
                    
                    # Create sales only for transfers with prices
                    for candidate, price_result in zip(transfer_candidates, price_results):
                        if isinstance(price_result, Exception):
                            logger.debug(f"Price check failed for {candidate['tx_hash'][:10]}...: {price_result}")
                            continue
                        
                        price, is_weth = price_result
                        logger.debug(f"Transfer {candidate['tx_hash'][:10]}... - Price: {price} wei ({'WETH' if is_weth else 'ETH' if price > 0 else 'None'})")
                        
                        if price == 0:
                            logger.debug(f"Skipping transfer {candidate['tx_hash'][:10]}... - no price detected")
                            continue
                        
                        sale = SaleEvent(
                            tx_hash=candidate["tx_hash"],
                            buyer=candidate["to_addr"],
                            seller=candidate["from_addr"],
                            token_id=candidate["token_id"],
                            token_ids=[candidate["token_id"]],
                            token_count=1,
                            total_price=price,
                            timestamp=None,
                            is_weth=is_weth
                        )
                        
                        # Store block number with sale for sorting
                        sale._block_number = candidate["block_number"]
                        sales.append(sale)
                
                # After processing this chunk, sort and check
                # Always sort by block number to ensure we have the most recent
                if sales:
                    sales.sort(key=lambda x: getattr(x, '_block_number', 0), reverse=True)
                    
                    # Remove duplicates
                    seen_hashes = set()
                    unique_sales = []
                    for sale in sales:
                        if sale.tx_hash.lower() not in seen_hashes:
                            seen_hashes.add(sale.tx_hash.lower())
                            unique_sales.append(sale)
                    sales = unique_sales
                    
                    # If we have sales from the most recent chunk, check if block is very recent
                    # If we're in chunk 0 (most recent) and have sales, return the top one
                    if chunk == 0 and len(sales) >= n:
                        logger.info(f"Found {len(sales)} sales in most recent chunk. Most recent block: {sales[0]._block_number}, Token: {sales[0].token_id}")
                        return sales[:n]
                    
                    # If we have enough sales total, return
                    if len(sales) >= n:
                        logger.info(f"Found {len(sales)} sales across chunks. Most recent block: {sales[0]._block_number}, Token: {sales[0].token_id}")
                        return sales[:n]
            
            # Final sort and deduplication (in case we collected from multiple chunks)
            sales.sort(key=lambda x: getattr(x, '_block_number', 0), reverse=True)
            
            # Remove duplicates by transaction hash
            seen_hashes = set()
            unique_sales = []
            for sale in sales:
                if sale.tx_hash.lower() not in seen_hashes:
                    seen_hashes.add(sale.tx_hash.lower())
                    unique_sales.append(sale)
            
            if unique_sales:
                price_eth = unique_sales[0].total_price / (10**18)
                currency = "WETH" if unique_sales[0].is_weth else "ETH"
                logger.info(f"Found {len(unique_sales)} unique sales. Most recent block: {unique_sales[0]._block_number}, TX: {unique_sales[0].tx_hash[:16]}...")
                logger.info(f"Most recent sale: {price_eth:.6f} {currency} for token {unique_sales[0].token_id}")
            else:
                logger.warning("No sales found in the specified block range")
                logger.warning(f"Searched blocks from {current_block - (max_chunks * block_chunk_size)} to {current_block}")
            
            return unique_sales[:n]
            
        except Exception as e:
            logger.error(f"Error fetching last sales: {e}")
            return []

