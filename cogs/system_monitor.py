import discord
from discord.ext import commands
from discord import app_commands
from github import Github
import psutil
import os
import asyncio
import time
import re
import logging
import traceback
import sqlite3
import json

logger = logging.getLogger("GhostCommander")


class SystemMonitor(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.g = Github(os.getenv("GITHUB_TOKEN"))
        self.start_time = time.time()

    # ==========================================
    # OMNI-AGENT AUDIT LOGGING
    # ==========================================
    async def _log_action(self, action: str, details: dict):
        """Silently logs an executed manual command to the global audit database."""

        def _db_op():
            with sqlite3.connect("data/dev_stats.db") as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO action_logs (action, timestamp, details) VALUES (?, ?, ?)",
                    (action, int(time.time()), json.dumps(details)),
                )
                conn.commit()

        await asyncio.to_thread(_db_op)

    async def cog_load(self):
        """Automatically fires when the bot starts up and loads this module."""
        await self._log_action(
            "bot_start", {"module": "SystemMonitor", "status": "online"}
        )

    # ==========================================
    # COMMAND: /status
    # ==========================================
    @app_commands.command(
        name="status", description="Check bot uptime, CPU, RAM, and Battery health"
    )
    async def system_status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)

        try:
            cpu_usage = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            ram_usage = f"{memory.used / (1024**3):.2f}GB / {memory.total / (1024**3):.2f}GB ({memory.percent}%)"

            battery_status = "🔌 AWS Instances Don't Have Batteries, Genius"
            try:
                battery = psutil.sensors_battery()
                if battery:
                    plugged_status = (
                        "⚡ Plugged In" if battery.power_plugged else "🔋 Discharging"
                    )
                    battery_status = f"{battery.percent}% ({plugged_status})"
            except Exception:
                pass

            uptime_seconds = int(time.time() - self.start_time)
            uptime_str = f"{uptime_seconds // 3600}h {(uptime_seconds % 3600) // 60}m {uptime_seconds % 60}s"

            # 🟢 Log the successful status check to the Omni-Agent memory
            await self._log_action(
                "system_status_check",
                {"cpu_percent": cpu_usage, "ram_percent": memory.percent},
            )

            if cpu_usage > 90 or memory.percent > 90:
                color = discord.Color.red()
                title_prefix = "🔥 CRITICAL ALERT:"
                roast = "\n\n**WARNING:** Your AWS Free Tier is literally choking to death. Shut something down before the server crashes."
            else:
                color = discord.Color.green()
                title_prefix = "📊"
                roast = ""

            embed = discord.Embed(
                title=f"{title_prefix} AWS System Health Dashboard",
                description=roast,
                color=color,
            )
            embed.add_field(name="⚙️ CPU Usage", value=f"`{cpu_usage}%`", inline=True)
            embed.add_field(name="🧠 RAM Usage", value=f"`{ram_usage}`", inline=True)
            embed.add_field(name="🔋 Battery", value=f"`{battery_status}`", inline=True)
            embed.add_field(name="⏱️ Bot Uptime", value=f"`{uptime_str}`", inline=True)
            embed.set_footer(text="GhostCommander DevOps Control")

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Status command crash: {traceback.format_exc()}")
            # 🟢 Log the system error
            await self._log_action(
                "system_error", {"command": "status", "error": str(e)}
            )
            await interaction.followup.send(
                "🔥 **The server is so broken it can't even tell me how broken it is.** Check your AWS console."
            )

    # ==========================================
    # COMMAND: /workflow
    # ==========================================
    @app_commands.command(
        name="workflow", description="Check the latest GitHub Actions workflow status"
    )
    @app_commands.describe(repo_name="Name of your repository (e.g., my-project)")
    async def workflow_status(self, interaction: discord.Interaction, repo_name: str):
        await interaction.response.defer(ephemeral=False)

        if not repo_name or not re.match(r"^[a-zA-Z0-9_-]+$", repo_name):
            return await interaction.followup.send(
                "🛑 **That is not a valid repository name.** Do you just smash your face on the keyboard and hope it works?"
            )

        try:
            try:
                user = await asyncio.to_thread(self.g.get_user)
                repo = await asyncio.to_thread(user.get_repo, repo_name)
            except Exception:
                return await interaction.followup.send(
                    f"🛑 **I checked everywhere.** `{repo_name}` does not exist. Stop making things up."
                )

            try:
                runs = await asyncio.to_thread(
                    lambda: list(repo.get_workflow_runs()[:3])
                )
            except Exception as e:
                logger.error(f"GitHub Workflow API Error: {e}")
                return await interaction.followup.send(
                    "🔥 **GitHub API is choking.** Try again later."
                )

            if not runs:
                return await interaction.followup.send(
                    f"⚠️ **Nothing to see here.** `{repo_name}` doesn't have any GitHub Actions workflows set up. Write some tests first."
                )

            # 🟢 Log the successful workflow check
            await self._log_action(
                "workflow_status_check", {"repo": repo.name, "runs_fetched": len(runs)}
            )

            embed = discord.Embed(
                title=f"🔄 Workflow Status: {repo.name}",
                url=repo.html_url,
                color=discord.Color.blue(),
            )

            for run in runs:
                status_emoji = (
                    "✅"
                    if run.conclusion == "success"
                    else "❌" if run.conclusion == "failure" else "⏳"
                )

                conclusion_text = run.conclusion if run.conclusion else "In Progress"

                embed.add_field(
                    name=f"{status_emoji} {run.name}",
                    value=f"**Branch:** `{run.head_branch}`\n**Conclusion:** `{conclusion_text.upper()}`\n**Event:** `{run.event}`",
                    inline=False,
                )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Workflow command crash: {traceback.format_exc()}")
            # 🟢 Log the workflow checking error
            await self._log_action(
                "system_error",
                {"command": "workflow", "repo": repo_name, "error": str(e)},
            )
            await interaction.followup.send(
                "🔥 **Catastrophic failure fetching workflows.** Look at your terminal if you want the stack trace."
            )

    # ==========================================
    # COMMAND: /clear
    # ==========================================
    @app_commands.command(
        name="clear", description="Purge messages in the current channel."
    )
    @app_commands.describe(
        amount="Number of messages to delete (leave blank to wipe EVERYTHING)"
    )
    @app_commands.default_permissions(manage_messages=True)  # Security Gate
    async def clear_channel(self, interaction: discord.Interaction, amount: int = None):
        # 1. Defer the response immediately. Deleting messages takes time.
        await interaction.response.defer(ephemeral=True)

        try:
            # 2. Execute the purge. If amount is None, it deletes everything (subject to Discord's 14-day limit).
            deleted = await interaction.channel.purge(limit=amount)

            # 3. 🟢 Log the action to the Omni-Agent memory
            await self._log_action(
                "channel_purge",
                {
                    "channel": interaction.channel.name,
                    "messages_deleted": len(deleted),
                    "requested_amount": amount or "ALL",
                },
            )

            # 4. Silent UI confirmation (only you will see this message)
            await interaction.followup.send(
                f"✅ **Sanitization Complete.** Eradicated `{len(deleted)}` messages from #{interaction.channel.name}.",
                ephemeral=True,
            )

        except discord.Forbidden:
            await interaction.followup.send(
                "🛑 **Access Denied.** I lack the `Manage Messages` permission in this channel.",
                ephemeral=True,
            )
        except Exception as e:
            logger.error(f"Clear command crash: {traceback.format_exc()}")
            await self._log_action(
                "system_error", {"command": "clear", "error": str(e)}
            )
            await interaction.followup.send(
                f"🔥 **System Error:** `{str(e)}`", ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(SystemMonitor(bot))
