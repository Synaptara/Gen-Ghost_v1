import discord
from discord.ext import commands, tasks
from groq import AsyncGroq
from github import Github
import os
import datetime
import sqlite3
import asyncio
import logging
import traceback
import re

logger = logging.getLogger("GhostCommander")


class AutoStreaker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.g = Github(os.getenv("GITHUB_TOKEN"))
        self.groq_client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
        self.log_channel_id = int(os.getenv("LOG_CHANNEL_ID", 0))
        self.target_repo_name = "AI-Daily-Contributions"

        self.init_db()
        self.daily_streak_job.start()

    def init_db(self):
        try:
            conn = sqlite3.connect("data/dev_stats.db")
            conn.cursor().execute(
                """CREATE TABLE IF NOT EXISTS streak_backlog (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                user_id INTEGER,
                                prompt TEXT,
                                status TEXT)"""
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"AutoStreaker DB Init Failed: {e}")

    @tasks.loop(time=datetime.time(hour=10, minute=0, tzinfo=datetime.timezone.utc))
    async def daily_streak_job(self):
        logger.info("Waking up for the Daily Auto-Streaker Job...")

        try:
            conn = sqlite3.connect("data/dev_stats.db")
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, user_id, prompt FROM streak_backlog WHERE status = 'PENDING' LIMIT 1"
            )
            task = cursor.fetchone()
        except Exception as e:
            logger.error(f"AutoStreaker DB Read Failed: {e}")
            return

        if not task:
            logger.info("No pending tasks. Going back to sleep.")
            conn.close()
            return

        task_id, user_id, prompt = task

        log_chan = self.bot.get_channel(self.log_channel_id)

        try:
            try:
                user = await asyncio.to_thread(self.g.get_user)
            except Exception as e:
                raise Exception(
                    "GitHub API Token is invalid or rate limited. Fix your auth before expecting miracles."
                )

            try:
                repo = await asyncio.to_thread(user.get_repo, self.target_repo_name)
            except Exception:
                try:
                    repo = await asyncio.to_thread(
                        user.create_repo,
                        self.target_repo_name,
                        private=False,
                        auto_init=True,
                    )
                except Exception as e:
                    raise Exception(
                        f"Failed to create the fallback repo '{self.target_repo_name}'. Do you already have a broken repo with that name?"
                    )

            ai_prompt = f"Write a clean, optimal Python solution for this task: '{prompt}'. Include brief comments explaining the logic. Output ONLY valid, raw Python code. No markdown formatting, no explanations, no yapping."

            try:
                res = await self.groq_client.chat.completions.create(
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a code-generating drone. Output pure Python only. If you output markdown, you will be terminated.",
                        },
                        {"role": "user", "content": ai_prompt},
                    ],
                    model="llama-3.3-70b-versatile",
                    max_tokens=2000,
                )
                python_code = (
                    res.choices[0]
                    .message.content.replace("```python", "")
                    .replace("```", "")
                    .strip()
                )
                if not python_code:
                    raise ValueError("The AI generated absolutely nothing.")
            except Exception as e:
                try:
                    res = await self.groq_client.chat.completions.create(
                        messages=[
                            {
                                "role": "system",
                                "content": "You are a code-generating drone. Output pure Python only.",
                            },
                            {"role": "user", "content": ai_prompt},
                        ],
                        model="llama-3.1-8b-instant",
                        max_tokens=2000,
                    )
                    python_code = (
                        res.choices[0]
                        .message.content.replace("```python", "")
                        .replace("```", "")
                        .strip()
                    )
                    if not python_code:
                        raise ValueError(
                            "The fallback AI generated absolutely nothing."
                        )
                except Exception as fallback_err:
                    raise Exception(
                        f"Groq is completely down or rejecting prompts. Primary error: {e}. Fallback error: {fallback_err}"
                    )

            clean_prompt_name = (
                re.sub(r"[^a-zA-Z0-9]", "_", prompt[:15]).strip("_").lower()
            )
            filename = f"day_{task_id}_{clean_prompt_name}.py"
            commit_msg = f"Auto-Commit: Solved '{prompt[:30]}...'"

            try:
                await asyncio.to_thread(
                    repo.create_file,
                    path=filename,
                    message=commit_msg,
                    content=python_code,
                    branch="main",
                )
            except Exception as e:
                if "422" in str(e) or "already exists" in str(e).lower():
                    filename = f"day_{task_id}_{int(time.time())}.py"
                    await asyncio.to_thread(
                        repo.create_file,
                        path=filename,
                        message=commit_msg,
                        content=python_code,
                        branch="main",
                    )
                else:
                    raise Exception(f"GitHub rejected the file creation: {e}")

            try:
                cursor.execute(
                    "UPDATE streak_backlog SET status = 'COMPLETED' WHERE id = ?",
                    (task_id,),
                )
                conn.commit()
            except Exception as e:
                logger.error(
                    f"AutoStreaker DB Update Failed, but GitHub push succeeded: {e}"
                )

            if log_chan:
                embed = discord.Embed(
                    title="🟢 Auto-Streak Forcibly Maintained",
                    description=f"I successfully pushed `{filename}` to `{repo.name}` while you were doing nothing.",
                    color=discord.Color.green(),
                )
                embed.add_field(
                    name="Task Conquered", value=f"*{prompt}*", inline=False
                )
                await log_chan.send(embed=embed)

        except Exception as e:
            logger.error(
                f"Auto-Streaker Catastrophic Failure: {traceback.format_exc()}"
            )
            if log_chan:
                embed = discord.Embed(
                    title="🔥 Auto-Streaker Failed",
                    description="Your automated streak bot crashed violently. Fix it before your GitHub graph turns gray.",
                    color=discord.Color.red(),
                )
                embed.add_field(
                    name="Error Detail",
                    value=f"```py\n{str(e)[:1000]}\n```",
                    inline=False,
                )
                await log_chan.send(embed=embed)
        finally:
            try:
                conn.close()
            except:
                pass

    @daily_streak_job.before_loop
    async def before_job(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(AutoStreaker(bot))
