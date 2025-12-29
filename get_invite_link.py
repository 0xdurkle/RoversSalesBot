#!/usr/bin/env python3
"""
Generate Discord bot invite link.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Get Client ID from environment or user input
client_id = os.getenv('DISCORD_CLIENT_ID')

if not client_id:
    print("=" * 60)
    print("Discord Bot Invite Link Generator")
    print("=" * 60)
    print("\nTo get your Client ID:")
    print("1. Go to: https://discord.com/developers/applications")
    print("2. Select your bot application")
    print("3. Go to 'General Information' tab")
    print("4. Copy the 'Application ID' (this is your Client ID)")
    print("\n" + "=" * 60)
    client_id = input("\nEnter your Client ID: ").strip()

if client_id:
    # Required permissions for image display:
    # - Send Messages (2048)
    # - Embed Links (16384) - Required for embed images
    # - Attach Files (32768) - Required for file attachments (optional but recommended)
    # - Read Message History (65536)
    # - Use Slash Commands (2147483648)
    permissions = 2048 + 16384 + 32768 + 65536 + 2147483648
    
    invite_url = f"https://discord.com/api/oauth2/authorize?client_id={client_id}&permissions={permissions}&scope=bot%20applications.commands"
    
    print("\n" + "=" * 60)
    print("Your Bot Invite Link:")
    print("=" * 60)
    print(f"\n{invite_url}\n")
    print("=" * 60)
    print("\nClick the link above to invite the bot to your server!")
    print("Make sure to select the server and authorize the bot.")
    print("=" * 60)
else:
    print("No Client ID provided. Please run the script again with your Client ID.")

