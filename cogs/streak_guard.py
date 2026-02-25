import discord
from discord.ext import commands, tasks
from discord import app_commands
from github import Github
import os
import datetime
import sqlite3
import asyncio
import logging
import traceback
import re

logger = logging.getLogger("GhostCommander")


class StreakGuard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.g = Github(os.getenv("GITHUB_TOKEN"))

        try:
            os.makedirs("data", exist_ok=True)
            self.init_db()
        except Exception as e:
            logger.error(f"Failed to create data directory or DB: {e}")

        self.streak_reminder.start()

    def init_db(self):
        try:
            conn = sqlite3.connect("data/dev_stats.db")
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS alerts (
                    user_id INTEGER PRIMARY KEY,
                    channel_id INTEGER,
                    github_username TEXT
                )
                """
            )
            conn.commit()
        except Exception as e:
            logger.error(f"StreakGuard DB Init Failed: {e}")
        finally:
            try:
                conn.close()
            except:
                pass

    def has_committed_today(self, username):
        try:
            user = self.g.get_user(username)
            events = user.get_events()

            today = datetime.datetime.now(datetime.timezone.utc).date()

            for event in events:
                if not event.created_at:
                    continue

                event_date = event.created_at.date()

                if event_date < today:
                    break

                if event.type == "PushEvent":
                    return True

            return False

        except Exception as e:
            logger.error(f"GitHub API Error for {username}: {traceback.format_exc()}")
            return True

    @app_commands.command(
        name="guard_on", description="Enable the 10:00 PM Daily GitHub Streak reminder"
    )
    @app_commands.describe(github_username="Your exact GitHub username")
    async def guard_on(self, interaction: discord.Interaction, github_username: str):
        if not github_username or not re.match(
            r"^[a-zA-Z\d](?:[a-zA-Z\d]|-(?=[a-zA-Z\d])){0,38}$", github_username
        ):
            return await interaction.response.send_message(
                "🛑 **Are you physically incapable of typing a valid GitHub username?** That is garbage. Try again when you figure out how keyboards work.",
                ephemeral=True,
            )

        try:
            await asyncio.to_thread(self.g.get_user, github_username)
        except Exception:
            return await interaction.response.send_message(
                f"🛑 **I searched the entire GitHub database.** `{github_username}` does not exist. Stop making up imaginary friends and give me a real account.",
                ephemeral=True,
            )

        try:
            conn = sqlite3.connect("data/dev_stats.db")
            cursor = conn.cursor()
            cursor.execute(
                "REPLACE INTO alerts (user_id, channel_id, github_username) VALUES (?, ?, ?)",
                (interaction.user.id, interaction.channel.id, github_username),
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Guard On DB Error: {e}")
            return await interaction.response.send_message(
                "🔥 **My internal database just threw up.** I couldn't save your request. It's probably your fault.",
                ephemeral=True,
            )
        finally:
            try:
                conn.close()
            except:
                pass

        await interaction.response.send_message(
            f"🛡️ **Babysitting Mode Activated.**\nI am now monitoring `{github_username}`. If you haven't pushed any code by 10:00 PM, I will publicly humiliate you in this channel. Don't test me.",
            ephemeral=False,
        )

    tz_ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

    @tasks.loop(time=datetime.time(hour=22, minute=0, tzinfo=tz_ist))
    async def streak_reminder(self):
        try:
            conn = sqlite3.connect("data/dev_stats.db")
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, channel_id, github_username FROM alerts")
            users = cursor.fetchall()
        except Exception as e:
            logger.error(f"Streak Reminder DB Read Error: {e}")
            return
        finally:
            try:
                conn.close()
            except:
                pass

        for user_id, channel_id, github_username in users:
            try:
                committed = await asyncio.to_thread(
                    self.has_committed_today, github_username
                )

                if not committed:
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        await channel.send(
                            f"⚠️ <@{user_id}> **WAKE UP.**\nIt is 10:00 PM and your GitHub graph is as empty as your promises. Open your editor and push some code before your streak dies."
                        )
            except Exception as e:
                logger.error(
                    f"Failed to check streak or send message for {github_username}: {e}"
                )

    @streak_reminder.before_loop
    async def before_reminder(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(StreakGuard(bot))
