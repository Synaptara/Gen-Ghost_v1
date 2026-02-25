import discord
from discord.ext import commands
from discord import app_commands
from github import Github
import os
import asyncio
import re
import logging
import traceback

logger = logging.getLogger("GhostCommander")


class DeleteConfirmView(discord.ui.View):
    def __init__(self, repo, author_id):
        super().__init__(timeout=60)
        self.repo = repo
        self.author_id = author_id

    @discord.ui.button(label="YES, DELETE", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message(
                "Keep your hands off. Only the command author can authorize this destruction.",
                ephemeral=True,
            )

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="⏳ Nuking repository from orbit... Stand by.", view=self
        )

        try:
            await asyncio.to_thread(self.repo.delete)
            await interaction.edit_original_response(
                content=f"✅ 🗑️ `{self.repo.name}` has been wiped from existence. I hope you backed that up."
            )
        except Exception as e:
            logger.error(f"Failed to delete repo: {traceback.format_exc()}")
            await interaction.edit_original_response(
                content="🔥 GitHub rejected the deletion request. Check your permissions or try doing it yourself for once."
            )

    @discord.ui.button(label="NO, CANCEL", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message(
                "Who asked you? Only the command author can cancel this.",
                ephemeral=True,
            )

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="🙄 Deletion aborted. Next time, make up your mind before wasting my CPU cycles.",
            view=self,
        )


class GitHubAdmin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.g = Github(os.getenv("GITHUB_TOKEN"))

    @app_commands.command(
        name="newrepo", description="Create a new GitHub repository instantly"
    )
    @app_commands.describe(
        name="The name of the repository", private="Should the repository be private?"
    )
    async def newrepo(
        self, interaction: discord.Interaction, name: str, private: bool = True
    ):
        await interaction.response.defer(ephemeral=True)

        if not name or not re.match(r"^[a-zA-Z0-9_-]+$", name):
            return await interaction.followup.send(
                "🛑 **Are you kidding me?** That repo name is garbage. Use letters, numbers, dashes, or underscores. I am not creating a repository named after a typo."
            )

        try:
            user = await asyncio.to_thread(self.g.get_user)

            try:
                await asyncio.to_thread(user.get_repo, name)
                return await interaction.followup.send(
                    f"🙄 **Open your eyes.** You already have a repository named `{name}`. I am not overwriting it. Pick another name."
                )
            except:
                pass

            repo = await asyncio.to_thread(
                user.create_repo, name, private=private, auto_init=True
            )

            await interaction.followup.send(
                f"✅ Fine. **{name}** is alive.\n🔗 {repo.html_url}\nNow go write some actual code for it."
            )

        except Exception as e:
            logger.error(f"Repo Creation Crash: {traceback.format_exc()}")
            await interaction.followup.send(
                "🔥 **Catastrophic failure.** GitHub threw a tantrum. I logged the error internally, but honestly, you probably messed up your API token."
            )

    @app_commands.command(
        name="deleterepo",
        description="Permanently delete a repository (Requires Confirmation)",
    )
    async def deleterepo(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)

        if not name or not re.match(r"^[a-zA-Z0-9_-]+$", name):
            return await interaction.followup.send(
                "🛑 **Learn to type.** That's not a valid repository name. What exactly did you expect me to delete?"
            )

        try:
            user = await asyncio.to_thread(self.g.get_user)
            repo = await asyncio.to_thread(user.get_repo, name)

            view = DeleteConfirmView(repo, interaction.user.id)
            await interaction.followup.send(
                f"⚠️ **WARNING**: You are about to permanently eradicate `{repo.name}`.\nI am not responsible for your lost code. Proceed?",
                view=view,
            )

        except Exception:
            await interaction.followup.send(
                f"❌ **Are you hallucinating?** I searched everywhere. `{name}` does not exist on your account. Try remembering your own project names."
            )

    @app_commands.command(
        name="myrepos", description="List your 10 most recently updated repositories"
    )
    async def myrepos(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            user = await asyncio.to_thread(self.g.get_user)

            repos = await asyncio.to_thread(
                lambda: list(user.get_repos(sort="updated", direction="desc")[:10])
            )

            if not repos:
                return await interaction.followup.send(
                    "🛑 **Wow. Empty.** You have zero repositories. Have you considered actually coding instead of just bothering me?"
                )

            repo_list = "\n".join([f"• [{r.name}]({r.html_url})" for r in repos])

            embed = discord.Embed(
                title="📂 Your So-Called Projects",
                description=repo_list,
                color=discord.Color.dark_gray(),
            )
            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Fetch Repos Crash: {traceback.format_exc()}")
            await interaction.followup.send(
                "🔥 **GitHub refused to answer me.** Your auth token is probably expired or you broke something else. I dropped the stack trace in the terminal."
            )


async def setup(bot):
    await bot.add_cog(GitHubAdmin(bot))
