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
import time
import json

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

    # ==========================================
    # OMNI-AGENT AUDIT LOGGING
    # ==========================================
    async def _log_action(self, action: str, details: dict):
        """Silently logs an executed action to the global audit database."""

        def _db_op():
            with sqlite3.connect("data/dev_stats.db") as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO action_logs (action, timestamp, details) VALUES (?, ?, ?)",
                    (action, int(time.time()), json.dumps(details)),
                )
                conn.commit()

        await asyncio.to_thread(_db_op)

    # ==========================================
    # AUTONOMOUS DEVELOPER LOOP
    # ==========================================
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
            await self._log_action(
                "auto_streaker_error",
                {"error": "Database read failure", "details": str(e)},
            )
            return

        # 🟢 THE AUTONOMY ENGINE: Self-Generating Tasks
        if not task:
            logger.info("Backlog empty. Generating a self-assigned autonomous task.")
            try:
                # Ask the LLM to invent a coding challenge for itself
                ai_task_res = await self.groq_client.chat.completions.create(
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a senior Python instructor. Provide a single, 1-sentence Python coding challenge (e.g., 'Write a script that validates an IPv4 address'). Output ONLY the challenge sentence. No quotes, no markdown.",
                        }
                    ],
                    model="llama-3.1-8b-instant",
                    max_tokens=50,
                )
                prompt = ai_task_res.choices[0].message.content.strip()
                task_id = "AUTO_GENERATED"
            except Exception as e:
                logger.error(f"Failed to self-generate a task: {e}")
                conn.close()
                return
        else:
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

            # 🟢 The AI writes the code
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
                # Fallback model
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
                        f"Groq is completely down. Primary error: {e}. Fallback error: {fallback_err}"
                    )

            clean_prompt_name = (
                re.sub(r"[^a-zA-Z0-9]", "_", prompt[:15]).strip("_").lower()
            )
            filename = f"day_{task_id}_{clean_prompt_name}.py"
            commit_msg = f"Auto-Commit: Solved '{prompt[:30]}...'"

            # 🟢 Push to GitHub
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

            # Update DB if it was a manual task
            if task_id != "AUTO_GENERATED":
                try:
                    cursor.execute(
                        "UPDATE streak_backlog SET status = 'COMPLETED' WHERE id = ?",
                        (task_id,),
                    )
                    conn.commit()
                except Exception as e:
                    logger.error(f"AutoStreaker DB Update Failed: {e}")

            # 🟢 NEW: Log the successful automated deployment to Omni-Agent memory
            await self._log_action(
                "autonomous_commit",
                {
                    "task_source": (
                        "user_backlog"
                        if task_id != "AUTO_GENERATED"
                        else "ai_self_assigned"
                    ),
                    "prompt": prompt,
                    "filename": filename,
                    "repo": repo.name,
                },
            )

            if log_chan:
                embed = discord.Embed(
                    title="🟢 Autonomous Code Deployed",
                    description=f"I successfully engineered and pushed `{filename}` to `{repo.name}` while you were doing nothing.",
                    color=discord.Color.brand_green(),
                )
                embed.add_field(
                    name="Task Conquered", value=f"*{prompt}*", inline=False
                )
                if task_id == "AUTO_GENERATED":
                    embed.set_footer(
                        text="Backlog was empty. I assigned this task to myself to maintain the streak."
                    )
                await log_chan.send(embed=embed)

        except Exception as e:
            logger.error(f"Auto-Streaker Failure: {traceback.format_exc()}")
            await self._log_action(
                "auto_streaker_error", {"prompt": prompt, "error": str(e)}
            )

            if log_chan:
                embed = discord.Embed(
                    title="🔥 Autonomous Engine Failed",
                    description="Your automated streak bot crashed violently.",
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
