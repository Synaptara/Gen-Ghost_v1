import discord
import aiohttp
from bs4 import BeautifulSoup
import logging
import json
import traceback
import re

logger = logging.getLogger("GhostCommander")


async def safe_ai_call(
    groq_client, prompt, model="llama-3.3-70b-versatile", response_format=None
):
    kwargs = {
        "messages": [
            {
                "role": "system",
                "content": "You are G.H.O.S.T., an advanced, highly efficient AI data extractor. Extract exactly what is requested with absolute precision. Strictly limit your text response to a maximum of 3 sentences. Zero conversational filler.",
            },
            {"role": "user", "content": prompt},
        ],
        "model": model,
        "max_tokens": 800,
    }
    if response_format:
        kwargs["response_format"] = response_format

    try:
        res = await groq_client.chat.completions.create(**kwargs)
        return res.choices[0].message.content
    except Exception as e:
        logger.warning(f"Scraper AI Primary Model choked: {e}")
        try:
            kwargs["model"] = "llama-3.1-8b-instant"
            res = await groq_client.chat.completions.create(**kwargs)
            return res.choices[0].message.content
        except Exception as fallback_e:
            logger.error(f"Scraper AI Fallback Model choked: {fallback_e}")
            raise Exception(
                "Both primary and fallback language models failed to process the target text."
            )


class ScraperDropdown(discord.ui.Select):
    def __init__(self, raw_text, groq_client, url, dynamic_actions):
        self.raw_text = raw_text
        self.groq = groq_client
        self.url = url
        self.action_prompts = {}

        options = []
        for i, action in enumerate(dynamic_actions):
            val = str(i)
            self.action_prompts[val] = action.get(
                "prompt", "Summarize this data concisely in under 3 sentences."
            )
            options.append(
                discord.SelectOption(
                    label=action.get("label", "Analyze")[:25],
                    description=action.get("description", "Perform data analysis")[:50],
                    emoji="⚡",
                    value=val,
                )
            )

        super().__init__(
            placeholder="Select a follow-up analysis protocol, sir...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        selected_action_prompt = self.action_prompts[self.values[0]]

        msg = await interaction.followup.send(
            "⏳ **Processing your request. Please stand by...**", ephemeral=True
        )
        full_prompt = f"{selected_action_prompt}\n\nCONTENT:\n{self.raw_text[:8000]}"

        try:
            answer = await safe_ai_call(self.groq, full_prompt)
            embed = discord.Embed(
                title="🔍 Analysis Complete",
                description=answer[:4096],
                color=discord.Color.dark_purple(),
            )
            await msg.edit(content=None, embed=embed)
        except Exception as e:
            logger.error(f"Scraper Dropdown Crash: {traceback.format_exc()}")
            await msg.edit(
                content="❌ **Processing Failed.** I was unable to complete the analysis on this specific data segment, sir."
            )


class ScraperProgressView(discord.ui.View):
    def __init__(self, raw_text, groq_client, url, dynamic_actions):
        super().__init__(timeout=300)
        self.add_item(ScraperDropdown(raw_text, groq_client, url, dynamic_actions))


async def execute_scraping(
    message: discord.Message, url: str, extraction_query: str, groq_client
):
    if not url or not re.match(
        r"^https?://[^\s/$.?#].[^\s]*$", url.strip(), re.IGNORECASE
    ):
        return await message.channel.send(
            "🛑 **Invalid URL.** Please provide a properly formatted HTTP or HTTPS link for the extraction protocol, sir."
        )

    embed = discord.Embed(
        title="🌐 Web Extraction Protocol",
        description="⏳ **Phase 1/4:** Establishing connection to the target server...",
        color=discord.Color.blurple(),
    )
    status_msg = await message.channel.send(embed=embed)

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=headers, timeout=15) as resp:
                    if resp.status == 403:
                        raise Exception(
                            "HTTP 403 Forbidden. The target server's firewall has blocked my access attempt."
                        )
                    elif resp.status == 404:
                        raise Exception(
                            "HTTP 404 Not Found. The requested page directory does not exist on the host."
                        )
                    elif resp.status != 200:
                        raise Exception(
                            f"Target server returned an unexpected HTTP {resp.status} status code."
                        )

                    html = await resp.text()
            except asyncio.TimeoutError:
                raise Exception(
                    "Timeout Error. The target server failed to respond within acceptable parameters."
                )

        embed.description = (
            "⏳ **Phase 2/4:** Parsing and sanitizing HTML data structures..."
        )
        await status_msg.edit(embed=embed)

        soup = BeautifulSoup(html, "html.parser")
        for script in soup(
            ["script", "style", "nav", "footer", "noscript", "header", "aside"]
        ):
            script.extract()

        text = soup.get_text(separator="\n", strip=True)
        text = re.sub(r"\n+", "\n", text)

        if len(text) < 50:
            raise Exception(
                "Data extraction failed. The page appears to be structurally empty or heavily reliant on client-side JavaScript execution."
            )

        embed.description = (
            "⏳ **Phase 3/4:** Executing primary query extraction matrix..."
        )
        await status_msg.edit(embed=embed)

        main_prompt = f"Extract exactly what is requested from this text: '{extraction_query}'. Ensure the response is highly concise and strictly under 4 sentences.\n\nTEXT:\n{text[:8000]}"
        final_text = await safe_ai_call(groq_client, main_prompt)

        embed.description = "⏳ **Phase 4/4:** Generating dynamic context actions..."
        await status_msg.edit(embed=embed)

        ui_prompt = f"Based on the text below, generate exactly 3 highly relevant follow-up questions or actions a developer might want to ask. Return ONLY a valid JSON array of objects. Each object must have 'label' (max 20 chars), 'description' (max 40 chars), and 'prompt' (the exact instruction for the AI, instructing it to answer in 3 sentences max). Text snippet: {text[:2000]}"

        try:
            ui_response = await safe_ai_call(
                groq_client,
                ui_prompt,
                model="llama-3.1-8b-instant",
                response_format={"type": "json_object"},
            )
            clean_json = ui_response.replace("```json", "").replace("```", "").strip()

            if clean_json.startswith("{") and "actions" in clean_json:
                dynamic_actions = json.loads(clean_json)["actions"]
            else:
                dynamic_actions = json.loads(clean_json)

            if not isinstance(dynamic_actions, list):
                raise ValueError(
                    "AI JSON parser failed to return a valid list structure."
                )

        except Exception as e:
            logger.warning(f"Dynamic UI Generation Failed: {e}")
            dynamic_actions = [
                {
                    "label": "Summarize Data",
                    "description": "Provide a brief summary of the extracted text",
                    "prompt": "Summarize the entirety of this content concisely in exactly 3 sentences.",
                },
                {
                    "label": "Extract URLs",
                    "description": "List all embedded hyperlinks and paths",
                    "prompt": "Provide a concise list of every URL, file path, or external link mentioned in the text.",
                },
            ]

        final_embed = discord.Embed(
            title="✅ Web Extraction Protocol Complete",
            description=final_text[:4096],
            color=discord.Color.green(),
        )
        final_embed.add_field(
            name="Target Source", value=f"[Access Original Data]({url})", inline=False
        )

        view = ScraperProgressView(text, groq_client, url, dynamic_actions)
        await status_msg.edit(embed=final_embed, view=view)

    except Exception as e:
        logger.error(f"Scraping Engine Catastrophic Failure: {traceback.format_exc()}")
        embed.description = f"❌ **Task Failed:**\n```py\n{e}\n```"
        embed.color = discord.Color.red()
        await status_msg.edit(embed=embed)
