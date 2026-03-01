import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import time, timezone
import logging

from utils.ghost_scraper import sweep_jobs
from utils.notion_jobs import check_job_exists, add_job_to_notion

logger = logging.getLogger("GhostCommander")
JOB_ALERT_CHANNEL_ID = int(os.getenv("JOB_ALERT_CHANNEL_ID", 0))


class JobHunterCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.midnight_sweep.start()

    def cog_unload(self):
        self.midnight_sweep.cancel()

    async def execute_hunt(
        self, channel: discord.TextChannel, role: str, location: str
    ):
        """Core logic to scrape, deduplicate, and silently push to Notion."""
        jobs = await sweep_jobs(role, location)

        if not jobs:
            return await channel.send(
                f"🛑 No new entry-level ATS listings found for **{role}** in **{location}**."
            )

        new_jobs_count = 0
        for job in jobs:
            # Step 1: Deduplication Check
            if await check_job_exists(job["link"]):
                continue

            # Step 2: Inject to Notion directly
            page_id = await add_job_to_notion(job["title"], job["company"], job["link"])
            if page_id:
                new_jobs_count += 1

        # Step 3: Single Summary Broadcast
        if new_jobs_count == 0:
            await channel.send(
                f"✅ Sweep complete. All discovered entry-level targets for `{role}` are already mapped in your Notion matrix."
            )
        else:
            embed = discord.Embed(
                title="✅ Pipeline Updated",
                description=f"Successfully injected **{new_jobs_count}** new entry-level opportunities into your Notion database.",
                color=0x2ECC71,
            )
            embed.set_footer(text="Check your Notion Job Listings to review and apply.")
            await channel.send(embed=embed)

    @app_commands.command(
        name="hunt", description="Manually trigger a targeted, filtered ATS job sweep."
    )
    async def hunt(self, interaction: discord.Interaction, role: str, location: str):
        await interaction.response.send_message(
            f"🕵️‍♂️ **Initiating Stealth Sweep:** Scanning ATS boards and filtering for entry-level `{role}` roles in `{location}`...",
        )
        await self.execute_hunt(interaction.channel, role, location)

    # ==========================================
    # SCHEDULED TRIGGER: 12:00 AM IST (18:30 UTC)
    # ==========================================
    utc_midnight = time(hour=18, minute=30, tzinfo=timezone.utc)

    @tasks.loop(time=utc_midnight)
    async def midnight_sweep(self):
        channel = self.bot.get_channel(JOB_ALERT_CHANNEL_ID)
        if not channel:
            return

        role = "Data Scientist OR Software Engineer"
        location = "Remote OR India"

        await channel.send(
            f"🌌 **Midnight Protocol Initiated:** Scanning for `{role}`..."
        )
        await self.execute_hunt(channel, role, location)

    @midnight_sweep.before_loop
    async def before_midnight_sweep(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(JobHunterCog(bot))
