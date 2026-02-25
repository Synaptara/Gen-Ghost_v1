import discord
from discord.ext import commands
from discord import app_commands
from groq import AsyncGroq
import os
import logging
import traceback

logger = logging.getLogger("GhostCommander")


class BatteryMonitor(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.groq_client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))

    @app_commands.command(
        name="battery",
        description="Check G.H.O.S.T. core power levels (Groq API Token Quota)",
    )
    async def check_battery_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)

        try:
            raw_response = (
                await self.groq_client.chat.completions.with_raw_response.create(
                    model="llama-3.1-8b-instant",
                    messages=[{"role": "user", "content": "."}],
                    max_tokens=1,
                )
            )
            headers = raw_response.headers

            req_rem = int(headers.get("x-ratelimit-remaining-requests-day", 0))
            req_limit = int(headers.get("x-ratelimit-limit-requests-day", 1))
            tok_rem = int(headers.get("x-ratelimit-remaining-tokens-day", 0))
            tok_limit = int(headers.get("x-ratelimit-limit-tokens-day", 1))

            battery_pct = round((tok_rem / tok_limit) * 100, 1) if tok_limit > 0 else 0
            est_msgs = tok_rem // 800

            color = discord.Color.green()
            if battery_pct < 40:
                color = discord.Color.orange()
            if battery_pct < 10:
                color = discord.Color.red()

            embed = discord.Embed(
                title="🔋 Core Power Diagnostics",
                description="Live telemetry from the Groq language matrix.",
                color=color,
            )
            embed.add_field(
                name="🔋 Power Level", value=f"`{battery_pct}%`", inline=True
            )
            embed.add_field(
                name="💬 Est. Messages Left", value=f"`~{est_msgs:,}`", inline=True
            )
            embed.add_field(
                name="⚡ Tokens Remaining",
                value=f"`{tok_rem:,} / {tok_limit:,}`",
                inline=False,
            )
            embed.add_field(
                name="📡 API Requests Left",
                value=f"`{req_rem:,} / {req_limit:,}`",
                inline=False,
            )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Battery Check Crash: {traceback.format_exc()}")
            await interaction.followup.send(
                "🛑 **Diagnostic Failure.** I am currently unable to read my own power sensors, sir."
            )


async def setup(bot):
    await bot.add_cog(BatteryMonitor(bot))
    