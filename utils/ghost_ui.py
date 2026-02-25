import discord
import asyncio
import logging
import os
import subprocess
import tempfile
import traceback
import re

logger = logging.getLogger("GhostCommander")


class ChatDeleteConfirmView(discord.ui.View):
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
                "Back off. You don't have the security clearance to vaporize this repo.",
                ephemeral=True,
            )

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="⏳ Nuking repository from orbit... Stand by.",
            embed=None,
            view=self,
        )

        try:
            await asyncio.to_thread(self.repo.delete)
            embed = discord.Embed(
                title="✅ Trash Removed",
                description=f"🗑️ `{self.repo.name}` has been wiped from the face of the internet. Good riddance.",
                color=discord.Color.red(),
            )
            await interaction.edit_original_response(
                content=None, embed=embed, view=None
            )
        except Exception as e:
            logger.error(
                f"Failed to delete repo {self.repo.name}: {traceback.format_exc()}"
            )
            await interaction.edit_original_response(
                content=f"❌ **GitHub blocked the nuke.** Did you mess up the permissions again? \n`{e}`"
            )

    @discord.ui.button(label="NO, CANCEL", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message(
                "Stop clicking my buttons. You aren't the commander.", ephemeral=True
            )
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="🙄 Deletion aborted. Next time, don't press the button if you're going to chicken out.",
            view=self,
        )


class CreateRepoView(discord.ui.View):
    def __init__(self, github_client, repo_name, author_id, is_private=True):
        super().__init__(timeout=60)
        self.g = github_client
        self.repo_name = repo_name
        self.author_id = author_id
        self.is_private = is_private

    async def _create(self, interaction: discord.Interaction, is_private: bool):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message(
                "Do I look like I take orders from you? Ask the boss.", ephemeral=True
            )

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="⏳ Spawning your repository... try not to break it.",
            embed=None,
            view=self,
        )

        try:
            user = await asyncio.to_thread(self.g.get_user)
            repo = await asyncio.to_thread(
                user.create_repo, self.repo_name, private=is_private, auto_init=True
            )

            embed = discord.Embed(
                title="✅ Repository Created", color=discord.Color.green()
            )
            embed.add_field(name="Name", value=f"`{repo.name}`", inline=True)
            embed.add_field(
                name="Visibility",
                value="🔒 Private" if is_private else "🌐 Public",
                inline=True,
            )
            embed.add_field(
                name="Link",
                value=f"[Click to view on GitHub]({repo.html_url})",
                inline=False,
            )

            await interaction.edit_original_response(
                content=None, embed=embed, view=None
            )
        except Exception as e:
            logger.error(
                f"Failed to create repo {self.repo_name}: {traceback.format_exc()}"
            )
            if "422" in str(e) or "name already exists" in str(e).lower():
                await interaction.edit_original_response(
                    content=f"🙄 **Open your eyes.** You already have a repository named `{self.repo_name}`.",
                    embed=None,
                    view=None,
                )
            else:
                await interaction.edit_original_response(
                    content=f"🔥 **Creation Failed.** GitHub threw up. Check your API limits or your awful naming conventions.",
                    embed=None,
                    view=None,
                )

    @discord.ui.button(label="Confirm Creation ✅", style=discord.ButtonStyle.success)
    async def btn_confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._create(interaction, is_private=self.is_private)

    @discord.ui.button(label="Cancel ❌", style=discord.ButtonStyle.danger)
    async def btn_cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message(
                "You don't have clearance to cancel this.", ephemeral=True
            )
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="❌ Creation cancelled. My CPU cycles weep for the time you just wasted.",
            embed=None,
            view=self,
        )


class DeployDropdown(discord.ui.Select):
    def __init__(self, parent_view):
        options = [
            discord.SelectOption(
                label="Deploy & Push Code",
                description="Generate and shove this code into GitHub",
                emoji="🚀",
                value="deploy",
            ),
            discord.SelectOption(
                label="Cancel Operation",
                description="Abort this entire deployment",
                emoji="❌",
                value="cancel",
            ),
        ]
        super().__init__(
            placeholder="Make a decision. I don't have all day...",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.author_id:
            return await interaction.response.send_message(
                "Only the commander can authorize a deployment. Sit down.",
                ephemeral=True,
            )

        if self.values[0] == "cancel":
            for child in self.view.children:
                child.disabled = True
            return await interaction.response.edit_message(
                content="❌ Deployment aborted. Coward.", embed=None, view=self.view
            )

        if self.values[0] == "deploy":
            await self.parent_view.execute_deployment(interaction)


class DeployConfirmView(discord.ui.View):
    def __init__(
        self, github_client, groq_client, repo_name, content, author_id, is_private=True
    ):
        super().__init__(timeout=120)
        self.g = github_client
        self.groq = groq_client
        self.repo_name = repo_name
        self.content = content
        self.author_id = author_id
        self.is_private = is_private
        self.token = os.getenv("GITHUB_TOKEN")

        self.add_item(DeployDropdown(self))

    async def execute_deployment(self, interaction: discord.Interaction):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="⏳ **Phase 1/3:** Writing the code because you are too lazy to do it yourself...",
            embed=None,
            view=self,
        )

        try:
            main_prompt = f"Write a complete, professional Python script for the following request or code snippet: '{self.content}'. Output ONLY valid, clean Python code. No markdown formatting blocks. Do your job."
            readme_prompt = f"Write a highly professional README.md for a project about: '{self.content}'. Make it look like a senior developer wrote it."

            try:
                main_res = await self.groq.chat.completions.create(
                    messages=[{"role": "user", "content": main_prompt}],
                    model="llama-3.3-70b-versatile",
                    max_tokens=3000,
                )
                read_res = await self.groq.chat.completions.create(
                    messages=[{"role": "user", "content": readme_prompt}],
                    model="llama-3.3-70b-versatile",
                    max_tokens=2000,
                )

                python_code = (
                    main_res.choices[0]
                    .message.content.replace("```python", "")
                    .replace("```", "")
                    .strip()
                )
                readme_text = read_res.choices[0].message.content.strip()

                if not python_code:
                    raise ValueError("AI returned empty Python code.")

            except Exception as ai_e:
                logger.error(f"Groq API choked during deployment generation: {ai_e}")
                return await interaction.edit_original_response(
                    content="🔥 **The AI completely brain-dumped.** Your prompt was probably garbage, or the API is rate-limiting me. Try explaining your app like I'm a 5-year-old."
                )

            await interaction.edit_original_response(
                content="⏳ **Phase 2/3:** Interrogating GitHub for repository status..."
            )

            try:
                user = await asyncio.to_thread(self.g.get_user)
            except Exception:
                return await interaction.edit_original_response(
                    content="🔥 **GitHub rejected my connection.** Your GITHUB_TOKEN is either dead, expired, or you don't actually know what you're doing."
                )

            repo_status = "Created New"
            try:
                repo = await asyncio.to_thread(
                    user.create_repo,
                    self.repo_name,
                    private=self.is_private,
                    auto_init=False,
                )
            except Exception as e:
                if "422" in str(e) or "name already exists" in str(e).lower():
                    repo = await asyncio.to_thread(user.get_repo, self.repo_name)
                    repo_status = "Updated Existing"
                else:
                    logger.error(f"Repo logic failed: {e}")
                    return await interaction.edit_original_response(
                        content=f"🔥 **GitHub refused to create the repository.** Check the name `{self.repo_name}`. It's probably invalid."
                    )

            await interaction.edit_original_response(
                content="⏳ **Phase 3/3:** Forcibly shoving code from my RAM into your repository..."
            )

            auth_url = (
                f"https://{self.token}@github.com/{user.login}/{self.repo_name}.git"
            )

            try:
                with tempfile.TemporaryDirectory() as temp_dir:
                    with open(
                        os.path.join(temp_dir, "main.py"), "w", encoding="utf-8"
                    ) as f:
                        f.write(python_code)
                    with open(
                        os.path.join(temp_dir, "README.md"), "w", encoding="utf-8"
                    ) as f:
                        f.write(readme_text)

                    subprocess.run(
                        "git init",
                        cwd=temp_dir,
                        shell=True,
                        check=True,
                        capture_output=True,
                    )
                    subprocess.run(
                        'git config user.name "GhostCommander"',
                        cwd=temp_dir,
                        shell=True,
                        check=True,
                        capture_output=True,
                    )
                    subprocess.run(
                        'git config user.email "ghost@commander.bot"',
                        cwd=temp_dir,
                        shell=True,
                        check=True,
                        capture_output=True,
                    )
                    subprocess.run(
                        "git branch -M main",
                        cwd=temp_dir,
                        shell=True,
                        capture_output=True,
                    )
                    subprocess.run(
                        "git add .",
                        cwd=temp_dir,
                        shell=True,
                        check=True,
                        capture_output=True,
                    )
                    subprocess.run(
                        'git commit -m "Ghost automated push"',
                        cwd=temp_dir,
                        shell=True,
                        check=True,
                        capture_output=True,
                    )
                    subprocess.run(
                        f"git remote add origin {auth_url}",
                        cwd=temp_dir,
                        shell=True,
                        capture_output=True,
                    )
                    subprocess.run(
                        "git push -u origin main --force",
                        cwd=temp_dir,
                        shell=True,
                        check=True,
                        capture_output=True,
                    )

            except subprocess.CalledProcessError as git_err:
                error_msg = (
                    git_err.stderr.decode().strip() if git_err.stderr else str(git_err)
                )
                logger.error(f"Git push failed: {error_msg}")
                return await interaction.edit_original_response(
                    content=f"🔥 **Git threw a massive tantrum.** The push failed. This is usually because your GitHub token lacks 'repo' scopes.\n```sh\n{error_msg[:1000]}\n```"
                )

            embed = discord.Embed(
                title="🚀 Deployment Successful", color=discord.Color.green()
            )
            embed.add_field(name="Repository", value=f"`{self.repo_name}`", inline=True)
            embed.add_field(name="Action", value=repo_status, inline=True)
            embed.add_field(
                name="Link", value=f"[Open on GitHub]({repo.html_url})", inline=False
            )
            await interaction.edit_original_response(
                content=None, embed=embed, view=None
            )

        except Exception as e:
            logger.error(f"Deployment Catastrophe: {traceback.format_exc()}")
            await interaction.edit_original_response(
                content="🔥 **Absolute systemic meltdown during deployment.** I caught the crash so the bot didn't die, but your code is definitely not on GitHub.",
                embed=None,
                view=None,
            )
