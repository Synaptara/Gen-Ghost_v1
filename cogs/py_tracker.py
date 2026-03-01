import os
import logging
import discord
from discord.ext import tasks, commands
from discord import app_commands
from datetime import datetime, timezone, time
import asyncio

from utils.notion_api import (
    add_task,
    get_next_pending_task,
    update_task_status,
    update_task_completion,
    get_progress_stats,
)

logger = logging.getLogger("ghost_tracker")

FEED_CHANNEL_ID = int(os.getenv("FEED_CHANNEL_ID", 0))
TRACKER_CHANNEL_ID = int(os.getenv("TRACKER_CHANNEL_ID", 0))

# ==========================================
# CUSTOM EMOJI CONFIGURATION
# ==========================================
EMOJI_1_READY = "✅"
EMOJI_2_TIME = "⏰"
EMOJI_3_START = "🚀"
EMOJI_4_ROLL = "🔄"
EMOJI_5_CANCEL = "❌"
EMOJI_6_COMPLETE = "🟢"
EMOJI_7_PAUSE = "⏸️"
EMOJI_8_RESUME = "▶️"
# ==========================================


class PausedView(discord.ui.View):
    def __init__(self, task_data, accumulated_mins):
        super().__init__(timeout=None)
        self.task_data = task_data
        self.accumulated_mins = accumulated_mins

    @discord.ui.button(
        label="Resume Session", style=discord.ButtonStyle.primary, emoji=EMOJI_8_RESUME
    )
    async def resume_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.defer()
        start_time = datetime.now(timezone.utc)
        timestamp = int(start_time.timestamp())

        embed = discord.Embed(title="🔴 Live Session Resumed", color=0x111111)
        embed.add_field(name="Module", value=f"`{self.task_data['day']}`", inline=True)
        embed.add_field(
            name="Subject", value=f"**{self.task_data['topic']}**", inline=False
        )
        embed.add_field(
            name="Telemetry",
            value=f"Time banked: `{self.accumulated_mins} mins`\nResumed: <t:{timestamp}:R>",
            inline=False,
        )

        await interaction.edit_original_response(
            embed=embed,
            view=SessionView(self.task_data, start_time, self.accumulated_mins),
        )


class ActiveSessionDropdown(discord.ui.Select):
    def __init__(self, task_data, start_time, accumulated_mins):
        self.task_data = task_data
        self.start_time = start_time
        self.accumulated_mins = accumulated_mins

        options = [
            discord.SelectOption(
                label="Mark Completed",
                description="Log final time and finish module",
                emoji=EMOJI_6_COMPLETE,
                value="complete",
            ),
            discord.SelectOption(
                label="Pause Session",
                description="Temporarily halt the tracker",
                emoji=EMOJI_7_PAUSE,
                value="pause",
            ),
            discord.SelectOption(
                label="Cancel Session",
                description="Abort module without logging time",
                emoji=EMOJI_5_CANCEL,
                value="cancel",
            ),
        ]
        super().__init__(
            placeholder="Manage active session...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        choice = self.values[0]

        end_time = datetime.now(timezone.utc)
        current_sprint_mins = int((end_time - self.start_time).total_seconds() / 60)
        total_duration = self.accumulated_mins + current_sprint_mins

        if choice == "complete":
            success = await update_task_completion(
                self.task_data["page_id"], total_duration
            )
            if success:
                embed = discord.Embed(title="🟢 Module Terminated", color=0x2ECC71)
                embed.add_field(
                    name="Status", value="`Successfully Logged to Notion`", inline=False
                )
                embed.add_field(
                    name="Time Invested",
                    value=f"`{total_duration} minutes`",
                    inline=False,
                )
                await interaction.edit_original_response(embed=embed, view=None)

                await interaction.followup.send(
                    embed=discord.Embed(
                        description="Excellent work, sir. Do you wish to initialize the next module?",
                        color=0x111111,
                    ),
                    view=NextTaskPromptView(),
                )
            else:
                await interaction.followup.send(
                    "❌ Error syncing completion data to Notion.", ephemeral=True
                )

        elif choice == "pause":
            embed = discord.Embed(title="⏸️ Session Paused", color=0xE67E22)
            embed.add_field(
                name="Module", value=f"`{self.task_data['day']}`", inline=True
            )
            embed.add_field(
                name="Time Banked", value=f"`{total_duration} minutes`", inline=True
            )
            await interaction.edit_original_response(
                embed=embed, view=PausedView(self.task_data, total_duration)
            )

        elif choice == "cancel":
            await update_task_status(self.task_data["page_id"], "Skipped")
            embed = discord.Embed(title="❌ Session Aborted", color=0xE74C3C)
            embed.add_field(
                name="Result",
                value="Task marked as Skipped. Time discarded.",
                inline=False,
            )
            await interaction.edit_original_response(embed=embed, view=None)


class SessionView(discord.ui.View):
    def __init__(self, task_data, start_time, accumulated_mins=0):
        super().__init__(timeout=None)
        self.add_item(ActiveSessionDropdown(task_data, start_time, accumulated_mins))


class TaskActionDropdown(discord.ui.Select):
    def __init__(self, task_data):
        self.task_data = task_data
        options = [
            discord.SelectOption(
                label="Start Now",
                description="Initiate active learning session",
                emoji=EMOJI_3_START,
                value="start",
            ),
            discord.SelectOption(
                label="Roll Another Task",
                description="Skip this topic for later",
                emoji=EMOJI_4_ROLL,
                value="roll",
            ),
            discord.SelectOption(
                label="Cancel for Today",
                description="Abort session entirely",
                emoji=EMOJI_5_CANCEL,
                value="cancel",
            ),
        ]
        super().__init__(
            placeholder="Select strategic action...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        choice = self.values[0]

        if choice == "start":
            await update_task_status(self.task_data["page_id"], "In-Progress")
            start_time = datetime.now(timezone.utc)
            timestamp = int(start_time.timestamp())

            embed = discord.Embed(title="🔴 Live Session Active", color=0x111111)
            embed.add_field(
                name="Module", value=f"`{self.task_data['day']}`", inline=True
            )
            embed.add_field(
                name="Subject", value=f"**{self.task_data['topic']}**", inline=False
            )
            embed.add_field(
                name="Telemetry", value=f"Started: <t:{timestamp}:R>", inline=False
            )

            await interaction.edit_original_response(
                embed=embed,
                view=SessionView(self.task_data, start_time, accumulated_mins=0),
            )

        elif choice == "roll":
            await update_task_status(self.task_data["page_id"], "Pending")
            embed = discord.Embed(
                description=f"{EMOJI_4_ROLL} Task bypassed. Pulling next module from the matrix...",
                color=0xE67E22,
            )
            await interaction.edit_original_response(embed=embed, view=None)
            await dispatch_daily_tracker(interaction.client)

        elif choice == "cancel":
            await update_task_status(self.task_data["page_id"], "Skipped")
            embed = discord.Embed(
                description=f"{EMOJI_5_CANCEL} Operations aborted for today. Status logged.",
                color=0xE74C3C,
            )
            await interaction.edit_original_response(embed=embed, view=None)


class TrackerMainView(discord.ui.View):
    def __init__(self, task_data):
        super().__init__(timeout=None)
        self.add_item(TaskActionDropdown(task_data))


class NextTaskPromptView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Load Next Task", style=discord.ButtonStyle.primary)
    async def load_next(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.defer()
        await interaction.delete_original_response()
        await dispatch_daily_tracker(interaction.client)

    @discord.ui.button(label="End Operations", style=discord.ButtonStyle.secondary)
    async def end_ops(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.edit_message(
            content="System shutting down. See you tomorrow, sir.",
            view=None,
            embed=None,
        )


class ReadyButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Yes, I am Ready",
        style=discord.ButtonStyle.success,
        custom_id="ready_btn",
        emoji=EMOJI_1_READY,
    )
    async def ready_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.defer()

        task_data = await get_next_pending_task()
        if not task_data:
            embed = discord.Embed(
                description="No pending tasks found in Notion. Curriculum complete.",
                color=0x111111,
            )
            await interaction.edit_original_response(embed=embed, view=None)
            return

        embed = discord.Embed(title="⚡ Module Authorization", color=0x111111)
        embed.add_field(
            name="Target Module", value=f"`{task_data['day']}`", inline=True
        )
        embed.add_field(name="Current Status", value="`Pending`", inline=True)
        embed.add_field(
            name="Subject Matter", value=f"**{task_data['topic']}**", inline=False
        )

        await interaction.edit_original_response(
            embed=embed, view=TrackerMainView(task_data)
        )

    @discord.ui.button(
        label="Remind in 1 Hr", style=discord.ButtonStyle.secondary, emoji=EMOJI_2_TIME
    )
    async def snooze_1hr(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.defer()
        embed = discord.Embed(
            description=f"{EMOJI_2_TIME} System snoozed. Will resume ping in 60 minutes.",
            color=0x3498DB,
        )
        await interaction.edit_original_response(embed=embed, view=None)
        await asyncio.sleep(3600)
        await dispatch_daily_tracker(interaction.client)

    @discord.ui.button(
        label="Remind in 2 Hrs", style=discord.ButtonStyle.secondary, emoji=EMOJI_2_TIME
    )
    async def snooze_2hr(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.defer()
        embed = discord.Embed(
            description=f"{EMOJI_2_TIME} System snoozed. Will resume ping in 120 minutes.",
            color=0x3498DB,
        )
        await interaction.edit_original_response(embed=embed, view=None)
        await asyncio.sleep(7200)
        await dispatch_daily_tracker(interaction.client)


async def dispatch_daily_tracker(bot):
    try:
        channel = await bot.fetch_channel(TRACKER_CHANNEL_ID)
    except discord.NotFound:
        logger.error(
            f"Tracker channel not found. Check TRACKER_CHANNEL_ID in .env: {TRACKER_CHANNEL_ID}"
        )
        return
    except discord.Forbidden:
        logger.error("Bot does not have permission to view the Tracker channel.")
        return

    task_data = await get_next_pending_task()

    if not task_data:
        logger.info("Notion curriculum is empty. Skipping tracker ping.")
        return

    embed = discord.Embed(
        title="🛡️ System Alert: Daily Protocol",
        description=f"Good evening, sir. **{task_data['day']}** is queued and awaiting your authorization.",
        color=0x111111,
    )
    await channel.send(embed=embed, view=ReadyButtonView())


class PyTrackerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.daily_ping.start()
        self.notion_watcher.start()

        self.last_pending_id = None
        self.watcher_initialized = False

    def cog_unload(self):
        self.daily_ping.cancel()
        self.notion_watcher.cancel()

    @tasks.loop(minutes=1)
    async def notion_watcher(self):
        """Silently polls Notion to detect new feeds if the queue was empty."""
        try:
            task_data = await get_next_pending_task()
            current_id = task_data["page_id"] if task_data else None

            # Ignore the very first tick to establish baseline memory
            if not self.watcher_initialized:
                self.last_pending_id = current_id
                self.watcher_initialized = True
                return

            # If we previously had NO tasks, and suddenly we have a task (e.g., from !feed)
            if self.last_pending_id is None and current_id is not None:
                await dispatch_daily_tracker(self.bot)

            # Silently update local memory
            self.last_pending_id = current_id
        except Exception as e:
            logger.error(f"Notion Watcher encountered an error: {e}")

    @notion_watcher.before_loop
    async def before_notion_watcher(self):
        await self.bot.wait_until_ready()

    # ==========================================
    # SCHEDULED TRIGGER: Exactly 6:00 PM IST (12:30 PM UTC)
    # ==========================================
    utc_time = time(hour=12, minute=30, tzinfo=timezone.utc)

    @tasks.loop(time=utc_time)
    async def daily_ping(self):
        try:
            logger.info("Executing scheduled 6:00 PM IST daily tracker ping.")
            await dispatch_daily_tracker(self.bot)
        except Exception as e:
            logger.error(f"Critical error in daily_ping scheduled task: {e}")

    @daily_ping.before_loop
    async def before_daily_ping(self):
        await self.bot.wait_until_ready()
        logger.info("Daily ping scheduler armed for 6:00 PM IST.")

    @commands.command(name="feed")
    async def feed_tasks(self, ctx):
        if ctx.channel.id != FEED_CHANNEL_ID:
            return

        lines = ctx.message.content.split("\n")[1:]

        if not lines:
            await ctx.send(
                "❌ Please provide tasks in the correct format:\n`!feed`\n`Day X | Topic Name`"
            )
            return

        success_count = 0

        for line in reversed(lines):
            if "|" in line:
                day, topic = [part.strip() for part in line.split("|", 1)]
                if await add_task(day, topic):
                    success_count += 1

        await ctx.message.add_reaction("✅")
        await ctx.send(
            f"Successfully synced `{success_count}` tasks to Notion. The Watcher Matrix will detect them shortly."
        )

    @app_commands.command(
        name="py_check",
        description="Manually poll Notion and queue the next pending module.",
    )
    async def py_check(self, interaction: discord.Interaction):
        await interaction.response.defer()
        task_data = await get_next_pending_task()

        if not task_data:
            await interaction.followup.send(
                embed=discord.Embed(
                    description="Notion curriculum is currently empty, sir. No tasks to queue.",
                    color=0x111111,
                )
            )
            return

        embed = discord.Embed(
            title="🛡️ System Alert: Manual Protocol Check",
            description=f"**{task_data['day']}** is queued and awaiting your authorization.",
            color=0x111111,
        )
        await interaction.followup.send(embed=embed, view=ReadyButtonView())

    @app_commands.command(
        name="py_dashboard",
        description="Display current Python learning telemetry from Notion.",
    )
    async def py_dashboard(self, interaction: discord.Interaction):
        await interaction.response.defer()
        stats = await get_progress_stats()

        total = stats["total"]
        comp = stats["completed"]
        perc = stats["percentage"]

        bar_length = 15
        filled = int((perc / 100) * bar_length)
        bar = "█" * filled + "░" * (bar_length - filled)

        embed = discord.Embed(title="📊 Mastery Telemetry", color=0x111111)
        embed.add_field(
            name="Current Progress", value=f"`[{bar}] {perc}%`", inline=False
        )
        embed.add_field(
            name="Module Metrics",
            value=f"✅ Completed: `{comp}/{total}`\n⏳ In-Progress: `{stats['in_progress']}`",
            inline=False,
        )

        next_task = await get_next_pending_task()
        if next_task:
            embed.add_field(
                name="Next in Pipeline",
                value=f"**{next_task['day']}**: {next_task['topic']}",
                inline=False,
            )

        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(PyTrackerCog(bot))
