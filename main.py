import discord
from discord.ext import commands
import os
import logging
from dotenv import load_dotenv

# 1. Load the secret variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# ==========================================
# 2. CONFIGURE LOGGING (CRITICAL)
# This allows you to see background cron jobs and scraper errors in your console.
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("GhostCommander")

# 3. Setup Bot Intents (Permissions)
intents = discord.Intents.default()
intents.message_content = True  # Allows the bot to read normal text messages


# 4. Create the Main Bot Class
class GhostCommander(commands.Bot):
    def __init__(self):
        # We use a standard prefix '!', but we will primarily use Slash Commands (/)
        super().__init__(command_prefix="!", intents=intents)

    # The setup_hook is the modern way to load Cogs before the bot fully starts
    async def setup_hook(self):
        logger.info("⚙️ Initializing Cognitive Modules (Cogs)...")

        # Dynamically load all .py files in the /cogs folder
        for filename in os.listdir("./cogs"):
            if filename.endswith(".py") and not filename.startswith("__"):
                cog_name = filename[:-3]
                try:
                    await self.load_extension(f"cogs.{cog_name}")
                    logger.info(f"✅ Successfully Loaded: {cog_name}")
                except Exception as e:
                    logger.error(f"❌ Failed to load {cog_name}: {e}")

        # Sync the slash commands to Discord safely
        logger.info("🔄 Syncing Slash Commands with Discord Matrix...")
        try:
            synced = await self.tree.sync()
            logger.info(f"✅ {len(synced)} Global Commands Synced!")
        except Exception as e:
            logger.error(f"❌ Failed to sync commands (Rate limit or API error): {e}")

    # This triggers when the bot successfully connects to Discord
    async def on_ready(self):
        print(f"\n🌐 Logged in as {self.user} (ID: {self.user.id})")
        print("=====================================")
        print("    GHOST COMMANDER IS ONLINE        ")
        print("=====================================\n")


# 5. Run the Bot
bot = GhostCommander()

if __name__ == "__main__":
    if not TOKEN:
        logger.critical("❌ CRITICAL ERROR: DISCORD_TOKEN not found in .env file.")
    else:
        # log_handler=None prevents discord.py from overriding our custom logging format
        bot.run(TOKEN, log_handler=None)
