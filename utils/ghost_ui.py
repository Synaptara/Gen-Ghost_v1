import discord
import asyncio
import logging
import os
import subprocess
import tempfile
import traceback
import re

logger = logging.getLogger("GhostCommander")


# ==========================================
# DELETE CONFIRMATION VIEW (Chat-triggered)
# ==========================================
class ChatDeleteConfirmView(discord.ui.View):
    def __init__(self, repo, author_id):
        super().__init__(timeout=60)
        self.repo = repo
        self.author_id = author_id

    @discord.ui.button(label="YES, DELETE", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
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
            await interaction.edit_original_response(content=None, embed=embed, view=None)
        except Exception:
            logger.error(f"Failed to delete repo {self.repo.name}: {traceback.format_exc()}")
            await interaction.edit_original_response(
                content="❌ **GitHub blocked the nuke.** Did you mess up the permissions again?",
                embed=None,
                view=None,
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
            embed=None,
            view=self,
        )
        self.stop()


# ==========================================
# CREATE REPO CONFIRMATION VIEW
# ==========================================
class CreateRepoView(discord.ui.View):
    def __init__(self, github_client, repo_name, author_id, is_private=True):
        super().__init__(timeout=60)
        self.g = github_client
        self.repo_name = repo_name
        self.author_id = author_id
        self.is_private = is_private

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Do I look like I take orders from you? Ask the boss.", ephemeral=True
            )
            return False
        return True

    async def _create(self, interaction: discord.Interaction, is_private: bool):
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

            embed = discord.Embed(title="✅ Repository Created", color=discord.Color.green())
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
            await interaction.edit_original_response(content=None, embed=embed, view=None)

        except Exception as e:
            logger.error(f"Failed to create repo {self.repo_name}: {traceback.format_exc()}")
            if "422" in str(e) or "name already exists" in str(e).lower():
                await interaction.edit_original_response(
                    content=f"🙄 **Open your eyes.** You already have a repository named `{self.repo_name}`.",
                    embed=None,
                    view=None,
                )
            else:
                await interaction.edit_original_response(
                    content="🔥 **Creation Failed.** GitHub threw up. Check your API limits.",
                    embed=None,
                    view=None,
                )
        self.stop()

    @discord.ui.button(label="Confirm Creation ✅", style=discord.ButtonStyle.success)
    async def btn_confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._create(interaction, is_private=self.is_private)

    @discord.ui.button(label="Cancel ❌", style=discord.ButtonStyle.danger)
    async def btn_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="❌ Creation cancelled. My CPU cycles weep for the time you just wasted.",
            embed=None,
            view=self,
        )
        self.stop()


# ==========================================
# PUSH CONFIRMATION VIEW  ← NEW / FIXED
# ==========================================
class PushConfirmView(discord.ui.View):
    """
    Confirmation gate for git push operations.
    Displays the AI-generated commit message and requires explicit user approval
    before running git add → commit → push.
    """

    def __init__(self, commit_msg: str, author_id: int):
        super().__init__(timeout=60)
        self.commit_msg = commit_msg
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "🛑 Unauthorized. Only the commander can authorize this push.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Push to Origin", style=discord.ButtonStyle.success, emoji="📤")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="⏳ **Executing push sequence...** Stand by.",
            embed=None,
            view=self,
        )

        try:
            def _push():
                subprocess.run(
                    "git add .", shell=True, check=True,
                    stderr=subprocess.PIPE, stdout=subprocess.PIPE
                )
                subprocess.run(
                    f'git commit -m "{self.commit_msg}"', shell=True, check=True,
                    stderr=subprocess.PIPE, stdout=subprocess.PIPE
                )
                result = subprocess.run(
                    "git push", shell=True, check=True,
                    capture_output=True, text=True
                )
                return (result.stdout + result.stderr).strip()

            output = await asyncio.to_thread(_push)

            embed = discord.Embed(
                title="✅ Push Successful",
                color=discord.Color.green(),
            )
            embed.add_field(
                name="💬 Commit Message", value=f"`{self.commit_msg}`", inline=False
            )
            if output:
                embed.add_field(
                    name="📡 Git Output",
                    value=f"```sh\n{output[:800]}\n```",
                    inline=False,
                )
            await interaction.edit_original_response(content=None, embed=embed, view=None)

        except subprocess.CalledProcessError as e:
            # Decode error safely — stderr is bytes here
            err_raw = e.stderr if isinstance(e.stderr, str) else (e.stderr or b"").decode(errors="replace")
            err = err_raw.strip()[:1000]
            logger.error(f"Git push failed: {err}")
            await interaction.edit_original_response(
                content=f"🔥 **Push Failed.** Git threw a tantrum.\n```sh\n{err}\n```",
                embed=None,
                view=None,
            )
        self.stop()

    @discord.ui.button(label="Abort Push", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="🛑 **Push Aborted.** Nothing was committed. Nothing was pushed. You're safe.",
            embed=None,
            view=self,
        )
        self.stop()


# ==========================================
# DEPLOY DROPDOWN + VIEW
# ==========================================
class DeployDropdown(discord.ui.Select):
    def __init__(self, parent_view):
        options = [
            discord.SelectOption(
                label="Deploy & Push Code",
                description="Generate and push this code to GitHub",
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
    def __init__(self, github_client, groq_client, repo_name, content, author_id, is_private=True):
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
            main_prompt = (
                f"Write a complete, professional Python script for: '{self.content}'. "
                "Output ONLY valid Python code. No markdown blocks."
            )
            readme_prompt = (
                f"Write a professional README.md for a project about: '{self.content}'. "
                "Make it look like a senior developer wrote it."
            )

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
                    main_res.choices[0].message.content
                    .replace("```python", "").replace("```", "").strip()
                )
                readme_text = read_res.choices[0].message.content.strip()

                if not python_code:
                    raise ValueError("AI returned empty Python code.")

            except Exception as ai_e:
                logger.error(f"Groq API error during deployment: {ai_e}")
                return await interaction.edit_original_response(
                    content="🔥 **The AI completely brain-dumped.** Your prompt was probably garbage or the API is rate-limiting."
                )

            await interaction.edit_original_response(
                content="⏳ **Phase 2/3:** Interrogating GitHub for repository status..."
            )

            try:
                user = await asyncio.to_thread(self.g.get_user)
            except Exception:
                return await interaction.edit_original_response(
                    content="🔥 **GitHub rejected my connection.** Your GITHUB_TOKEN is dead or expired."
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
                        content=f"🔥 **GitHub refused to create `{self.repo_name}`.** Check the name."
                    )

            await interaction.edit_original_response(
                content="⏳ **Phase 3/3:** Forcibly shoving code into your repository..."
            )

            auth_url = f"https://{self.token}@github.com/{user.login}/{self.repo_name}.git"

            try:
                def _git_push():
                    with tempfile.TemporaryDirectory() as temp_dir:
                        with open(os.path.join(temp_dir, "main.py"), "w", encoding="utf-8") as f:
                            f.write(python_code)
                        with open(os.path.join(temp_dir, "README.md"), "w", encoding="utf-8") as f:
                            f.write(readme_text)

                        def run(cmd, **kwargs):
                            subprocess.run(cmd, cwd=temp_dir, shell=True, check=True, capture_output=True, **kwargs)

                        run("git init")
                        run('git config user.name "GhostCommander"')
                        run('git config user.email "ghost@commander.bot"')
                        run("git branch -M main")
                        run("git add .")
                        run('git commit -m "Ghost automated push"')
                        run(f"git remote add origin {auth_url}")
                        run("git push -u origin main --force")

                await asyncio.to_thread(_git_push)

            except subprocess.CalledProcessError as git_err:
                # FIX: Mask token from error output before logging or displaying
                err_raw = git_err.stderr.decode(errors="replace") if git_err.stderr else str(git_err)
                masked_err = err_raw.replace(self.token, "***TOKEN***") if self.token else err_raw
                logger.error(f"Git push failed: {masked_err}")
                return await interaction.edit_original_response(
                    content=f"🔥 **Git push failed.** Usually means your token lacks `repo` scopes.\n```sh\n{masked_err[:800]}\n```"
                )

            embed = discord.Embed(title="🚀 Deployment Successful", color=discord.Color.green())
            embed.add_field(name="Repository", value=f"`{self.repo_name}`", inline=True)
            embed.add_field(name="Action", value=repo_status, inline=True)
            embed.add_field(name="Link", value=f"[Open on GitHub]({repo.html_url})", inline=False)
            await interaction.edit_original_response(content=None, embed=embed, view=None)

        except Exception:
            logger.error(f"Deployment Catastrophe: {traceback.format_exc()}")
            await interaction.edit_original_response(
                content="🔥 **Absolute systemic meltdown during deployment.** Your code is not on GitHub.",
                embed=None,
                view=None,
            )
