import discord
from discord.ext import commands
from discord import app_commands
from groq import AsyncGroq
import os
import subprocess
import py_compile
import glob
import re
import logging
import traceback
import asyncio

logger = logging.getLogger("GhostCommander")


# ==========================================
# CONFIRMATION UI VIEW
# ==========================================
class PushConfirmView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=60.0)
        self.user_id = user_id
        self.value = None
        self.interaction = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Ensure only the command initiator can click the buttons."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "🛑 Unauthorized. Only the user who initiated the push can confirm it.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="Confirm Push", style=discord.ButtonStyle.green, emoji="✅"
    )
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.value = True
        self.interaction = interaction
        self.clear_items()
        await interaction.response.edit_message(
            content="🔄 **Pushing to GitHub...** Please wait.", view=self
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        self.interaction = interaction
        self.clear_items()
        await interaction.response.edit_message(
            content="🛑 **Push Sequence Aborted.**", view=self
        )
        self.stop()

    async def on_timeout(self):
        """Handle the 60-second expiration."""
        self.value = False
        self.clear_items()
        # Fallback if timeout happens without any button press
        pass


class AIDevAgent(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.groq_client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))

    # ==========================================
    # ASYNC HELPERS & SAFETY WRAPPERS
    # ==========================================
    async def _run_command(self, cmd: list[str]) -> str:
        """Safely executes system commands asynchronously without shell=True."""

        def _execute():
            return subprocess.check_output(
                cmd, stderr=subprocess.STDOUT, text=True
            ).strip()

        return await asyncio.to_thread(_execute)

    async def _validate_code_async(self) -> list[str]:
        """Validates Python files asynchronously to prevent event loop blocking."""

        def _validate():
            # Checks recursively for comprehensive coverage
            python_files = glob.glob("**/*.py", recursive=True)
            errors = []
            for file in python_files:
                try:
                    py_compile.compile(file, doraise=True)
                except py_compile.PyCompileError as e:
                    errors.append(f"**{file}**: {e.msg}")
            return errors

        return await asyncio.to_thread(_validate)

    def _sanitize_commit_message(self, msg: str) -> str:
        """Strips markdown, line breaks, and limits length."""
        # Remove markdown blocks, backticks, and quotes
        msg = re.sub(r"```[a-z]*\n|```", "", msg)
        msg = msg.replace('"', "").replace("'", "").replace("`", "")
        # Force onto a single line
        msg = re.sub(r"[\r\n]+", " | ", msg.strip())
        return msg[:72]  # Standard git commit subject length limit

    # ==========================================
    # COMMAND: PUSH
    # ==========================================
    @app_commands.command(
        name="push",
        description="Validates code, generates an AI commit message, and securely pushes to GitHub",
    )
    @app_commands.default_permissions(
        administrator=True
    )  # Security: Server Admins only
    async def push_code(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)

        # 1. Syntax Validation
        errors = await self._validate_code_async()
        if errors:
            error_text = "\n".join(errors)[:1500]  # Limit output size
            truncation_notice = (
                "\n...[truncated]" if len("\n".join(errors)) > 1500 else ""
            )
            return await interaction.followup.send(
                f"❌ **Syntax Validation Failed.** Cannot deploy until these errors are resolved:\n{error_text}{truncation_notice}"
            )

        try:
            # 2. Check Git Status
            status = await self._run_command(["git", "status", "--porcelain"])
            if not status:
                return await interaction.followup.send(
                    "🛑 **No Changes Detected.** The working tree is clean, sir."
                )

            # 3. Retrieve Diff
            try:
                # Use HEAD to get diff of both staged and unstaged files
                diff = await self._run_command(["git", "diff", "HEAD"])
                if not diff:
                    diff = f"Untracked files modified/added:\n{status}"
            except subprocess.CalledProcessError:
                diff = f"Untracked or new repository state:\n{status}"

            # 4. Generate AI Commit Message
            try:
                completion = await self.groq_client.chat.completions.create(
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a senior DevOps AI. Generate a SINGLE-LINE, strict conventional commit message based on the provided diff. Do NOT use markdown, quotes, or conversational text. Output ONLY the commit message.",
                        },
                        {
                            "role": "user",
                            "content": f"Generate commit message for:\n\n{diff[:2000]}",
                        },
                    ],
                    model="llama-3.3-70b-versatile",
                    max_tokens=30,
                    temperature=0.2,  # Lower temperature for deterministic formatting
                )
                raw_commit_msg = completion.choices[0].message.content
                commit_msg = self._sanitize_commit_message(raw_commit_msg)
            except Exception as e:
                logger.error(f"Groq API commit gen failed: {e}")
                commit_msg = "chore: automated deployment fallback"

            # 5. Confirmation UI
            embed = discord.Embed(
                title="⚠️ Deployment Authorization Required",
                description="Please review the AI-generated commit message before pushing to production.",
                color=discord.Color.brand_green(),
            )
            embed.add_field(
                name="Commit Message Preview", value=f"`{commit_msg}`", inline=False
            )
            embed.set_footer(
                text="This authorization request will expire in 60 seconds."
            )

            view = PushConfirmView(interaction.user.id)
            prompt_msg = await interaction.followup.send(embed=embed, view=view)

            # Wait for user interaction
            await view.wait()

            # 6. Execution based on UI interaction
            if view.value is None:
                # Timeout occurred
                await prompt_msg.edit(
                    content="⏳ **Authorization Expired.** Push sequence cancelled.",
                    view=None,
                    embed=None,
                )
                return
            elif view.value is False:
                # Handled inside the view class, but we exit here
                return

            # Proceed with Push Sequence
            # Run commands strictly as lists to prevent shell injection
            await self._run_command(["git", "add", "."])
            await self._run_command(["git", "commit", "-m", commit_msg])
            await self._run_command(["git", "push"])

            await view.interaction.followup.send(
                f"✅ **Deployment Successful.** The code has been securely pushed to the repository.\n📝 **Commit:** `{commit_msg}`"
            )

        except subprocess.CalledProcessError as e:
            err_output = e.output if hasattr(e, "output") else str(e)
            if "not a git repository" in err_output.lower():
                await interaction.followup.send(
                    "🛑 **Directory Error.** Not operating within a valid Git repository."
                )
            elif "no upstream branch" in err_output.lower():
                await interaction.followup.send(
                    "🛑 **Configuration Error.** No upstream branch configured. Please establish remote tracking manually."
                )
            else:
                await interaction.followup.send(
                    f"❌ **Git Subsystem Error:**\n```sh\n{err_output[:1000]}\n```"
                )
        except Exception as e:
            logger.error(f"Push Code Crash: {traceback.format_exc()}")
            await interaction.followup.send(
                "🔥 **Critical Failure.** An unexpected error occurred during the deployment sequence."
            )

    # ==========================================
    # COMMAND: BOILERPLATE
    # ==========================================
    @app_commands.command(
        name="boilerplate",
        description="Generate a secure Python project template using AI",
    )
    @app_commands.choices(
        model_choice=[
            app_commands.Choice(name="LLaMA 3.1 (Fast)", value="llama-3.1-8b-instant"),
            app_commands.Choice(
                name="LLaMA 3.3 (Complex)", value="llama-3.3-70b-versatile"
            ),
        ]
    )
    async def boilerplate(
        self,
        interaction: discord.Interaction,
        project_name: str,
        model_choice: app_commands.Choice[str],
    ):
        await interaction.response.defer()

        # 1. Strict Security: Sanitize and Prevent Traversal
        if not re.match(r"^[a-zA-Z0-9_-]+$", project_name):
            return await interaction.followup.send(
                "🛑 **Invalid Nomenclature.** Use only alphanumeric characters, dashes, and underscores to prevent path traversal."
            )

        # 2. Safety: Prevent accidental overwrites
        if os.path.exists(project_name):
            return await interaction.followup.send(
                f"🛑 **Directory Collision.** A folder named `{project_name}` already exists. Aborting to prevent data loss."
            )

        try:
            main_prompt = f"Write a clean, professional starter Python script for '{project_name}'. Return ONLY valid Python code. No markdown formatting blocks. Do not explain the code."
            readme_prompt = f"Write a professional README.md for '{project_name}'. Include Setup and Usage sections."

            # Run LLM calls concurrently for speed
            try:
                main_coro = self.groq_client.chat.completions.create(
                    messages=[{"role": "user", "content": main_prompt}],
                    model=model_choice.value,
                    max_tokens=2000,
                    temperature=0.3,
                )
                readme_coro = self.groq_client.chat.completions.create(
                    messages=[{"role": "user", "content": readme_prompt}],
                    model=model_choice.value,
                    max_tokens=2000,
                    temperature=0.3,
                )

                # Await both simultaneously
                main_completion, readme_completion = await asyncio.gather(
                    main_coro, readme_coro
                )

                main_code = (
                    main_completion.choices[0]
                    .message.content.replace("```python", "")
                    .replace("```", "")
                    .strip()
                )
                readme_text = readme_completion.choices[0].message.content.strip()
            except Exception as e:
                logger.error(f"Groq API failed during boilerplate gen: {e}")
                return await interaction.followup.send(
                    "🛑 **API Connectivity Issue.** Unable to interface with the language generation matrix."
                )

            # 3. Secure Async File Writing
            def _write_files():
                os.makedirs(
                    project_name, exist_ok=False
                )  # exist_ok=False reinforces collision prevention
                with open(
                    os.path.join(project_name, "main.py"), "w", encoding="utf-8"
                ) as f:
                    f.write(main_code)
                with open(
                    os.path.join(project_name, "README.md"), "w", encoding="utf-8"
                ) as f:
                    f.write(readme_text)

            await asyncio.to_thread(_write_files)

            await interaction.followup.send(
                f"📁 **Initialization Complete.** The architecture for `{project_name}` has been safely constructed."
            )

        except OSError as e:
            logger.error(f"File System Error: {e}")
            await interaction.followup.send(
                "🛑 **Filesystem Error.** Operating system permissions denied or path resolution failed."
            )
        except Exception as e:
            logger.error(f"Boilerplate Crash: {traceback.format_exc()}")
            await interaction.followup.send(
                "🔥 **Critical Failure.** An unexpected error occurred while compiling the boilerplate."
            )


async def setup(bot):
    await bot.add_cog(AIDevAgent(bot))
