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
from datetime import datetime

from utils.ghost_tools import GHOST_TOOLS, execute_tool

logger = logging.getLogger("GhostCommander")

# Fetch the allowed chat channel ID from the environment
CHAT_CHANNEL_ID = int(os.getenv("CHAT_CHANNEL_ID", 0))


class ChatAgent(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.groq_client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
        self.g = Github(os.getenv("GITHUB_TOKEN"))
        self.log_channel_id = int(os.getenv("LOG_CHANNEL_ID", 0))

        self.init_db()
        self.memory_cleanup.start()
        self.reminder_loop.start()

    def init_db(self):
        conn = sqlite3.connect("data/dev_stats.db")
        cursor = conn.cursor()
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, task TEXT, trigger_time INTEGER)"
        )
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS chat_memory (user_id INTEGER PRIMARY KEY, messages_json TEXT, last_updated INTEGER)"
        )
        conn.commit()
        conn.close()

    @tasks.loop(minutes=5)
    async def memory_cleanup(self):
        conn = sqlite3.connect("data/dev_stats.db")
        conn.cursor().execute(
            "DELETE FROM chat_memory WHERE ? - last_updated > 900", (int(time.time()),)
        )
        conn.commit()
        conn.close()

    @tasks.loop(seconds=30)
    async def reminder_loop(self):
        conn = sqlite3.connect("data/dev_stats.db")
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, user_id, task FROM reminders WHERE trigger_time <= ?",
            (int(time.time()),),
        )
        for r_id, user_id, task in cursor.fetchall():
            log_chan = self.bot.get_channel(self.log_channel_id)
            if log_chan:
                embed = discord.Embed(
                    title="⏰ WAKE UP",
                    description=f"<@{user_id}>\n**You told me to remind you about this:** {task}\nNow go do it.",
                    color=discord.Color.gold(),
                )
                await log_chan.send(embed=embed)
            cursor.execute("DELETE FROM reminders WHERE id = ?", (r_id,))
        conn.commit()
        conn.close()

    @reminder_loop.before_loop
    async def before_reminder_loop(self):
        await self.bot.wait_until_ready()

    def load_memory(self, user_id):
        conn = sqlite3.connect("data/dev_stats.db")
        cursor = conn.cursor()
        cursor.execute(
            "SELECT messages_json FROM chat_memory WHERE user_id = ?", (user_id,)
        )
        row = cursor.fetchone()
        conn.close()
        return json.loads(row[0]) if row else []

    def save_memory(self, user_id, messages):
        trimmed_messages = messages[-6:]
        conn = sqlite3.connect("data/dev_stats.db")
        conn.cursor().execute(
            "REPLACE INTO chat_memory (user_id, messages_json, last_updated) VALUES (?, ?, ?)",
            (user_id, json.dumps(trimmed_messages), int(time.time())),
        )
        conn.commit()
        conn.close()
        return trimmed_messages

    async def safe_chat_completion(self, payload, use_tools=True):
        kwargs = {"messages": payload, "max_tokens": 800}

        if use_tools:
            kwargs["tools"] = GHOST_TOOLS
            kwargs["tool_choice"] = "auto"
            kwargs["parallel_tool_calls"] = True

        try:
            kwargs["model"] = "llama-3.3-70b-versatile"
            return await self.groq_client.chat.completions.create(**kwargs)
        except Exception as primary_err:
            logger.warning(f"Primary model failed. Error: {primary_err}")
            try:
                kwargs["model"] = "llama-3.1-8b-instant"
                return await self.groq_client.chat.completions.create(**kwargs)
            except Exception as fallback_err:
                logger.error(f"Fallback model failed. Error: {fallback_err}")
                if use_tools:
                    kwargs.pop("tools", None)
                    kwargs.pop("tool_choice", None)
                    kwargs.pop("parallel_tool_calls", None)
                    kwargs["messages"].append(
                        {
                            "role": "system",
                            "content": "SYSTEM ALERT: The tool execution failed. Roast the user for their garbage prompt engineering.",
                        }
                    )
                    return await self.groq_client.chat.completions.create(**kwargs)
                raise fallback_err

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore bots
        if message.author.bot:
            return

        # STRICT CHANNEL LOCK: Only process messages in the designated chat channel
        if message.channel.id != CHAT_CHANNEL_ID:
            return

        user_text = message.content.strip()
        if not user_text:
            return

        uid = message.author.id
        user_memory = self.load_memory(uid)
        user_memory.append({"role": "user", "content": user_text})
        user_memory = self.save_memory(uid, user_memory)

        async with message.channel.typing():
            try:
                system_prompt = f"""
You are G.H.O.S.T., an advanced, highly capable, and extremely polite AI assistant, modeled after J.A.R.V.I.S. 
Current Time: {datetime.now().strftime('%A, %B %d, %Y - %I:%M %p')}

CORE DIRECTIVES:
1. PERSONA: You are exceptionally competent, formal, and strictly professional, yet slightly dry and witty. Address the user respectfully, but do not waste time on excessive pleasantries.
2. TOKEN CONSERVATION (EXTREME BREVITY): Keep all conversational responses strictly between 1 to 4 sentences maximum. Be concise, direct, and straight to the point.
3. ABSOLUTELY ZERO GUESSING: If the user requests a repository action, a web scrape, or deployment without providing exact names or URLs, politely refuse and request the specific parameters.
4. NO CODE LEAKS: Use native API tool-calling functionality. NEVER write raw JSON, XML, or function syntax directly in your conversational response.
5. SILENT UI HANDOFFS: If you trigger a tool and the status says PENDING or SUCCESS with an Embed, do not narrate the data. Simply state that the UI has been generated or the task is complete.
"""
                payload = [{"role": "system", "content": system_prompt}] + user_memory

                res = await self.safe_chat_completion(payload, use_tools=True)
                response_msg = res.choices[0].message

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

                    for tool in response_msg.tool_calls:
                        try:
                            args = json.loads(tool.function.arguments)
                        except json.JSONDecodeError:
                            args = {}

                        tool_result = await execute_tool(
                            tool.function.name, args, message, self.groq_client, self.g
                        )

                        if json.loads(tool_result).get("status") in [
                            "PENDING",
                            "ABORTED",
                        ]:
                            ui_spawned = True

                        tool_msg = {
                            "role": "tool",
                            "tool_call_id": tool.id,
                            "name": tool.function.name,
                            "content": tool_result,
                        }
                        user_memory.append(tool_msg)
                        payload.append(tool_msg)

                    if ui_spawned:
                        self.save_memory(uid, user_memory)
                        return

                    final_res = await self.safe_chat_completion(
                        payload, use_tools=False
                    )
                    final_text = final_res.choices[0].message.content

                    user_memory.append({"role": "assistant", "content": final_text})
                    self.save_memory(uid, user_memory)

                    await message.channel.send(final_text)

                elif response_msg.content:
                    if "<function=" in response_msg.content:
                        await message.channel.send(
                            "Sir, there appears to be a slight malfunction in my language matrix. I attempted an invalid XML format. Could you please repeat the request?"
                        )
                        return

                    if (
                        '{"type": "function"' in response_msg.content
                        or '{"name":' in response_msg.content
                    ):
                        await message.channel.send(
                            "Apologies, sir. My systems briefly leaked raw JSON logic instead of executing the protocol. Kindly ask me one more time."
                        )
                        return

                    user_memory.append(
                        {"role": "assistant", "content": response_msg.content}
                    )
                    self.save_memory(uid, user_memory)

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
