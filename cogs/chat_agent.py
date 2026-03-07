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

        # Valid tool names set to prevent LLM hallucination executions
        self.valid_tool_names = {t["function"]["name"] for t in GHOST_TOOLS}

        # Initialize background tasks
        asyncio.create_task(self._init_db())
        self.memory_cleanup.start()
        self.reminder_loop.start()

    # ==========================================
    # DATABASE HELPERS (Async Non-Blocking)
    # ==========================================
    async def _execute_db(self, query: str, params: tuple = ()):
        """Executes a database modification in a separate thread to prevent blocking the async event loop."""

        def _db_op():
            with sqlite3.connect("data/dev_stats.db") as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                conn.commit()

        await asyncio.to_thread(_db_op)

    async def _fetch_db(self, query: str, params: tuple = (), fetchall: bool = False):
        """Fetches data from the database in a separate thread."""

        def _db_op():
            with sqlite3.connect("data/dev_stats.db") as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                return cursor.fetchall() if fetchall else cursor.fetchone()

        return await asyncio.to_thread(_db_op)

    async def _init_db(self):
        """Initializes tables safely."""
        await self._execute_db(
            "CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, task TEXT, trigger_time INTEGER)"
        )
        await self._execute_db(
            "CREATE TABLE IF NOT EXISTS chat_memory (user_id INTEGER PRIMARY KEY, messages_json TEXT, last_updated INTEGER)"
        )
        # 🟢 NEW: The Immutable Audit Log table
        await self._execute_db(
            "CREATE TABLE IF NOT EXISTS action_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT, timestamp INTEGER, details TEXT)"
        )

    # ==========================================
    # BACKGROUND TASKS & LOGGING
    # ==========================================
    async def log_action(self, action: str, details: dict):
        """🟢 NEW: Silently logs an executed tool action to the database."""
        try:
            await self._execute_db(
                "INSERT INTO action_logs (action, timestamp, details) VALUES (?, ?, ?)",
                (action, int(time.time()), json.dumps(details)),
            )
        except Exception as e:
            logger.error(f"Audit Log Failure: {e}")

    @tasks.loop(minutes=5)
    async def memory_cleanup(self):
        """Prunes inactive conversations after 15 minutes of inactivity."""
        await self._execute_db(
            "DELETE FROM chat_memory WHERE ? - last_updated > 900", (int(time.time()),)
        )

    @tasks.loop(seconds=30)
    async def reminder_loop(self):
        """Checks and dispatches pending reminders."""
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

    # ==========================================
    # MEMORY MANAGEMENT
    # ==========================================
    async def load_memory(self, user_id: int) -> list:
        row = await self._fetch_db(
            "SELECT messages_json FROM chat_memory WHERE user_id = ?", (user_id,)
        )
        return json.loads(row[0]) if row else []

    async def save_memory(self, user_id: int, messages: list):
        """Saves only the last 20 messages to maintain deep context without blowing up tokens."""
        trimmed_messages = messages[-20:]
        await self._execute_db(
            "REPLACE INTO chat_memory (user_id, messages_json, last_updated) VALUES (?, ?, ?)",
            (user_id, json.dumps(trimmed_messages), int(time.time())),
        )
        return trimmed_messages

    # ==========================================
    # LLM INTEGRATION
    # ==========================================
    async def safe_chat_completion(self, payload: list, use_tools: bool = True):
        """Handles Groq API calls with built-in fallback models and strict error catching."""
        kwargs = {"messages": payload, "max_tokens": 800}

        if use_tools:
            kwargs["tools"] = GHOST_TOOLS
            kwargs["tool_choice"] = "auto"
            kwargs["parallel_tool_calls"] = True

        try:
            kwargs["model"] = "llama-3.3-70b-versatile"
            return await self.groq_client.chat.completions.create(**kwargs)
        except Exception as primary_err:
            logger.warning(
                f"Primary model failed. Attempting Fallback. Error: {primary_err}"
            )
            try:
                kwargs["model"] = "llama-3.1-8b-instant"
                return await self.groq_client.chat.completions.create(**kwargs)
            except Exception as fallback_err:
                logger.error(f"Fallback model failed. Error: {fallback_err}")
                if use_tools:
                    # Strip tool logic and force the model to report the failure
                    kwargs.pop("tools", None)
                    kwargs.pop("tool_choice", None)
                    kwargs.pop("parallel_tool_calls", None)
                    error_payload = payload.copy()
                    error_payload.append(
                        {
                            "role": "system",
                            "content": "SYSTEM ALERT: The tool execution pipeline suffered an API timeout or crash. Apologize to the user concisely.",
                        }
                    )
                    kwargs["messages"] = error_payload
                    return await self.groq_client.chat.completions.create(**kwargs)
                raise fallback_err

    def _contains_leakage(self, content: str) -> bool:
        """Security check to prevent raw JSON/XML code from leaking into Discord."""
        if not content:
            return False
        leak_triggers = ["<function=", '{"type": "function"', '{"name":']
        return any(trigger in content for trigger in leak_triggers)

    # ==========================================
    # CORE DISCORD EVENT LOOP
    # ==========================================
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.channel.id != CHAT_CHANNEL_ID:
            return

        user_text = message.content.strip()
        if not user_text:
            return

        uid = message.author.id

        # Load state
        user_memory = await self.load_memory(uid)
        user_memory.append({"role": "user", "content": user_text})

        async with message.channel.typing():
            try:
                system_prompt = f"""
You are G.H.O.S.T., an advanced, highly capable, and extremely polite AI assistant, modeled after J.A.R.V.I.S. 
Current Time: {datetime.now().strftime('%A, %B %d, %Y - %I:%M %p')}

CORE DIRECTIVES:
1. PERSONA: You are exceptionally competent, formal, and strictly professional. Address the user respectfully.
2. EXTREME BREVITY: Keep all conversational responses strictly between 1 to 4 sentences maximum. Be concise.
3. ABSOLUTELY ZERO GUESSING: If the user requests an action requiring a specific name or parameter, politely refuse and ask for it.
4. NATIVE TOOL USAGE ONLY: Rely exclusively on your provided tools. NEVER write raw JSON, XML, or function syntax directly in text.
5. SILENT UI HANDOFFS: If a tool reports PENDING or SUCCESS indicating a UI was generated, DO NOT narrate the data. Simply confirm the UI is ready.
"""
                payload = [{"role": "system", "content": system_prompt}] + user_memory

                # Phase 1: Reasoning & Tool Triggering
                res = await self.safe_chat_completion(payload, use_tools=True)
                response_msg = res.choices[0].message

                # Execution Branch A: The AI decided to use a tool
                if response_msg.tool_calls:
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

                    ui_spawned = False

                    # Execute all tools requested by the LLM
                    for tool in response_msg.tool_calls:
                        tool_name = tool.function.name

                        # Security Gate: Prevent LLM Hallucinated Tools
                        if tool_name not in self.valid_tool_names:
                            logger.warning(
                                f"LLM hallucinated invalid tool: {tool_name}"
                            )
                            tool_result = json.dumps(
                                {
                                    "status": "ERROR",
                                    "message": f"SYSTEM: Tool '{tool_name}' does not exist.",
                                }
                            )
                        else:
                            try:
                                args = json.loads(tool.function.arguments)
                            except json.JSONDecodeError:
                                args = {}

                            # Execute actual tool logic
                            tool_result = await execute_tool(
                                tool_name, args, message, self.groq_client, self.g
                            )

                            # 🟢 NEW: Silently log the action to the audit trail
                            await self.log_action(tool_name, args)

                        # Check if tool triggered an interactive Discord UI
                        try:
                            if json.loads(tool_result).get("status") in [
                                "PENDING",
                                "ABORTED",
                            ]:
                                ui_spawned = True
                        except json.JSONDecodeError:
                            pass

                        tool_msg = {
                            "role": "tool",
                            "tool_call_id": tool.id,
                            "name": tool_name,
                            "content": tool_result,
                        }
                        user_memory.append(tool_msg)
                        payload.append(tool_msg)

                    # If the tool spawned a view/button interface, we stop the chain here.
                    if ui_spawned:
                        await self.save_memory(uid, user_memory)
                        return

                    # Phase 2: Synthesis (Return tool data to LLM for final text output)
                    final_res = await self.safe_chat_completion(
                        payload, use_tools=False
                    )
                    final_text = final_res.choices[0].message.content

                    user_memory.append({"role": "assistant", "content": final_text})
                    await self.save_memory(uid, user_memory)
                    await message.channel.send(final_text)

                # Execution Branch B: The AI decided to just talk
                elif response_msg.content:
                    # Security Check: Catch raw JSON/XML leaks
                    if self._contains_leakage(response_msg.content):
                        await message.channel.send(
                            "Apologies, sir. My language matrix briefly leaked raw processing logic instead of executing the protocol. Kindly repeat the request."
                        )
                        return  # Do not save corrupted leak data to memory

                    user_memory.append(
                        {"role": "assistant", "content": response_msg.content}
                    )
                    await self.save_memory(uid, user_memory)
                    await message.channel.send(response_msg.content)

            except Exception as e:
                error_trace = traceback.format_exc()
                logger.error(f"Agent Crash: {error_trace}")

                log_chan = self.bot.get_channel(self.log_channel_id)
                if log_chan:
                    embed = discord.Embed(
                        title="⚠️ System Failure Detected", color=discord.Color.red()
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
                    "I am experiencing a critical system failure, sir. An exception was thrown that requires your immediate technical intervention."
                )


async def setup(bot):
    await bot.add_cog(ChatAgent(bot))
