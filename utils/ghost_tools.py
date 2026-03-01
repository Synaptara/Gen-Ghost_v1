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
from ddgs import DDGS
from utils.ghost_ui import ChatDeleteConfirmView, CreateRepoView, DeployConfirmView

logger = logging.getLogger("GhostCommander")

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
            "description": "Generate code and deploy/push it to a GitHub repository (works for BOTH new and existing repositories). Use this whenever a user asks to write code and push it to a repo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_name": {
                        "type": "string",
                        "description": "The exact name. DO NOT GUESS OR INVENT PLACEHOLDERS like 'new-repo'.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The code or the description of the app the user wants to build.",
                    },
                    "private": {
                        "type": "boolean",
                    },
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
                "properties": {
                    "task_description": {
                        "type": "string",
                    }
                },
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
                    "role": {
                        "type": "string",
                    },
                    "location": {
                        "type": "string",
                    },
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
                "properties": {
                    "query": {
                        "type": "string",
                    }
                },
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
]


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
        if function_name == "schedule_reminder":
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
                conn = sqlite3.connect("data/dev_stats.db")
                conn.cursor().execute(
                    "INSERT INTO reminders (user_id, task, trigger_time) VALUES (?, ?, ?)",
                    (message.author.id, task, int(time.time()) + (delay * 60)),
                )
                conn.commit()
            except Exception as e:
                logger.error(f"Reminder DB Error: {e}")
                return json.dumps(
                    {"status": "ERROR", "message": "Database encountered an error."}
                )
            finally:
                conn.close()

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
            return json.dumps(
                {
                    "status": "PENDING",
                    "message": "UI spawned. Waiting for user interaction.",
                }
            )

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
                    f"❌ **Repository Not Found.** I am unable to locate a repository named `{repo_name}` on your GitHub account, sir."
                )
                return json.dumps({"status": "ABORTED", "message": "Repo not found."})

            embed = discord.Embed(
                title="⚠️ Repository Deletion Protocol",
                description=f"Warning: You are about to permanently delete **{repo.name}**. Shall I proceed with the deletion protocol?",
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
                            "message": "SYSTEM ALERT: Inform the user that no repositories were found.",
                        }
                    )

                repo_list = "\n".join([f"• [{r.name}]({r.html_url})" for r in repos])

                embed = discord.Embed(
                    title="📂 Active Repositories",
                    description=repo_list,
                    color=discord.Color.dark_gray(),
                )
                await message.channel.send(embed=embed)

                return json.dumps(
                    {
                        "status": "SUCCESS",
                        "message": "SYSTEM ALERT: The repositories have been displayed in the UI. Provide a brief, formal confirmation.",
                    }
                )
            except Exception as e:
                logger.error(f"List Repos Error: {e}")
                return json.dumps(
                    {"status": "ERROR", "message": "GitHub API rejected the request."}
                )

        elif function_name == "generate_boilerplate":
            return json.dumps(
                {
                    "status": "ERROR",
                    "message": "SYSTEM ALERT: Inform the user to utilize the deploy_project tool or the slash command instead.",
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
                        {
                            "status": "ERROR",
                            "message": "SYSTEM ALERT: Inform the user that the bot is not operating within a valid local Git repository.",
                        }
                    )

                diff = (
                    subprocess.check_output("git diff", shell=True).decode().strip()
                    or "New untracked files"
                )

                comp = await groq_client.chat.completions.create(
                    messages=[
                        {
                            "role": "system",
                            "content": "You are G.H.O.S.T., an advanced AI system. Reply ONLY with a professional, single-line Git commit message summarizing the changes. No markdown.",
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
            except subprocess.CalledProcessError as e:
                logger.error(
                    f"Push Code Error: {e.stderr.decode() if hasattr(e, 'stderr') and e.stderr else e}"
                )
                return json.dumps(
                    {
                        "status": "ERROR",
                        "message": "Git push protocol failed. A merge conflict may be present.",
                    }
                )

        elif function_name == "check_workflow":
            repo_name = args.get("repo_name")
            if not repo_name:
                return json.dumps(
                    {
                        "status": "ERROR",
                        "message": "SYSTEM ALERT: Request the user to specify a repository name.",
                    }
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
                return json.dumps(
                    {
                        "status": "ERROR",
                        "message": f"SYSTEM ALERT: Inform the user that repository '{repo_name}' does not exist.",
                    }
                )

        elif function_name == "toggle_streak_guard":
            action = args.get("action")
            username = args.get("github_username")

            try:
                conn = sqlite3.connect("data/dev_stats.db")
                if action == "on":
                    if not username:
                        return json.dumps(
                            {
                                "status": "ERROR",
                                "message": "SYSTEM ALERT: Request the user to provide their GitHub username.",
                            }
                        )

                    conn.cursor().execute(
                        "REPLACE INTO alerts VALUES (?, ?, ?)",
                        (message.author.id, message.channel.id, username),
                    )
                    res = {"status": "SUCCESS", "action": "activated", "user": username}
                else:
                    conn.cursor().execute(
                        "DELETE FROM alerts WHERE user_id = ?", (message.author.id,)
                    )
                    res = {"status": "SUCCESS", "action": "deactivated"}
                conn.commit()
            except Exception:
                res = {
                    "status": "ERROR",
                    "message": "Database error while toggling guard.",
                }
            finally:
                conn.close()
            return json.dumps(res)

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

                return json.dumps(
                    {
                        "status": "SUCCESS",
                        "message": "SYSTEM ALERT: The system status UI has been generated. Provide a brief, formal confirmation of our operational status.",
                    }
                )
            except Exception:
                return json.dumps(
                    {
                        "status": "ERROR",
                        "message": "System diagnostic sensors failed to initialize.",
                    }
                )

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
                    "🛑 **Invalid Project Designation.** Sir, please provide a valid repository name for deployment."
                )
                return json.dumps(
                    {
                        "status": "ABORTED",
                        "message": "AI hallucinated a placeholder repo name. Instruct user to provide a real one.",
                    }
                )

            if len(content.strip()) < 5:
                await message.channel.send(
                    "🛑 **Missing Payload.** Sir, the content payload is missing. Please provide the required code or instructions."
                )
                return json.dumps(
                    {"status": "ABORTED", "message": "Missing content payload."}
                )

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
                    {
                        "status": "ERROR",
                        "message": "SYSTEM ALERT: Inform the user they provided an insufficient task description.",
                    }
                )

            try:
                conn = sqlite3.connect("data/dev_stats.db")
                conn.cursor().execute(
                    "INSERT INTO streak_backlog (user_id, prompt, status) VALUES (?, ?, 'PENDING')",
                    (message.author.id, task),
                )
                conn.commit()
            except Exception:
                return json.dumps(
                    {
                        "status": "ERROR",
                        "message": "Database anomaly occurred while saving the task.",
                    }
                )
            finally:
                conn.close()

            return json.dumps(
                {
                    "status": "SUCCESS",
                    "message": "Task has been successfully logged to the queue, sir.",
                }
            )

        elif function_name == "trigger_job_sweep":
            role = args.get("role", "Data Scientist")
            location = args.get("location", "Remote")
            return json.dumps(
                {
                    "status": "SUCCESS",
                    "message": f'SYSTEM ALERT: Instruct the user to use the slash command `/hunt role: "{role}" location: "{location}"` to execute the ATS visual job sweep UI.',
                }
            )

        elif function_name == "web_search":
            query = args.get("query")
            if not query or len(query.strip()) < 2:
                return json.dumps(
                    {
                        "status": "ERROR",
                        "message": "SYSTEM ALERT: Inform the user the search query was insufficient.",
                    }
                )

            def perform_search(q):
                try:
                    with DDGS() as ddgs:
                        return list(ddgs.text(q, max_results=5))
                except Exception:
                    return None

            results = await asyncio.to_thread(perform_search, query)

            if results is None:
                return json.dumps(
                    {
                        "status": "ERROR",
                        "message": "SYSTEM ALERT: The external search engine failed to respond.",
                    }
                )

            if not results:
                return json.dumps(
                    {
                        "status": "ERROR",
                        "message": "SYSTEM ALERT: No relevant results were found.",
                    }
                )

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

                embed = discord.Embed(
                    title="🔋 Core Power Diagnostics",
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

                await message.channel.send(embed=embed)

                return json.dumps(
                    {
                        "status": "SUCCESS",
                        "message": "SYSTEM ALERT: The power status UI has been generated. Do NOT repeat the exact numbers. Provide a brief, formal confirmation of our operational status.",
                    }
                )
            except Exception as e:
                logger.error(f"Battery Check Failed: {e}")
                return json.dumps(
                    {"status": "ERROR", "message": "Failed to read diagnostic sensors."}
                )

        return json.dumps(
            {
                "status": "ERROR",
                "message": "SYSTEM ALERT: An unrecognized protocol tool was invoked.",
            }
        )

    except Exception as e:
        logger.error(f"Execute Tool Critical Crash: {traceback.format_exc()}")
        return json.dumps(
            {
                "status": "ERROR",
                "message": "SYSTEM ALERT: The tool execution pipeline suffered a critical anomaly.",
            }
        )
