import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

# 1. Load the secret variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# 2. Setup Bot Intents (Permissions)
intents = discord.Intents.default()
intents.message_content = True  # Allows the bot to read normal text messages


# 3. Create the Main Bot Class
class GhostCommander(commands.Bot):
    def __init__(self):
        # We use a standard prefix '!', but we will primarily use Slash Commands (/)
        super().__init__(command_prefix="!", intents=intents)

    # The setup_hook is the modern way to load Cogs before the bot fully starts
    async def setup_hook(self):
        print("⚙️ Loading Cogs...")

        # Look inside the /cogs folder and load every .py file
        for filename in os.listdir("./cogs"):
            if filename.endswith(".py"):
                try:
                    await self.load_extension(f"cogs.{filename[:-3]}")
                    print(f"  ✅ Loaded: {filename}")
                except Exception as e:
                    print(f"  ❌ Failed to load {filename}: {e}")

        # Sync the slash commands to Discord
        print("🔄 Syncing Slash Commands...")
        await self.tree.sync()
        print("  ✅ Commands synced!")

    # This triggers when the bot successfully connects to Discord
    async def on_ready(self):
        print(f"\n🌐 Logged in as {self.user} (ID: {self.user.id})")
        print("=====================================")
        print("    GHOST COMMANDER IS ONLINE        ")
        print("=====================================\n")


# 4. Run the Bot
bot = GhostCommander()

if __name__ == "__main__":
    if not TOKEN:
        print("❌ CRITICAL ERROR: DISCORD_TOKEN not found in .env file.")
    else:
        bot.run(TOKEN)
