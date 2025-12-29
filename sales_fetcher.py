"""
Alchemy API integration module for fetching NFT sales data.
Includes IPFS direct image fetching for improved reliability.
"""
import asyncio
import logging
import os
import ssl
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

import aiohttp
import certifi

logger = logging.getLogger(__name__)

# WETH contract address on Ethereum mainnet
# WETH contract address on Ethereum mainnet (can be overridden via WETH_CONTRACT_ADDRESS env var)
# Correct address: 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2 (note: three 'a's after C02)
WETH_CONTRACT = os.environ.get("WETH_CONTRACT_ADDRESS", "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2").lower()
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# Cache configuration
MAX_METADATA_CACHE_SIZE = 1000  # Maximum number of cached metadata entries


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
        self._metadata_cache: OrderedDict[str, dict] = OrderedDict()  # LRU cache for metadata
    
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
    
    async def _nft_api_call(self, endpoint: str, params: dict, max_retries: int = 3) -> dict:
        """
        Make call to Alchemy NFT API with retry logic for 500 errors.
        
        Args:
            endpoint: API endpoint (e.g., "getNFTMetadata")
            params: Query parameters
            max_retries: Maximum number of retries for 500 errors
            
        Returns:
            Response data
        """
        session = await self._get_session()
        url = f"{self.nft_api_url}/{endpoint}"
        
        for attempt in range(max_retries):
            try:
                async with session.get(
                    url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    # Retry on 500 errors (server errors are often transient)
                    if response.status == 500:
                        if attempt < max_retries - 1:
                            wait_time = (attempt + 1) * 2  # 2s, 4s, 6s
                            logger.warning(f"Alchemy API returned 500 for {endpoint}, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})")
                            await asyncio.sleep(wait_time)
                            continue
                        else:
                            logger.error(f"Alchemy API returned 500 for {endpoint} after {max_retries} attempts")
                            return {}
                    
                    # For other errors, raise immediately
                    response.raise_for_status()
                    return await response.json()
            except aiohttp.client_exceptions.ClientResponseError as e:
                # Don't retry on client errors (4xx)
                if 400 <= e.status < 500:
                    logger.error(f"NFT API call failed for {endpoint}: {e.status}, message='{e.message}', url='{url}'")
                    return {}
                # Retry on server errors (5xx) if we haven't exhausted retries
                elif e.status >= 500 and attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    logger.warning(f"Alchemy API returned {e.status} for {endpoint}, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    logger.error(f"NFT API call failed for {endpoint}: {e.status}, message='{e.message}', url='{url}'")
                    return {}
            except Exception as e:
                logger.error(f"NFT API call failed for {endpoint}: {e}, url='{url}'")
                return {}
        
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
    
    async def get_transaction_receipt(self, tx_hash: str) -> dict:
        """
        Get transaction receipt by hash (includes logs).
        
        Args:
            tx_hash: Transaction hash
            
        Returns:
            Transaction receipt with logs
        """
        return await self._rpc_call("eth_getTransactionReceipt", [tx_hash])
    
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
        Uses LRU caching to avoid duplicate API calls.
        
        Args:
            token_id: Token ID (hex or decimal string)
            
        Returns:
            NFT metadata
        """
        # Convert token_id to decimal if it's hex
        if token_id.startswith("0x"):
            token_id = str(int(token_id, 16))
        
        # Check cache first (move to end for LRU)
        cache_key = f"{self.contract_address}:{token_id}"
        if cache_key in self._metadata_cache:
            # Move to end (most recently used)
            self._metadata_cache.move_to_end(cache_key)
            logger.debug(f"Using cached metadata for token {token_id}")
            return self._metadata_cache[cache_key]
        
        params = {
            "contractAddress": self.contract_address,
            "tokenId": token_id
        }
        metadata = await self._nft_api_call("getNFTMetadata", params)
        
        # Cache the result with LRU eviction
        if metadata:
            self._metadata_cache[cache_key] = metadata
            # Evict oldest entries if over limit
            while len(self._metadata_cache) > MAX_METADATA_CACHE_SIZE:
                self._metadata_cache.popitem(last=False)
        
        return metadata
    
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
        tx_hash: str,
        seller_address: Optional[str] = None,
        buyer_address: Optional[str] = None
    ) -> Tuple[int, bool]:
        """
        Get transaction price in wei.
        Supports both ETH and WETH.
        
        Args:
            tx_hash: Transaction hash
            seller_address: Seller address (to match WETH transfers to seller)
            buyer_address: Buyer address (to match WETH transfers from buyer)
            
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
                logger.debug(f"No block number in tx {tx_hash[:16]}... - cannot check for WETH")
                return (0, False)
            
            block_num = int(block_hex, 16)
            logger.info(f"🔍 Checking for WETH transfers around block {block_num} for tx {tx_hash[:16]}...")
            logger.info(f"🔍 Seller: {seller_address[:10] if seller_address else 'None'}..., Buyer: {buyer_address[:10] if buyer_address else 'None'}...")
            
            seller_lower = seller_address.lower() if seller_address else None
            buyer_lower = buyer_address.lower() if buyer_address else None
            
            weth_total = 0
            transfers_list = []
            
            # Strategy 0: Check transaction receipt logs for WETH transfers in the SAME transaction
            # This is the most reliable - WETH transfers in the same tx will be in the logs
            logger.info(f"🔍 Strategy 0: Checking transaction receipt logs for WETH transfers in same tx {tx_hash[:16]}...")
            logger.info(f"🔍 Strategy 0: WETH contract address: {WETH_CONTRACT}")
            try:
                receipt = await self.get_transaction_receipt(tx_hash)
                if not receipt:
                    logger.warning(f"⚠️ Strategy 0: No receipt returned for tx {tx_hash[:16]}...")
                elif not receipt.get("logs"):
                    logger.info(f"ℹ️ Strategy 0: Transaction has no logs (might be a simple transfer)")
                else:
                    logs = receipt.get("logs", [])
                    logger.info(f"🔍 Strategy 0: Found {len(logs)} log(s) in transaction")
                    weth_contract_lower = WETH_CONTRACT.lower()
                    # WETH Transfer event signature: Transfer(address indexed from, address indexed to, uint256 value)
                    # Event signature hash: keccak256("Transfer(address,address,uint256)") = 0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef
                    transfer_event_topic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
                    
                    # Log all unique contract addresses in the logs to help debug
                    unique_contracts = set()
                    for log in logs:
                        log_addr = log.get("address", "").lower()
                        if log_addr:
                            unique_contracts.add(log_addr)
                    logger.info(f"🔍 Strategy 0: Unique contract addresses in logs: {len(unique_contracts)}")
                    if unique_contracts:
                        logger.info(f"🔍 Strategy 0: Contract addresses: {', '.join([addr[:10] + '...' for addr in list(unique_contracts)[:5]])}...")
                    
                    weth_logs_found = 0
                    all_weth_transfers = []  # Track all WETH transfers for fallback
                    
                    for i, log in enumerate(logs):
                        log_address = log.get("address", "").lower()
                        # Check if this is a WETH contract log
                        if log_address == weth_contract_lower:
                            weth_logs_found += 1
                            logger.info(f"🔍 Strategy 0: Found WETH contract log #{weth_logs_found} (log {i+1}/{len(logs)})")
                            # Check if this is a Transfer event
                            topics = log.get("topics", [])
                            if topics and len(topics) >= 3:
                                event_topic = topics[0].lower()
                                if event_topic == transfer_event_topic:
                                    # Extract from, to, and value from log
                                    from_addr = "0x" + topics[1][-40:] if len(topics[1]) >= 42 else topics[1]
                                    to_addr = "0x" + topics[2][-40:] if len(topics[2]) >= 42 else topics[2]
                                    value_hex = log.get("data", "0x0")
                                    
                                    try:
                                        weth_amount = int(value_hex, 16) if value_hex != "0x0" else 0
                                    except (ValueError, TypeError):
                                        weth_amount = 0
                                    
                                    if weth_amount > 0:
                                        all_weth_transfers.append({
                                            "from": from_addr.lower(),
                                            "to": to_addr.lower(),
                                            "amount": weth_amount
                                        })
                                    
                                    logger.info(f"🔍 Strategy 0: WETH Transfer - from: {from_addr[:10]}..., to: {to_addr[:10]}..., amount: {weth_amount / (10**18):.6f}")
                                    
                                    # Check if this transfer is to the seller
                                    if seller_lower and to_addr.lower() == seller_lower:
                                        if weth_amount > 0:
                                            weth_total += weth_amount
                                            logger.info(f"✅ Strategy 0: Found WETH in same tx (from logs): {weth_amount / (10**18):.6f} WETH to seller {seller_lower[:10]}...")
                                    else:
                                        logger.debug(f"⚠️ Strategy 0: WETH transfer to_addr ({to_addr.lower()[:10]}...) does not match seller ({seller_lower[:10] if seller_lower else 'None'}...)")
                    
                    # If no WETH to seller found, use LARGEST WETH transfer as fallback
                    # This handles cases where seller uses different address for payment
                    if weth_total == 0 and all_weth_transfers:
                        # Sort by amount descending and use the largest
                        largest = max(all_weth_transfers, key=lambda x: x["amount"])
                        weth_total = largest["amount"]
                        logger.info(f"✅ Strategy 0 FALLBACK: Using largest WETH transfer: {weth_total / (10**18):.6f} WETH to {largest['to'][:10]}...")
                    
                    if weth_logs_found == 0:
                        logger.info(f"ℹ️ Strategy 0: No WETH contract logs found in transaction (checked {len(logs)} log(s))")
                    elif weth_total == 0:
                        logger.info(f"ℹ️ Strategy 0: Found {weth_logs_found} WETH contract log(s), but no matching transfers")
            except Exception as e:
                logger.error(f"❌ Strategy 0: Error checking transaction receipt for WETH: {e}", exc_info=True)
            
            if weth_total > 0:
                logger.info(f"✅ Found WETH transfer in same transaction: {weth_total / (10**18):.6f} WETH for tx {tx_hash[:16]}...")
                return (weth_total, True)
            
            # Strategy 1: If we have buyer and seller addresses, query WETH transfers directly by addresses
            # This is the most reliable method - query transfers from buyer to seller with wide block range
            if buyer_lower and seller_lower:
                logger.info(f"🔍 Strategy 1: Querying WETH transfers from buyer {buyer_lower[:10]}... to seller {seller_lower[:10]}... (direct address query)")
                # Use a very wide block range (±100 blocks) for direct address queries
                direct_from_block = max(0, block_num - 100)
                direct_to_block = block_num + 100
                
                direct_transfers = await self.get_asset_transfers(
                    contract_address=WETH_CONTRACT,
                    category=["erc20"],
                    from_address=buyer_lower,
                    to_address=seller_lower,
                    from_block=hex(direct_from_block),
                    to_block=hex(direct_to_block)
                )
                direct_list = direct_transfers.get("transfers", [])
                logger.info(f"🔍 Strategy 1: Found {len(direct_list)} WETH transfer(s) from buyer to seller in blocks {direct_from_block}-{direct_to_block}")
                
                # Add all direct transfers to the list
                for direct_transfer in direct_list:
                    direct_hash = direct_transfer.get("hash", "")
                    transfers_list.append(direct_transfer)
                    logger.debug(f"➕ Found WETH transfer: {direct_hash[:16]}... from {buyer_lower[:10]}... to {seller_lower[:10]}...")
                
                # Strategy 1b: If no direct buyer->seller WETH found, check for ANY WETH transfers from buyer
                # (WETH might go to marketplace/intermediary contract, not directly to seller)
                if len(direct_list) == 0:
                    logger.info(f"🔍 Strategy 1b: No direct buyer->seller WETH found, checking ANY WETH transfers from buyer {buyer_lower[:10]}...")
                    # Try a MUCH wider range - WETH might be transferred hours/days before the NFT sale
                    wide_from_block = max(0, block_num - 1000)  # 1000 blocks = ~3.3 hours
                    wide_to_block = block_num + 100
                    buyer_weth_transfers = await self.get_asset_transfers(
                        contract_address=WETH_CONTRACT,
                        category=["erc20"],
                        from_address=buyer_lower,
                        from_block=hex(wide_from_block),
                        to_block=hex(wide_to_block)
                    )
                    buyer_list = buyer_weth_transfers.get("transfers", [])
                    logger.info(f"🔍 Strategy 1b: Found {len(buyer_list)} WETH transfer(s) FROM buyer in blocks {wide_from_block}-{wide_to_block} (wide range)")
                    if buyer_list:
                        for transfer in buyer_list[:5]:  # Log first 5
                            transfer_to = transfer.get("to", "")
                            transfer_value = transfer.get("value", "0x0")
                            transfer_hash = transfer.get("hash", "")
                            transfer_block = transfer.get("blockNum", "")
                            try:
                                value_wei = int(transfer_value, 16) if transfer_value != "0x0" else 0
                                block_diff = ""
                                if transfer_block:
                                    try:
                                        tx_block = int(transfer_block, 16) if transfer_block.startswith("0x") else int(transfer_block)
                                        block_diff = f" (block diff: {block_num - tx_block})"
                                    except:
                                        pass
                                logger.info(f"🔍 Strategy 1b: WETH transfer from buyer to {transfer_to[:10]}...: {value_wei / (10**18):.6f} WETH (tx: {transfer_hash[:16]}...){block_diff}")
                                # If WETH goes to seller, count it (even if it's earlier)
                                if transfer_to.lower() == seller_lower:
                                    transfers_list.append(transfer)
                                    logger.info(f"✅ Strategy 1b: Found WETH transfer to seller!")
                            except Exception as e:
                                logger.debug(f"Strategy 1b: Error parsing transfer: {e}")
            
            # Strategy 2: Also check block range around the transaction (in case addresses don't match exactly)
            # Check from block-20 to block+20 to catch WETH transfers
            from_block = max(0, block_num - 20)
            to_block = block_num + 20
            
            logger.info(f"🔍 Strategy 2: Checking WETH transfers in blocks {from_block} to {to_block} (range: {to_block - from_block} blocks)")
            
            # Get ERC-20 transfers for this block range (WETH only)
            transfers = await self.get_asset_transfers(
                contract_address=WETH_CONTRACT,
                category=["erc20"],
                from_block=hex(from_block),
                to_block=hex(to_block)
            )
            
            block_range_list = transfers.get("transfers", [])
            logger.info(f"🔍 Found {len(block_range_list)} WETH transfer(s) in block range {from_block}-{to_block}")
            
            # Add transfers from block range (avoid duplicates)
            for transfer in block_range_list:
                transfer_hash = transfer.get("hash", "")
                # Only add if not already in transfers_list
                if not any(t.get("hash", "").lower() == transfer_hash.lower() for t in transfers_list):
                    transfers_list.append(transfer)
                    logger.debug(f"➕ Added WETH transfer from block range: {transfer_hash[:16]}...")
            
            logger.info(f"🔍 Total WETH transfers to check: {len(transfers_list)}")
            
            # Filter transfers - WETH payment goes TO the seller (seller receives payment)
            # Check both: same transaction hash OR matching addresses (WETH might be in different tx)
            for i, transfer in enumerate(transfers_list):
                logger.debug(f"🔍 WETH transfer {i+1}/{len(transfers_list)}: hash={transfer.get('hash', '')[:16]}..., from={transfer.get('from', '')[:10]}..., to={transfer.get('to', '')[:10]}...")
                transfer_hash = transfer.get("hash", "")
                transfer_from = transfer.get("from", "").lower()
                transfer_to = transfer.get("to", "").lower()
                transfer_block = transfer.get("blockNum", "")
                
                # Get WETH amount
                value_hex = transfer.get("value", "0x0")
                if value_hex and value_hex != "0x0":
                    try:
                        weth_amount = int(value_hex, 16)
                        
                        # Match by transaction hash first (most reliable)
                        if transfer_hash and transfer_hash.lower() == tx_hash.lower():
                            logger.debug(f"✅ WETH transfer matches tx hash: {transfer_hash[:16]}...")
                            if seller_lower and transfer_to == seller_lower:
                                weth_total += weth_amount
                                logger.info(f"✅ Found WETH in same tx: {weth_amount / (10**18):.6f} WETH to seller {seller_lower[:10]}...")
                            elif not seller_lower:
                                # No seller address, just sum all WETH transfers in this tx
                                weth_total += weth_amount
                                logger.info(f"✅ Found WETH in same tx (no seller check): {weth_amount / (10**18):.6f} WETH")
                            else:
                                logger.debug(f"⚠️ WETH in same tx but transfer_to ({transfer_to[:10]}...) != seller ({seller_lower[:10]}...)")
                        # Also check if WETH transfer involves the same addresses (might be different tx)
                        # WETH goes from buyer to seller
                        elif seller_lower and buyer_lower:
                            logger.debug(f"🔍 Checking address match: transfer_from={transfer_from[:10]}... (buyer={buyer_lower[:10]}...), transfer_to={transfer_to[:10]}... (seller={seller_lower[:10]}...)")
                            if transfer_from == buyer_lower and transfer_to == seller_lower:
                                # Check if transfer is in a nearby block (within 5 blocks)
                                if transfer_block:
                                    try:
                                        transfer_block_num = int(transfer_block, 16) if transfer_block.startswith("0x") else int(transfer_block)
                                        block_diff = abs(transfer_block_num - block_num)
                                        logger.debug(f"🔍 Transfer block {transfer_block_num}, NFT tx block {block_num}, diff: {block_diff}")
                                        if block_diff <= 5:
                                            weth_total += weth_amount
                                            logger.info(f"✅ Found WETH in nearby block {transfer_block_num} (diff: {block_diff}): {weth_amount / (10**18):.6f} WETH from buyer {buyer_lower[:10]}... to seller {seller_lower[:10]}...")
                                        else:
                                            logger.debug(f"⚠️ WETH transfer block {transfer_block_num} too far from NFT tx block {block_num} (diff: {block_diff} > 5)")
                                    except (ValueError, TypeError) as e:
                                        # If block parsing fails, still count it if addresses match
                                        logger.warning(f"⚠️ Could not parse transfer block '{transfer_block}': {e}, but addresses match - counting WETH")
                                        weth_total += weth_amount
                                        logger.info(f"✅ Found WETH (addresses match, block parse failed): {weth_amount / (10**18):.6f} WETH from buyer {buyer_lower[:10]}... to seller {seller_lower[:10]}...")
                                else:
                                    # No block info, but addresses match - count it
                                    logger.warning(f"⚠️ No block info for WETH transfer, but addresses match - counting it")
                                    weth_total += weth_amount
                                    logger.info(f"✅ Found WETH (addresses match, no block info): {weth_amount / (10**18):.6f} WETH from buyer {buyer_lower[:10]}... to seller {seller_lower[:10]}...")
                            else:
                                logger.debug(f"⚠️ Address mismatch: transfer_from ({transfer_from[:10]}...) != buyer ({buyer_lower[:10]}...) OR transfer_to ({transfer_to[:10]}...) != seller ({seller_lower[:10]}...)")
                        elif seller_lower and transfer_to == seller_lower:
                            # WETH goes to seller (no buyer check) - but only if in nearby block
                            if transfer_block:
                                try:
                                    transfer_block_num = int(transfer_block, 16) if transfer_block.startswith("0x") else int(transfer_block)
                                    if abs(transfer_block_num - block_num) <= 5:
                                        weth_total += weth_amount
                                        logger.info(f"✅ Found WETH to seller in block {transfer_block_num}: {weth_amount / (10**18):.6f} WETH to {seller_lower[:10]}...")
                                except (ValueError, TypeError):
                                    pass
                    except (ValueError, TypeError):
                        pass
            
            if weth_total > 0:
                logger.info(f"✅ Found WETH transfer: {weth_total / (10**18):.6f} WETH for tx {tx_hash[:16]}...")
                return (weth_total, True)
            
            logger.debug(f"❌ No WETH transfers found for tx {tx_hash[:16]}... (checked {len(transfers_list)} transfer(s) in blocks {from_block}-{to_block})")
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
                # We'll collect all URLs and pick the best one (prefer Alchemy CDN over Cloudinary)
                image_url = None
                cloudinary_url = None  # Store Cloudinary URL as fallback only
                
                # FIRST: Check top-level image (where cachedUrl usually exists)
                # We check this FIRST because it's the most reliable source
                top_image = result.get("image")
                if isinstance(top_image, dict):
                    cached_url = top_image.get("cachedUrl")
                    original_url = top_image.get("originalUrl")
                    png_url = top_image.get("pngUrl")
                    thumbnail_url = top_image.get("thumbnailUrl")
                    content_type = top_image.get("contentType", "")
                    
                    # Check if originalUrl indicates it's a video (to know if cachedUrl is also video)
                    is_video = False
                    if original_url and isinstance(original_url, str):
                        is_video = any(ext in original_url.lower() for ext in ['.mp4', '.webm', '.mov', '.avi', 'video'])
                    if content_type and "video" in content_type.lower():
                        is_video = True
                    
                    logger.info(f"🔍 Checking top-level image FIRST (most reliable source):")
                    logger.info(f"🔍   cachedUrl: {repr(cached_url)} (type: {type(cached_url).__name__})")
                    logger.info(f"🔍   originalUrl: {original_url[:100] if original_url else 'None'}...")
                    logger.info(f"🔍   pngUrl: {png_url[:100] if png_url else 'None'}...")
                    logger.info(f"🔍   thumbnailUrl: {thumbnail_url[:100] if thumbnail_url else 'None'}...")
                    
                    # Helper function to check if URL is a video
                    def is_video_url(url: str) -> bool:
                        if not url:
                            return False
                        url_lower = url.lower()
                        return any(ext in url_lower for ext in ['.mp4', '.webm', '.mov', '.avi', 'video'])
                    
                    # Check cachedUrl - but skip if it's a video file
                    if cached_url and isinstance(cached_url, str) and cached_url.strip():
                        if is_video or is_video_url(cached_url):
                            logger.warning(f"⚠️ cachedUrl is a video file (detected from originalUrl/contentType), skipping: {cached_url[:80]}...")
                            logger.info(f"⚠️ Will look for PNG/thumbnail instead for still image")
                            # Don't use video URL - look for thumbnail/preview instead
                        else:
                            image_url = cached_url.strip()
                            logger.info(f"✅ FOUND cachedUrl in top-level image (Alchemy CDN): {image_url}")
                    # Check originalUrl - but skip if it's a video file
                    elif original_url and isinstance(original_url, str) and original_url.strip():
                        if is_video_url(original_url):
                            logger.warning(f"⚠️ originalUrl is a video file, skipping: {original_url[:80]}...")
                            # Don't use video URL - look for thumbnail/preview instead
                        elif "nft-cdn.alchemy.com" in original_url:
                            image_url = original_url.strip()
                            logger.info(f"✅ FOUND originalUrl in top-level image (Alchemy CDN): {image_url[:80]}...")
                        else:
                            # Store as potential fallback (only if not video)
                            if not image_url:
                                image_url = original_url.strip()
                                logger.info(f"Found originalUrl in top-level (not Alchemy CDN): {image_url[:80]}...")
                    
                    # If we don't have an image yet (or skipped video URLs), prefer thumbnail over PNG
                    # Thumbnails are usually smaller and more reliable than full PNG conversions
                    if not image_url:
                        if thumbnail_url and isinstance(thumbnail_url, str) and thumbnail_url.strip():
                            # Thumbnail URLs are usually still images, not videos, and are smaller/more reliable
                            image_url = thumbnail_url.strip()
                            logger.info(f"✅ FOUND thumbnailUrl in top-level (using as image - preferred over PNG): {image_url[:60]}...")
                        elif png_url and isinstance(png_url, str) and png_url.strip():
                            # PNG URLs are usually still images, not videos
                            image_url = png_url.strip()
                            logger.info(f"✅ FOUND pngUrl in top-level (using as image): {image_url[:60]}...")
                        else:
                            # Store Cloudinary URLs as fallback if we still don't have anything
                            if png_url and isinstance(png_url, str) and png_url.strip():
                                cloudinary_url = png_url.strip()
                                logger.info(f"Found Cloudinary PNG in top-level (will use as fallback): {cloudinary_url[:60]}...")
                            elif thumbnail_url and isinstance(thumbnail_url, str) and thumbnail_url.strip():
                                if not cloudinary_url:
                                    cloudinary_url = thumbnail_url.strip()
                                    logger.info(f"Found Cloudinary thumbnail in top-level (will use as fallback): {cloudinary_url[:60]}...")
                
                # If we found Alchemy CDN URL in top-level, use it and skip other sources
                if image_url and "nft-cdn.alchemy.com" in image_url:
                    logger.info(f"✅ Using Alchemy CDN URL from top-level image, skipping other sources")
                else:
                    # Continue checking other sources if we didn't find Alchemy CDN URL
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
                            
                            # For embed images, prefer cachedUrl (Alchemy CDN) over Cloudinary URLs
                            # Cloudinary URLs often return 400 errors, while Alchemy CDN works reliably
                            # Note: cachedUrl may be large (>8MB) but works fine for Discord embeds
                            gateway_cached = gateway_dict.get("cachedUrl")
                            gateway_png = gateway_dict.get("pngUrl")
                            gateway_thumb = gateway_dict.get("thumbnailUrl")
                            gateway_original = gateway_dict.get("originalUrl")
                            
                            logger.info(f"🔍 Gateway dict URLs - cachedUrl: {bool(gateway_cached)}, pngUrl: {bool(gateway_png)}, thumbnailUrl: {bool(gateway_thumb)}, originalUrl: {bool(gateway_original)}")
                            
                            if gateway_cached and isinstance(gateway_cached, str) and gateway_cached.strip():
                                image_url = gateway_cached.strip()
                                logger.info(f"✅ SELECTED: cached URL from gateway (Alchemy CDN): {image_url}")
                            elif gateway_original and isinstance(gateway_original, str) and gateway_original.strip():
                                # Check if originalUrl is Alchemy CDN
                                if "nft-cdn.alchemy.com" in gateway_original:
                                    image_url = gateway_original.strip()
                                    logger.info(f"✅ SELECTED: original URL from gateway (Alchemy CDN): {image_url[:60]}...")
                                else:
                                    image_url = gateway_original.strip()
                                    logger.info(f"SELECTED: original URL from gateway: {image_url[:60]}...")
                            elif gateway_png and isinstance(gateway_png, str) and gateway_png.strip():
                                # Store Cloudinary URL as fallback, but continue checking for better URLs
                                cloudinary_url = gateway_png.strip()
                                logger.info(f"Found Cloudinary PNG URL in gateway (will use as fallback if no better URL found): {cloudinary_url[:60]}...")
                                # Don't set image_url yet - continue checking other sources
                            elif gateway_thumb and isinstance(gateway_thumb, str) and gateway_thumb.strip():
                                # Store Cloudinary URL as fallback, but continue checking for better URLs
                                if not cloudinary_url:  # Only use thumbnail if we don't have PNG
                                    cloudinary_url = gateway_thumb.strip()
                                    logger.info(f"Found Cloudinary thumbnail URL in gateway (will use as fallback if no better URL found): {cloudinary_url[:60]}...")
                                # Don't set image_url yet - continue checking other sources
                            elif is_video:
                                # For videos without cached URL, log warning
                                logger.warning(f"Video detected but no cached URL available. Available keys: {list(gateway_dict.keys())}")
                                image_url = gateway_original if gateway_original else None
                            else:
                                # Last resort: originalUrl
                                image_url = gateway_original if gateway_original else None
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
                                
                                # Prefer cachedUrl (Alchemy CDN) over Cloudinary URLs for embeds
                                if raw_item.get("cachedUrl"):
                                    image_url = raw_item.get("cachedUrl")
                                    logger.info(f"Using cached URL from raw (Alchemy CDN): {image_url[:60]}...")
                                elif raw_item.get("pngUrl"):
                                    image_url = raw_item.get("pngUrl")
                                    logger.info(f"Using PNG URL from raw (Cloudinary): {image_url[:60]}...")
                                elif raw_item.get("thumbnailUrl"):
                                    image_url = raw_item.get("thumbnailUrl")
                                    logger.info(f"Using thumbnail URL from raw (Cloudinary): {image_url[:60]}...")
                                elif is_video:
                                    logger.warning("Video in raw but no cached URL available")
                                    image_url = raw_item.get("originalUrl")
                                else:
                                    image_url = raw_item.get("originalUrl")
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
                                # Prefer cachedUrl for embeds
                                if meta_image.get("cachedUrl"):
                                    image_url = meta_image.get("cachedUrl")
                                    logger.info(f"Using cached URL from metadata.image (Alchemy CDN): {image_url[:80]}...")
                                elif meta_image.get("pngUrl"):
                                    image_url = meta_image.get("pngUrl")
                                    logger.info(f"Using PNG URL from metadata.image (Cloudinary): {image_url[:80]}...")
                                elif meta_image.get("thumbnailUrl"):
                                    image_url = meta_image.get("thumbnailUrl")
                                    logger.info(f"Using thumbnail URL from metadata.image (Cloudinary): {image_url[:80]}...")
                                else:
                                    image_url = meta_image.get("originalUrl")
                            else:
                                image_url = meta_image
                    
                    # Final fallback: Use Cloudinary URL only if we have nothing else
                    if not image_url and cloudinary_url:
                        image_url = cloudinary_url
                        logger.warning(f"⚠️  FINAL FALLBACK: Using Cloudinary URL (may return 400): {image_url[:60]}...")
                        logger.warning(f"⚠️  WARNING: No Alchemy CDN URL found, using Cloudinary which may fail!")
                
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
    
    def _extract_ipfs_hash_from_video_url(self, video_url: str) -> Optional[Tuple[str, str]]:
        """
        Extract IPFS hash and token ID from video URL pattern like:
        https://ipfs.io/ipfs/QmXNofSXgZNVTnu1jdaFHM42M4BM4Nnv8Srv7Zat4ueAPa/2665.mp4
        
        Returns:
            Tuple of (ipfs_hash, token_id) or None if not found
        """
        if not video_url or not isinstance(video_url, str):
            return None
        
        # Pattern: .../ipfs/HASH/TOKEN_ID.mp4
        if '/ipfs/' in video_url:
            parts = video_url.split('/ipfs/')
            if len(parts) > 1:
                rest = parts[1]
                # Split by / to get hash and filename
                hash_and_file = rest.split('/')
                if len(hash_and_file) >= 2:
                    ipfs_hash = hash_and_file[0].split('?')[0].split('#')[0]  # Remove query params
                    filename = hash_and_file[1].split('?')[0].split('#')[0]  # Remove query params
                    # Extract token ID from filename (e.g., "2665.mp4" -> "2665")
                    if '.' in filename:
                        token_id = filename.split('.')[0]
                        if ipfs_hash and token_id:
                            return (ipfs_hash, token_id)
        return None
    
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
    
    async def get_ipfs_image_urls(self, token_id: str, timeout: float = 5.0) -> List[str]:
        """
        Get IPFS image URLs for a token with timeout protection.
        
        This is a wrapper around _get_ipfs_image_urls_internal that adds
        timeout handling to prevent hanging on slow IPFS gateways.
        
        Args:
            token_id: Token ID to get IPFS URLs for
            timeout: Maximum seconds to wait (default 5.0)
            
        Returns:
            List of IPFS image URLs, or empty list if timeout/error
        """
        try:
            return await asyncio.wait_for(
                self._get_ipfs_image_urls_internal(token_id),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.debug(f"IPFS fetch timed out for token {token_id} after {timeout}s")
            return []
        except Exception as e:
            logger.debug(f"Error fetching IPFS URLs for token {token_id}: {e}")
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
            
            # For Cloudinary URLs, add specific headers and handle redirects
            if 'cloudinary.com' in image_url:
                logger.info(f"📥 Downloading from Cloudinary: {image_url[:100]}...")
                # Cloudinary URLs may need specific headers
                headers['Referer'] = 'https://alchemy.com/'
                headers['Origin'] = 'https://alchemy.com/'
            
            async with session.get(
                image_url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=20),  # Longer timeout for Cloudinary
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
                    
                    # Check if it's actually a video file (we don't want videos)
                    if 'video' in content_type.lower():
                        logger.warning(f"⚠️ URL returned video content (Content-Type: {content_type}), skipping")
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
                    # For Cloudinary 400 errors, skip this URL - it's likely malformed
                    # Cloudinary URLs often fail with 400, so we'll try other URLs
                    if 'cloudinary.com' in image_url:
                        logger.debug(f"Skipping Cloudinary URL (HTTP 400): {image_url[:100]}...")
                    else:
                        logger.warning(f"HTTP 400 from {image_url[:60]}... - URL might be malformed")
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
    
    async def extract_video_frame(self, video_url: str, token_id: str) -> Optional[bytes]:
        """
        Download video from IPFS and extract first frame as PNG image.
        This is the most reliable way to get a thumbnail from video NFTs.
        
        Args:
            video_url: IPFS video URL (e.g., https://ipfs.io/ipfs/HASH/TOKEN_ID.mp4)
            token_id: Token ID for logging
            
        Returns:
            PNG image bytes, or None if extraction fails
        """
        temp_video_path: Optional[str] = None
        temp_image_path: Optional[str] = None
        
        try:
            import imageio
            import tempfile
            
            logger.info(f"🎬 Extracting frame from video: {video_url[:80]}...")
            
            # Download video to temporary file
            session = await self._get_session()
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            }
            
            async with session.get(
                video_url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)  # Videos can be large
            ) as response:
                if response.status != 200:
                    logger.warning(f"Failed to download video: HTTP {response.status}")
                    return None
                
                video_data = await response.read()
                # Limit video size to 50MB to avoid memory issues
                if len(video_data) > 50 * 1024 * 1024:
                    logger.warning(f"Video too large ({len(video_data)} bytes), skipping frame extraction")
                    return None
                
                # Write video to temp file
                with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as temp_video:
                    temp_video.write(video_data)
                    temp_video_path = temp_video.name
            
            # Extract first frame using imageio
            reader = imageio.get_reader(temp_video_path)
            frame = reader.get_data(0)  # Get first frame
            reader.close()
            
            # Convert frame to PNG bytes
            with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as temp_image:
                temp_image_path = temp_image.name
                imageio.imwrite(temp_image_path, frame, format='PNG')
            
            with open(temp_image_path, 'rb') as f:
                image_bytes = f.read()
            
            logger.info(f"✅ Successfully extracted frame: {len(image_bytes)} bytes")
            return image_bytes
                        
        except ImportError:
            logger.error("imageio not installed - cannot extract video frames. Install with: pip install imageio imageio-ffmpeg")
            return None
        except Exception as e:
            logger.error(f"Error in extract_video_frame: {e}", exc_info=True)
            return None
        finally:
            # Clean up temp files
            if temp_video_path:
                try:
                    os.unlink(temp_video_path)
                except OSError:
                    pass
            if temp_image_path:
                try:
                    os.unlink(temp_image_path)
                except OSError:
                    pass
    
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
                    
                    # Get transaction index for better sorting (within same block)
                    tx_index_hex = transfer.get("transactionIndex", "0x0")
                    tx_index = 0
                    if tx_index_hex and tx_index_hex != "0x0":
                        try:
                            tx_index = int(tx_index_hex, 16)
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
                        "block_number": block_number,
                        "transaction_index": tx_index
                    })
                
                # Check prices for all candidates in parallel (batch)
                if transfer_candidates:
                    price_tasks = [
                        self._get_transaction_price_simple(candidate["tx_hash"], candidate["from_addr"], candidate["to_addr"])
                        for candidate in transfer_candidates
                    ]
                    price_results = await asyncio.gather(*price_tasks, return_exceptions=True)
                    
                    # Create sales only for transfers with prices
                    for candidate, price_result in zip(transfer_candidates, price_results):
                        if isinstance(price_result, Exception):
                            logger.debug(f"Price check failed for {candidate['tx_hash'][:10]}...: {price_result}")
                            continue
                        
                        price, is_weth = price_result
                        if price > 0:
                            logger.debug(f"Transfer {candidate['tx_hash'][:10]}... - Price: {price} wei ({'WETH' if is_weth else 'ETH'})")
                        
                        if price == 0:
                            # Log this at INFO level so we can see if WETH sales are being filtered out
                            logger.info(f"⚠️ Skipping transfer {candidate['tx_hash'][:10]}... - no price detected (might be WETH sale that failed detection)")
                            logger.info(f"⚠️   Seller: {candidate['from_addr'][:10] if candidate.get('from_addr') else 'None'}..., Buyer: {candidate['to_addr'][:10] if candidate.get('to_addr') else 'None'}...")
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
                        # Also store transaction index for better sorting (WETH sales might be in same block)
                        sale._tx_index = candidate.get("transaction_index", 0)
                        sales.append(sale)
                
                # After processing this chunk, sort and check
                # Sort by block number first, then by transaction index (most recent first)
                if sales:
                    sales.sort(key=lambda x: (getattr(x, '_block_number', 0), getattr(x, '_tx_index', 0)), reverse=True)
                    
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
            # Sort by block number first, then by transaction index (most recent first)
            sales.sort(key=lambda x: (getattr(x, '_block_number', 0), getattr(x, '_tx_index', 0)), reverse=True)
            
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

