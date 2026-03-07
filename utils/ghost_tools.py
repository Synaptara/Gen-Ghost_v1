import os
import json
import sqlite3
import time
import psutil
import asyncio
import subprocess
import discord
import traceback
import logging
import re
from datetime import datetime
from ddgs import DDGS
from utils.ghost_ui import ChatDeleteConfirmView, CreateRepoView, DeployConfirmView

logger = logging.getLogger("GhostCommander")

# ==========================================
# SECURITY SANDBOX & UI VIEWS
# ==========================================
# Lock file I/O to the current working directory of the bot
ALLOWED_BASE_DIR = os.path.abspath(os.getcwd())


def is_safe_path(target_path: str) -> bool:
    """Prevents Path Traversal (e.g., reading /etc/passwd or ../../.env)"""
    if not target_path:
        return False
    abs_target = os.path.abspath(target_path)
    return abs_target.startswith(ALLOWED_BASE_DIR)


class TerminalConfirmView(discord.ui.View):
    """Gatekeeper UI for Terminal Execution"""

    def __init__(self, command: str, user_id: int):
        super().__init__(timeout=60.0)
        self.command = command
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "🛑 Unauthorized. Only the commander can authorize terminal execution.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="Execute Command", style=discord.ButtonStyle.danger, emoji="⚠️"
    )
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.defer()
        self.clear_items()

        await interaction.followup.edit_message(
            message_id=interaction.message.id,
            content="🔄 **Executing in host terminal...**",
            view=self,
            embed=None,
        )

        try:
            # Run command asynchronously to prevent blocking the bot
            def _run():
                return subprocess.check_output(
                    self.command, shell=True, stderr=subprocess.STDOUT, text=True
                )

            output = await asyncio.to_thread(_run)
            output = (
                output[:1900] + "\n...[truncated]" if len(output) > 1900 else output
            )
            if not output.strip():
                output = "[Command executed successfully with no output]"

            await interaction.followup.edit_message(
                message_id=interaction.message.id,
                content=f"✅ **Execution Complete**\n```bash\n{output}\n```",
                view=self,
            )
        except subprocess.CalledProcessError as e:
            err = e.output[:1900] if e.output else str(e)
            await interaction.followup.edit_message(
                message_id=interaction.message.id,
                content=f"❌ **Execution Failed**\n```bash\n{err}\n```",
                view=self,
            )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.clear_items()
        await interaction.response.edit_message(
            content="🛑 **Terminal Execution Aborted.**", view=self, embed=None
        )
        self.stop()


# ==========================================
# TOOL REGISTRY SCHEMA
# ==========================================
GHOST_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "schedule_reminder",
            "description": "Schedule a future reminder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "delay_minutes": {"type": "integer"},
                },
                "required": ["task", "delay_minutes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_repository",
            "description": "Initiate the creation of a GitHub repository. ONLY call this if the user explicitly provided a name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The exact name. DO NOT GUESS OR INVENT PLACEHOLDERS like 'new-repo'.",
                    },
                    "private": {"type": "boolean"},
                },
                "required": ["name", "private"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_repository",
            "description": "Initiate the deletion of a GitHub repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The EXACT name of the repository. Do NOT include words like 'repo' or 'repository'.",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_repositories",
            "description": "List top 10 updated GitHub repositories.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_boilerplate",
            "description": "Generate Python boilerplate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string"},
                    "model": {
                        "type": "string",
                        "enum": ["llama-3.1-8b-instant", "llama-3.3-70b-versatile"],
                    },
                },
                "required": ["project_name", "model"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "push_code",
            "description": "Push local code to GitHub.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_workflow",
            "description": "Check GitHub Actions workflows.",
            "parameters": {
                "type": "object",
                "properties": {"repo_name": {"type": "string"}},
                "required": ["repo_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "toggle_streak_guard",
            "description": "Toggle GitHub streak reminder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["on", "off"]},
                    "github_username": {"type": "string"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_status",
            "description": "Check CPU and RAM status.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deploy_project",
            "description": "Generate code and deploy/push it to a GitHub repository. Use this whenever a user asks to write code and push it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_name": {"type": "string"},
                    "content": {"type": "string"},
                    "private": {"type": "boolean"},
                },
                "required": ["repo_name", "content", "private"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_backlog",
            "description": "Add Python questions or tasks to the daily auto-streaker backlog.",
            "parameters": {
                "type": "object",
                "properties": {"task_description": {"type": "string"}},
                "required": ["task_description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_job_sweep",
            "description": "Trigger an ATS job sweep for a specific role and location.",
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {"type": "string"},
                    "location": {"type": "string"},
                },
                "required": ["role", "location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the live internet for up-to-date documentation, news, or debugging info.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_battery",
            "description": "Check the API token quota (battery level), remaining requests, and estimated messages left for today.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    # 🟢 OMNI-AGENT GOD MODE TOOLS 🟢
    {
        "type": "function",
        "function": {
            "name": "query_logs",
            "description": "Query your internal SQLite audit log to recall past actions, executed tools, scraped jobs, or generated repos.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "How many past actions to retrieve (default 10).",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a local file in the project directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The relative path to the file (e.g., 'main.py' or 'cogs/chat_agent.py')",
                    }
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write or overwrite a local file with new content. Use this to write code or modify files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "content": {
                        "type": "string",
                        "description": "The full exact content to write to the file.",
                    },
                },
                "required": ["file_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_terminal",
            "description": "Execute a bash/terminal command on the host server. A confirmation UI will appear for the user to approve.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The exact terminal command to run (e.g., 'ls -la', 'pip install x').",
                    }
                },
                "required": ["command"],
            },
        },
    },
]


# ==========================================
# TOOL EXECUTION ROUTER
# ==========================================
async def execute_tool(
    function_name: str, args: dict, message: discord.Message, groq_client, github_client
) -> str:
    bad_placeholders = [
        "none",
        "null",
        "test",
        "new-repo",
        "my-repo",
        "example-repo",
        "untitled",
        "missing",
        "repo-name",
        "undefined",
        "project",
    ]

    try:
        # 🟢 NEW: OMNI-AGENT CAPABILITIES 🟢

        if function_name == "query_logs":
            limit = args.get("limit", 10)
            try:

                def _fetch_logs():
                    with sqlite3.connect("data/dev_stats.db") as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            "SELECT action, timestamp, details FROM action_logs ORDER BY timestamp DESC LIMIT ?",
                            (limit,),
                        )
                        return cursor.fetchall()

                rows = await asyncio.to_thread(_fetch_logs)

                logs = []
                for row in rows:
                    logs.append(
                        {
                            "action": row[0],
                            "time": datetime.fromtimestamp(row[1]).strftime(
                                "%Y-%m-%d %H:%M:%S"
                            ),
                            "details": json.loads(row[2]) if row[2] else {},
                        }
                    )
                return json.dumps({"status": "SUCCESS", "logs": logs})
            except Exception as e:
                logger.error(f"Log Query Failed: {e}")
                return json.dumps(
                    {"status": "ERROR", "message": "Failed to access audit logs."}
                )

        elif function_name == "read_file":
            file_path = args.get("file_path", "")
            if not is_safe_path(file_path):
                return json.dumps(
                    {
                        "status": "ERROR",
                        "message": "SECURITY BREACH: Path traversal detected. Access Denied.",
                    }
                )

            try:

                def _read():
                    with open(file_path, "r", encoding="utf-8") as f:
                        return f.read()

                content = await asyncio.to_thread(_read)

                # Protect context limits
                if len(content) > 12000:
                    content = (
                        content[:12000] + "\n...[TRUNCATED TO PREVENT TOKEN OVERFLOW]"
                    )
                return json.dumps({"status": "SUCCESS", "content": content})
            except FileNotFoundError:
                return json.dumps(
                    {
                        "status": "ERROR",
                        "message": f"File '{file_path}' does not exist.",
                    }
                )
            except Exception as e:
                return json.dumps({"status": "ERROR", "message": str(e)})

        elif function_name == "write_file":
            file_path = args.get("file_path", "")
            content = args.get("content", "")
            if not is_safe_path(file_path):
                return json.dumps(
                    {
                        "status": "ERROR",
                        "message": "SECURITY BREACH: Path traversal detected. Access Denied.",
                    }
                )

            try:

                def _write():
                    abs_path = os.path.abspath(file_path)
                    dirname = os.path.dirname(abs_path)
                    if dirname:
                        os.makedirs(dirname, exist_ok=True)
                    with open(abs_path, "w", encoding="utf-8") as f:
                        f.write(content)

                await asyncio.to_thread(_write)
                return json.dumps(
                    {
                        "status": "SUCCESS",
                        "message": f"File '{file_path}' successfully updated/created.",
                    }
                )
            except Exception as e:
                return json.dumps({"status": "ERROR", "message": str(e)})

        elif function_name == "execute_terminal":
            command = args.get("command")
            if not command:
                return json.dumps(
                    {"status": "ERROR", "message": "No command provided."}
                )

            embed = discord.Embed(
                title="⚠️ Terminal Execution Authorization",
                description=f"I have determined that running this shell command is necessary. Do you authorize this action, sir?",
                color=discord.Color.red(),
            )
            embed.add_field(
                name="Target Command", value=f"```bash\n{command}\n```", inline=False
            )

            await message.channel.send(
                embed=embed, view=TerminalConfirmView(command, message.author.id)
            )
            return json.dumps(
                {
                    "status": "PENDING",
                    "message": "Terminal UI spawned. Waiting for user authorization. Do not narrate the output until the user confirms.",
                }
            )

        # ==========================================
        # EXISTING TOOLS
        # ==========================================
        elif function_name == "schedule_reminder":
            task = args.get("task")
            delay = args.get("delay_minutes", 0)

            if not task or not isinstance(delay, int) or delay <= 0:
                await message.channel.send(
                    "🛑 **Invalid Parameters.** Sir, please provide a clear task and a delay duration greater than zero."
                )
                return json.dumps(
                    {
                        "status": "ABORTED",
                        "message": "User provided invalid input parameters.",
                    }
                )

            try:

                def _save_reminder():
                    with sqlite3.connect("data/dev_stats.db") as conn:
                        conn.cursor().execute(
                            "INSERT INTO reminders (user_id, task, trigger_time) VALUES (?, ?, ?)",
                            (message.author.id, task, int(time.time()) + (delay * 60)),
                        )
                        conn.commit()

                await asyncio.to_thread(_save_reminder)
            except Exception as e:
                logger.error(f"Reminder DB Error: {e}")
                return json.dumps(
                    {"status": "ERROR", "message": "Database encountered an error."}
                )

            return json.dumps(
                {
                    "status": "SUCCESS",
                    "message": f"Protocol confirmed. I will notify you regarding '{task}' in {delay} minutes, sir.",
                }
            )

        elif function_name == "create_repository":
            repo_name = args.get("name", "")
            is_private = args.get("private", True)
            if (
                not repo_name
                or repo_name.lower() in bad_placeholders
                or not re.match(r"^[a-zA-Z0-9_-]+$", repo_name)
            ):
                await message.channel.send(
                    "🛑 **Invalid Nomenclature.** Sir, please provide a specific and valid repository name."
                )
                return json.dumps(
                    {
                        "status": "ABORTED",
                        "message": "AI hallucinated a placeholder repo name. Instruct user to provide a real one.",
                    }
                )

            visibility = "Private 🔒" if is_private else "Public 🌐"
            embed = discord.Embed(
                title="📦 Repository Initialization",
                description=f"Awaiting confirmation to spawn a new **{visibility}** repository designated as **{repo_name}**, sir.",
                color=discord.Color.blurple(),
            )
            await message.channel.send(
                embed=embed,
                view=CreateRepoView(
                    github_client, repo_name, message.author.id, is_private
                ),
            )
            return json.dumps({"status": "PENDING", "message": "UI spawned."})

        elif function_name == "delete_repository":
            raw_name = args.get("name", "")
            repo_name = (
                raw_name.lower().replace(" repo", "").replace(" repository", "").strip()
            )

            if not repo_name:
                await message.channel.send(
                    "🛑 **Missing Parameters.** Sir, please specify the exact repository name you wish to delete."
                )
                return json.dumps(
                    {"status": "ABORTED", "message": "Missing repository name."}
                )

            try:
                repo = await asyncio.to_thread(
                    github_client.get_user().get_repo, repo_name
                )
            except Exception:
                await message.channel.send(
                    f"❌ **Repository Not Found.** I am unable to locate `{repo_name}`, sir."
                )
                return json.dumps({"status": "ABORTED", "message": "Repo not found."})

            embed = discord.Embed(
                title="⚠️ Repository Deletion Protocol",
                description=f"Warning: You are about to permanently delete **{repo.name}**. Shall I proceed?",
                color=discord.Color.red(),
            )
            await message.channel.send(
                embed=embed, view=ChatDeleteConfirmView(repo, message.author.id)
            )
            return json.dumps({"status": "PENDING", "message": "UI spawned."})

        elif function_name == "list_repositories":
            try:
                repos = await asyncio.to_thread(
                    lambda: list(
                        github_client.get_user().get_repos(
                            sort="updated", direction="desc"
                        )[:10]
                    )
                )
                if not repos:
                    return json.dumps(
                        {
                            "status": "SUCCESS",
                            "message": "Inform user no repositories were found.",
                        }
                    )

                repo_list = "\n".join([f"• [{r.name}]({r.html_url})" for r in repos])
                embed = discord.Embed(
                    title="📂 Active Repositories",
                    description=repo_list,
                    color=discord.Color.dark_gray(),
                )
                await message.channel.send(embed=embed)
                return json.dumps({"status": "SUCCESS", "message": "UI Displayed."})
            except Exception as e:
                logger.error(f"List Repos Error: {e}")
                return json.dumps(
                    {"status": "ERROR", "message": "GitHub API rejected request."}
                )

        elif function_name == "generate_boilerplate":
            return json.dumps(
                {
                    "status": "ERROR",
                    "message": "Instruct user to utilize the deploy_project tool.",
                }
            )

        elif function_name == "push_code":
            try:
                try:
                    subprocess.check_output(
                        "git status", shell=True, stderr=subprocess.STDOUT
                    )
                except subprocess.CalledProcessError:
                    return json.dumps(
                        {"status": "ERROR", "message": "Not a valid git repository."}
                    )

                diff = (
                    subprocess.check_output("git diff", shell=True).decode().strip()
                    or "New untracked files"
                )
                comp = await groq_client.chat.completions.create(
                    messages=[
                        {
                            "role": "system",
                            "content": "Generate a single-line git commit message. No markdown.",
                        },
                        {"role": "user", "content": diff[:2000]},
                    ],
                    model="llama-3.3-70b-versatile",
                )
                msg_cmt = comp.choices[0].message.content.strip().replace('"', "")
                subprocess.run(
                    "git add .", shell=True, check=True, stderr=subprocess.PIPE
                )
                subprocess.run(
                    f'git commit -m "{msg_cmt}"',
                    shell=True,
                    check=True,
                    stderr=subprocess.PIPE,
                )
                subprocess.run(
                    "git push", shell=True, check=True, stderr=subprocess.PIPE
                )
                return json.dumps({"status": "SUCCESS", "commit_message": msg_cmt})
            except subprocess.CalledProcessError:
                return json.dumps({"status": "ERROR", "message": "Git push failed."})

        elif function_name == "check_workflow":
            repo_name = args.get("repo_name")
            if not repo_name:
                return json.dumps(
                    {"status": "ERROR", "message": "Request repository name."}
                )
            try:
                repo = await asyncio.to_thread(
                    github_client.get_user().get_repo, repo_name
                )
                runs = await asyncio.to_thread(
                    lambda: list(repo.get_workflow_runs()[:3])
                )
                run_data = [
                    {"name": r.name, "status": r.conclusion, "branch": r.head_branch}
                    for r in runs
                ]
                return json.dumps({"status": "SUCCESS", "workflows": run_data})
            except Exception:
                return json.dumps({"status": "ERROR", "message": "Repo not found."})

        elif function_name == "toggle_streak_guard":
            action = args.get("action")
            username = args.get("github_username")
            try:

                def _toggle():
                    with sqlite3.connect("data/dev_stats.db") as conn:
                        if action == "on":
                            conn.cursor().execute(
                                "REPLACE INTO alerts VALUES (?, ?, ?)",
                                (message.author.id, message.channel.id, username),
                            )
                        else:
                            conn.cursor().execute(
                                "DELETE FROM alerts WHERE user_id = ?",
                                (message.author.id,),
                            )
                        conn.commit()

                if action == "on" and not username:
                    return json.dumps(
                        {"status": "ERROR", "message": "Request GitHub username."}
                    )
                await asyncio.to_thread(_toggle)
                return json.dumps({"status": "SUCCESS", "action": action})
            except Exception:
                return json.dumps({"status": "ERROR", "message": "Database error."})

        elif function_name == "get_system_status":
            try:
                cpu = psutil.cpu_percent(interval=0.5)
                ram = psutil.virtual_memory().percent
                embed = discord.Embed(
                    title="📊 AWS System Health",
                    color=(
                        discord.Color.green()
                        if cpu < 90 and ram < 90
                        else discord.Color.red()
                    ),
                )
                embed.add_field(name="⚙️ CPU Usage", value=f"`{cpu}%`", inline=True)
                embed.add_field(name="🧠 RAM Usage", value=f"`{ram}%`", inline=True)
                await message.channel.send(embed=embed)
                return json.dumps({"status": "SUCCESS", "message": "UI Generated."})
            except Exception:
                return json.dumps({"status": "ERROR", "message": "Sensors failed."})

        elif function_name == "deploy_project":
            repo_name = args.get("repo_name", "")
            content = args.get("content", "")
            is_private = args.get("private", True)

            if (
                not repo_name
                or repo_name.lower() in bad_placeholders
                or not re.match(r"^[a-zA-Z0-9_-]+$", repo_name)
            ):
                await message.channel.send(
                    "🛑 **Invalid Project Designation.** Sir, please provide a valid repository name."
                )
                return json.dumps(
                    {"status": "ABORTED", "message": "Invalid repo name."}
                )
            if len(content.strip()) < 5:
                await message.channel.send("🛑 **Missing Payload.**")
                return json.dumps({"status": "ABORTED", "message": "Missing content."})

            embed = discord.Embed(
                title="⚙️ Deployment Authorization",
                description=f"Authorization required to generate and deploy code to **{repo_name}**, sir. Shall I proceed?",
                color=discord.Color.orange(),
            )
            await message.channel.send(
                embed=embed,
                view=DeployConfirmView(
                    github_client,
                    groq_client,
                    repo_name,
                    content,
                    message.author.id,
                    is_private,
                ),
            )
            return json.dumps(
                {"status": "PENDING", "message": "Deployment UI Spawned."}
            )

        elif function_name == "add_to_backlog":
            task = args.get("task_description")
            if not task or len(task.strip()) < 5:
                return json.dumps(
                    {"status": "ERROR", "message": "Insufficient description."}
                )
            try:

                def _add_task():
                    with sqlite3.connect("data/dev_stats.db") as conn:
                        conn.cursor().execute(
                            "INSERT INTO streak_backlog (user_id, prompt, status) VALUES (?, ?, 'PENDING')",
                            (message.author.id, task),
                        )
                        conn.commit()

                await asyncio.to_thread(_add_task)
                return json.dumps({"status": "SUCCESS", "message": "Task logged."})
            except Exception:
                return json.dumps({"status": "ERROR", "message": "Database anomaly."})

        elif function_name == "trigger_job_sweep":
            role = args.get("role", "Data Scientist")
            location = args.get("location", "Remote")
            return json.dumps(
                {
                    "status": "SUCCESS",
                    "message": f'Instruct user to use `/hunt role: "{role}" location: "{location}"`',
                }
            )

        elif function_name == "web_search":
            query = args.get("query")
            if not query or len(query.strip()) < 2:
                return json.dumps({"status": "ERROR", "message": "Insufficient query."})

            def perform_search(q):
                try:
                    with DDGS() as ddgs:
                        return list(ddgs.text(q, max_results=5))
                except Exception:
                    return None

            results = await asyncio.to_thread(perform_search, query)
            if results is None:
                return json.dumps(
                    {"status": "ERROR", "message": "Search engine failed."}
                )
            if not results:
                return json.dumps({"status": "ERROR", "message": "No results found."})

            formatted_results = [
                {
                    "title": r.get("title", ""),
                    "snippet": r.get("body", ""),
                    "url": r.get("href", ""),
                }
                for r in results
            ]
            return json.dumps({"status": "SUCCESS", "results": formatted_results})

        elif function_name == "check_battery":
            try:
                raw_response = (
                    await groq_client.chat.completions.with_raw_response.create(
                        model="llama-3.1-8b-instant",
                        messages=[{"role": "user", "content": "."}],
                        max_tokens=1,
                    )
                )
                headers = raw_response.headers
                req_rem = int(
                    headers.get("x-ratelimit-remaining-requests")
                    or headers.get("x-ratelimit-remaining-requests-day", 0)
                )
                req_limit = int(
                    headers.get("x-ratelimit-limit-requests")
                    or headers.get("x-ratelimit-limit-requests-day", 1)
                )
                tok_rem = int(
                    headers.get("x-ratelimit-remaining-tokens")
                    or headers.get("x-ratelimit-remaining-tokens-day", 0)
                )
                tok_limit = int(
                    headers.get("x-ratelimit-limit-tokens")
                    or headers.get("x-ratelimit-limit-tokens-day", 1)
                )

                battery_pct = (
                    round((tok_rem / tok_limit) * 100, 1) if tok_limit > 0 else 0
                )
                est_msgs = tok_rem // 800

                color = discord.Color.green()
                if battery_pct < 40:
                    color = discord.Color.orange()
                if battery_pct < 10:
                    color = discord.Color.red()

                embed = discord.Embed(title="🔋 Core Power Diagnostics", color=color)
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
                await message.channel.send(embed=embed)
                return json.dumps({"status": "SUCCESS", "message": "UI Generated."})
            except Exception as e:
                logger.error(f"Battery Check Failed: {e}")
                return json.dumps(
                    {"status": "ERROR", "message": "Failed to read diagnostic sensors."}
                )

        return json.dumps({"status": "ERROR", "message": "Unrecognized tool."})

    except Exception as e:
        logger.error(f"Execute Tool Crash: {traceback.format_exc()}")
        return json.dumps({"status": "ERROR", "message": "Tool pipeline anomaly."})
