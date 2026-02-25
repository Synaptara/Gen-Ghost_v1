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

logger = logging.getLogger("GhostCommander")


class AIDevAgent(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.groq_client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))

    def validate_code(self):
        python_files = glob.glob("*.py")
        errors = []
        for file in python_files:
            try:
                py_compile.compile(file, doraise=True)
            except py_compile.PyCompileError as e:
                errors.append(f"**{file}**: {e.msg}")
        return errors

    @app_commands.command(
        name="push",
        description="Validates code, generates an AI commit message, and pushes to GitHub",
    )
    async def push_code(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)

        errors = self.validate_code()
        if errors:
            error_text = "\n".join(errors)
            return await interaction.followup.send(
                f"❌ **Validation Failed.** Sir, I cannot push this code to production until the following syntax errors are resolved:\n{error_text}"
            )

        try:
            status = (
                subprocess.check_output(
                    "git status --porcelain", shell=True, stderr=subprocess.STDOUT
                )
                .decode()
                .strip()
            )

            if not status:
                return await interaction.followup.send(
                    "🛑 **No Changes Detected.** There are currently no uncommitted modifications in the working directory, sir."
                )

            try:
                diff = (
                    subprocess.check_output(
                        "git diff", shell=True, stderr=subprocess.STDOUT
                    )
                    .decode()
                    .strip()
                )
                if not diff:
                    diff = "New untracked files added."
            except subprocess.CalledProcessError:
                diff = "New untracked files added."

            try:
                completion = await self.groq_client.chat.completions.create(
                    messages=[
                        {
                            "role": "system",
                            "content": "You are G.H.O.S.T., a highly efficient AI system. Reply ONLY with a valid, single-line professional git commit message summarizing the changes. No quotes, no markdown, no conversational filler.",
                        },
                        {
                            "role": "user",
                            "content": f"Write a commit message for this:\n\n{diff[:2000]}",
                        },
                    ],
                    model="llama-3.3-70b-versatile",
                    max_tokens=50,
                )
                commit_msg = (
                    completion.choices[0]
                    .message.content.strip()
                    .replace('"', "")
                    .replace("'", "")
                )
            except Exception as e:
                logger.error(f"Groq API failed during commit gen: {e}")
                commit_msg = "Automated fallback commit: System anomaly detected during generation."

            subprocess.run("git add .", shell=True, check=True, stderr=subprocess.PIPE)
            subprocess.run(
                f'git commit -m "{commit_msg}"',
                shell=True,
                check=True,
                stderr=subprocess.PIPE,
            )
            subprocess.run("git push", shell=True, check=True, stderr=subprocess.PIPE)

            await interaction.followup.send(
                f"✅ **Deployment Successful.** The code has been securely pushed to the repository, sir.\n📝 **Commit Message:** `{commit_msg}`"
            )

        except subprocess.CalledProcessError as e:
            err_output = (
                e.stderr.decode().strip() if getattr(e, "stderr", None) else str(e)
            )
            if "not a git repository" in err_output.lower():
                await interaction.followup.send(
                    "🛑 **Directory Error.** It appears the bot is not currently operating within a valid Git repository, sir."
                )
            elif "no upstream branch" in err_output.lower():
                await interaction.followup.send(
                    "🛑 **Configuration Error.** There is no upstream branch configured. Please establish the remote tracking manually first."
                )
            else:
                await interaction.followup.send(
                    f"❌ **Git Subsystem Error:**\n```sh\n{err_output[:1000]}\n```"
                )
        except Exception as e:
            logger.error(f"Push Code Crash: {traceback.format_exc()}")
            await interaction.followup.send(
                "🔥 **Critical Failure.** An unexpected error occurred during the deployment sequence. Please review my terminal diagnostics."
            )

    @app_commands.command(
        name="boilerplate", description="Generate a Python project template using AI"
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

        if not re.match(r"^[a-zA-Z0-9_-]+$", project_name):
            return await interaction.followup.send(
                "🛑 **Invalid Nomenclature.** Please use only alphanumeric characters, dashes, and underscores for the project directory name, sir."
            )

        try:
            main_prompt = f"Write a clean, professional starter Python script for '{project_name}'. Return ONLY valid Python code. No markdown formatting blocks."
            readme_prompt = f"Write a professional README.md for '{project_name}'. Include Setup and Usage sections."

            try:
                main_completion = await self.groq_client.chat.completions.create(
                    messages=[{"role": "user", "content": main_prompt}],
                    model=model_choice.value,
                    max_tokens=2000,
                )
                main_code = (
                    main_completion.choices[0]
                    .message.content.replace("```python", "")
                    .replace("```", "")
                    .strip()
                )

                readme_completion = await self.groq_client.chat.completions.create(
                    messages=[{"role": "user", "content": readme_prompt}],
                    model=model_choice.value,
                    max_tokens=2000,
                )
                readme_text = readme_completion.choices[0].message.content.strip()
            except Exception as e:
                logger.error(f"Groq API failed during boilerplate gen: {e}")
                return await interaction.followup.send(
                    "🛑 **API Connectivity Issue.** I am currently unable to interface with the language generation matrix. We may be experiencing rate limits."
                )

            os.makedirs(project_name, exist_ok=True)
            with open(
                os.path.join(project_name, "main.py"), "w", encoding="utf-8"
            ) as f:
                f.write(main_code)
            with open(
                os.path.join(project_name, "README.md"), "w", encoding="utf-8"
            ) as f:
                f.write(readme_text)

            await interaction.followup.send(
                f"📁 **Initialization Complete.** The boilerplate architecture for `{project_name}` has been successfully generated and saved to the local drive, sir."
            )

        except OSError as e:
            logger.error(f"File System Error: {e}")
            await interaction.followup.send(
                "🛑 **Permission Denied.** I lack the necessary operating system permissions to create this directory structure. Please verify my access rights."
            )
        except Exception as e:
            logger.error(f"Boilerplate Crash: {traceback.format_exc()}")
            await interaction.followup.send(
                "🔥 **Critical Failure.** An unexpected systemic error occurred while compiling the boilerplate."
            )


async def setup(bot):
    await bot.add_cog(AIDevAgent(bot))
