import discord
from discord.ext import commands, tasks
from groq import AsyncGroq
from github import Github
import os
import json
import sqlite3
import time
import logging
import traceback
import asyncio
from datetime import datetime

from utils.ghost_tools import GHOST_TOOLS, execute_tool

logger = logging.getLogger("GhostCommander")

CHAT_CHANNEL_ID = int(os.getenv("CHAT_CHANNEL_ID", 0))


class ChatAgent(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.groq_client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
        self.g = Github(os.getenv("GITHUB_TOKEN"))
        self.log_channel_id = int(os.getenv("LOG_CHANNEL_ID", 0))
        self.valid_tool_names = {t["function"]["name"] for t in GHOST_TOOLS}

        # FIX: Do NOT call asyncio.create_task() here — event loop may not be
        # running yet during __init__. DB init is moved to cog_load() below.

    async def cog_load(self):
        """
        Called by discord.py automatically after the Cog is loaded and the
        event loop is guaranteed to be running. Safe place for async startup work.
        """
        await self._init_db()
        self.memory_cleanup.start()
        self.reminder_loop.start()
        logger.info("✅ ChatAgent DB initialized and background tasks started.")

    async def cog_unload(self):
        """Cleanly cancel background tasks when the cog is unloaded."""
        self.memory_cleanup.cancel()
        self.reminder_loop.cancel()

    # ==========================================
    # DB HELPERS
    # ==========================================

    async def _execute_db(self, query: str, params: tuple = ()):
        def _db_op():
            with sqlite3.connect("data/dev_stats.db") as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                conn.commit()

        await asyncio.to_thread(_db_op)

    async def _fetch_db(self, query: str, params: tuple = (), fetchall: bool = False):
        def _db_op():
            with sqlite3.connect("data/dev_stats.db") as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                return cursor.fetchall() if fetchall else cursor.fetchone()

        return await asyncio.to_thread(_db_op)

    async def _init_db(self):
        """
        Create all required tables if they do not already exist.
        FIX: Added missing `alerts` and `streak_backlog` tables that were
        referenced in ghost_tools.py but never created — caused runtime crashes.
        """
        # Core agent tables
        await self._execute_db(
            """CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                task TEXT,
                trigger_time INTEGER
            )"""
        )
        await self._execute_db(
            """CREATE TABLE IF NOT EXISTS chat_memory (
                user_id INTEGER PRIMARY KEY,
                messages_json TEXT,
                last_updated INTEGER
            )"""
        )
        await self._execute_db(
            """CREATE TABLE IF NOT EXISTS action_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT,
                timestamp INTEGER,
                details TEXT
            )"""
        )

        # FIX: streak_guard toggle writes to this table — was never created
        await self._execute_db(
            """CREATE TABLE IF NOT EXISTS alerts (
                user_id INTEGER PRIMARY KEY,
                channel_id INTEGER,
                github_username TEXT
            )"""
        )

        # FIX: add_to_backlog writes to this table — was never created
        await self._execute_db(
            """CREATE TABLE IF NOT EXISTS streak_backlog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                prompt TEXT,
                status TEXT DEFAULT 'PENDING',
                created_at INTEGER DEFAULT (strftime('%s', 'now'))
            )"""
        )

        logger.info("✅ All database tables verified/created.")

    # ==========================================
    # AUDIT LOGGING
    # ==========================================

    async def log_action(self, action: str, details: dict):
        try:
            await self._execute_db(
                "INSERT INTO action_logs (action, timestamp, details) VALUES (?, ?, ?)",
                (action, int(time.time()), json.dumps(details)),
            )
        except Exception as e:
            logger.error(f"Audit Log Failure: {e}")

    # ==========================================
    # BACKGROUND TASKS
    # ==========================================

    @tasks.loop(minutes=5)
    async def memory_cleanup(self):
        """Purge chat memory older than 15 minutes (900 seconds)."""
        await self._execute_db(
            "DELETE FROM chat_memory WHERE ? - last_updated > 900", (int(time.time()),)
        )

    @tasks.loop(seconds=30)
    async def reminder_loop(self):
        rows = await self._fetch_db(
            "SELECT id, user_id, task FROM reminders WHERE trigger_time <= ?",
            (int(time.time()),),
            fetchall=True,
        )

        for r_id, user_id, task in rows or []:
            log_chan = self.bot.get_channel(self.log_channel_id)
            if log_chan:
                embed = discord.Embed(
                    title="⏰ WAKE UP",
                    description=f"<@{user_id}>\n**You told me to remind you about this:** {task}\nNow go do it.",
                    color=discord.Color.gold(),
                )
                await log_chan.send(embed=embed)
            await self._execute_db("DELETE FROM reminders WHERE id = ?", (r_id,))

    @reminder_loop.before_loop
    async def before_reminder_loop(self):
        await self.bot.wait_until_ready()

    @memory_cleanup.before_loop
    async def before_memory_cleanup(self):
        await self.bot.wait_until_ready()

    # ==========================================
    # MEMORY MANAGEMENT
    # ==========================================

    async def load_memory(self, user_id: int) -> list:
        row = await self._fetch_db(
            "SELECT messages_json FROM chat_memory WHERE user_id = ?", (user_id,)
        )
        return json.loads(row[0]) if row else []

    async def save_memory(self, user_id: int, messages: list):
        trimmed_messages = messages[-20:]
        await self._execute_db(
            "REPLACE INTO chat_memory (user_id, messages_json, last_updated) VALUES (?, ?, ?)",
            (user_id, json.dumps(trimmed_messages), int(time.time())),
        )
        return trimmed_messages

    # ==========================================
    # GROQ API WRAPPER
    # ==========================================

    async def safe_chat_completion(self, payload: list, use_tools: bool = True):
        kwargs = {"messages": payload, "max_tokens": 800}

        if use_tools:
            kwargs["tools"] = GHOST_TOOLS
            kwargs["tool_choice"] = "auto"
            # FIX: parallel_tool_calls disabled — prevents two UI views spawning
            # simultaneously when AI wants to call multiple tools at once.
            # Sequential execution is required for safety and UX coherence.
            kwargs["parallel_tool_calls"] = False

        try:
            kwargs["model"] = "llama-3.3-70b-versatile"
            return await self.groq_client.chat.completions.create(**kwargs)

        except Exception as primary_err:
            logger.warning(f"Primary model failed. Attempting fallback. {primary_err}")

            try:
                kwargs["model"] = "llama-3.1-8b-instant"
                return await self.groq_client.chat.completions.create(**kwargs)

            except Exception as fallback_err:
                logger.error(f"Fallback model also failed: {fallback_err}")

                if use_tools:
                    kwargs.pop("tools", None)
                    kwargs.pop("tool_choice", None)
                    kwargs.pop("parallel_tool_calls", None)

                    error_payload = payload.copy()
                    error_payload.append({
                        "role": "system",
                        "content": "SYSTEM ALERT: Tool execution pipeline crashed. Apologize briefly.",
                    })
                    kwargs["messages"] = error_payload
                    return await self.groq_client.chat.completions.create(**kwargs)

                raise fallback_err

    # ==========================================
    # SAFETY: DETECT INTERNAL PROMPT LEAKAGE
    # ==========================================

    def _contains_leakage(self, content: str) -> bool:
        if not content:
            return False
        leak_triggers = ["<function=", '{"type": "function"', '{"name":']
        return any(trigger in content for trigger in leak_triggers)

    # ==========================================
    # MAIN MESSAGE HANDLER
    # ==========================================

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.channel.id != CHAT_CHANNEL_ID:
            return

        user_text = message.content.strip()
        if not user_text:
            return

        uid = message.author.id
        user_memory = await self.load_memory(uid)
        user_memory.append({"role": "user", "content": user_text})

        async with message.channel.typing():
            try:
                system_prompt = f"""You are G.H.O.S.T., a professional AI assistant.
Current Time: {datetime.now().strftime('%A, %B %d, %Y - %I:%M %p')}

Rules:
1. Use tools ONLY if the user explicitly asks for an action.
2. Be professional and direct.
3. Keep responses to 3 sentences max unless explaining an error.
4. Never output raw JSON or internal function schemas.
5. For PENDING tool results, do not narrate the outcome — the UI handles it.
"""
                payload = [{"role": "system", "content": system_prompt}] + user_memory

                res = await self.safe_chat_completion(payload, True)
                response_msg = res.choices[0].message

                if response_msg.tool_calls:
                    # Append assistant message with tool_calls to payload
                    assistant_msg = {
                        "role": "assistant",
                        "content": response_msg.content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in response_msg.tool_calls
                        ],
                    }
                    user_memory.append(assistant_msg)
                    payload.append(assistant_msg)

                    # Send any pre-tool commentary (if clean)
                    if response_msg.content and not self._contains_leakage(response_msg.content):
                        await message.channel.send(response_msg.content)

                    # Execute tools sequentially (parallel_tool_calls=False guarantees only 1)
                    for tool in response_msg.tool_calls:
                        tool_name = tool.function.name

                        if tool_name not in self.valid_tool_names:
                            tool_result = json.dumps({
                                "status": "ERROR",
                                "message": f"Tool '{tool_name}' does not exist.",
                            })
                        else:
                            try:
                                args = json.loads(tool.function.arguments)
                            except (json.JSONDecodeError, Exception):
                                args = {}

                            tool_result = await execute_tool(
                                tool_name, args, message, self.groq_client, self.g
                            )
                            await self.log_action(tool_name, args)

                        tool_msg = {
                            "role": "tool",
                            "tool_call_id": tool.id,
                            "name": tool_name,
                            "content": tool_result,
                        }
                        user_memory.append(tool_msg)
                        payload.append(tool_msg)

                    # Final response after tool results
                    final_res = await self.safe_chat_completion(payload, False)
                    final_text = final_res.choices[0].message.content

                    user_memory.append({"role": "assistant", "content": final_text})
                    await self.save_memory(uid, user_memory)
                    await message.channel.send(final_text)

                elif response_msg.content:
                    if self._contains_leakage(response_msg.content):
                        await message.channel.send(
                            "⚠️ Internal processing error. Please retry your request."
                        )
                        return

                    user_memory.append({"role": "assistant", "content": response_msg.content})
                    await self.save_memory(uid, user_memory)
                    await message.channel.send(response_msg.content)

            except Exception as e:
                error_trace = traceback.format_exc()
                logger.error(error_trace)

                log_chan = self.bot.get_channel(self.log_channel_id)
                if log_chan:
                    embed = discord.Embed(
                        title="⚠️ System Failure Detected",
                        color=discord.Color.red(),
                    )
                    embed.add_field(
                        name="Error Type", value=f"`{type(e).__name__}`", inline=False
                    )
                    embed.add_field(
                        name="Traceback",
                        value=f"```py\n{error_trace[-1000:]}\n```",
                        inline=False,
                    )
                    await log_chan.send(embed=embed)

                await message.channel.send(
                    "⚠️ Critical system failure detected. Intervention required."
                )


async def setup(bot):
    await bot.add_cog(ChatAgent(bot))