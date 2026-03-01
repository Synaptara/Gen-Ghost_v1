import aiohttp
from bs4 import BeautifulSoup
import logging
import asyncio
import re
from ddgs import DDGS

logger = logging.getLogger("GhostCommander")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# 1. Global Cache & Config
MAX_CONCURRENCY = 5
MAX_RETRIES = 2
_url_cache = set()


def is_entry_level(title: str) -> bool:
    title_lower = title.lower()
    senior_terms = [
        r"\bsenior\b",
        r"\bsr\.?\b",
        r"\blead\b",
        r"\bprincipal\b",
        r"\bstaff\b",
        r"\bmanager\b",
        r"\bdirector\b",
        r"\bhead\b",
        r"\bvp\b",
        r"\bii\b",
        r"\biii\b",
        r"\biv\b",
        r"\bexperienced\b",
    ]
    return not any(re.search(term, title_lower) for term in senior_terms)


def match_role_and_location(
    job_title: str, job_loc: str, target_role: str, target_loc: str
) -> bool:
    """Flexible matching using regex and partial word overlaps."""
    target_role_clean = target_role.replace('"', "").lower()
    target_loc_clean = target_loc.replace('"', "").lower()
    title_lower = job_title.lower()
    loc_lower = job_loc.lower()

    # Role Match: Check if any major keyword from the target role exists in the title
    role_keywords = [w for w in target_role_clean.split() if len(w) > 2]
    role_match = (
        any(word in title_lower for word in role_keywords)
        or target_role_clean in title_lower
    )

    # Location Match: Check for explicit remote or target location
    loc_match = (
        "remote" in loc_lower
        or "worldwide" in loc_lower
        or "anywhere" in loc_lower
        or target_loc_clean in loc_lower
    )

    return role_match and loc_match


async def fetch_html(
    session: aiohttp.ClientSession, semaphore: asyncio.Semaphore, url: str
) -> str | None:
    """Fetches HTML with Semaphore concurrency limit, retries, and backoff."""
    clean_url = url.split("?")[0]

    # 2. In-Memory Caching (Prevents double-fetching)
    if clean_url in _url_cache:
        return None
    _url_cache.add(clean_url)

    async with semaphore:
        for attempt in range(MAX_RETRIES + 1):
            try:
                async with session.get(clean_url, headers=HEADERS, timeout=15) as resp:
                    if resp.status == 200:
                        return await resp.text()
                    else:
                        logger.warning(f"HTTP {resp.status} - {clean_url}")
                        if resp.status in [403, 404]:
                            return None  # Do not retry hard blocks or missing pages
            except Exception as e:
                if attempt < MAX_RETRIES:
                    backoff = 2**attempt
                    logger.info(
                        f"Fetch failed, retrying {clean_url} in {backoff}s... (Attempt {attempt+1})"
                    )
                    await asyncio.sleep(backoff)
                else:
                    logger.error(f"Max retries reached for {clean_url}: {e}")
    return None


async def extract_ats_data(
    session: aiohttp.ClientSession, semaphore: asyncio.Semaphore, url: str
) -> dict | None:
    html = await fetch_html(session, semaphore, url)
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    title, company = "Unknown Role", "Unknown Company"

    # 3. Robust ATS Parsing with Fallbacks
    try:
        if "greenhouse.io" in url:
            title_el = soup.find("h1", class_="app-title") or soup.find("h1")
            company_el = soup.find("span", class_="company-name") or soup.find("h2")
            if title_el:
                title = title_el.text.strip()
            if company_el:
                company = company_el.text.replace("at", "").strip()

        elif "lever.co" in url:
            title_el = soup.find("h2") or soup.find("h1")
            if title_el:
                title = title_el.text.strip()
            page_title = soup.title.string if soup.title else ""
            company = (
                page_title.split("-")[0].strip()
                if "-" in page_title
                else page_title.strip()
            )

        elif "ashbyhq.com" in url:
            title_el = soup.find("h1")
            if title_el:
                title = title_el.text.strip()
            page_title = soup.title.string if soup.title else ""
            company = (
                page_title.split("-")[0].strip()
                if "-" in page_title
                else "Unknown Company"
            )

        elif "workable.com" in url or "breezy.hr" in url:
            title_el = soup.find("h1") or soup.find("h2", class_="title")
            company_el = soup.find("h2") or soup.find("strong")
            if title_el:
                title = title_el.text.strip()
            if company_el:
                company = company_el.text.strip()

        return {"title": title, "company": company, "link": url}
    except Exception as e:
        logger.error(f"ATS Parsing error for {url}: {e}")
        return None


def execute_dork_search(role: str, location: str) -> list:
    clean_role = role.replace('"', "").replace("'", "").strip()
    clean_location = location.replace('"', "").replace("'", "").strip()
    query = f'"{clean_role}" "{clean_location}" (site:boards.greenhouse.io OR site:jobs.lever.co OR site:jobs.ashbyhq.com OR site:apply.workable.com OR site:breezy.hr)'
    links = []
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=30))
            for r in results:
                href = r.get("href")
                if href:
                    links.append(href)
    except Exception as e:
        logger.error(f"DDGS Search Error: {e}")
    return links


async def fetch_remoteok(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    role: str,
    location: str,
) -> list:
    url = "https://remoteok.com/api"
    jobs = []

    # RemoteOK API is a single call, but we still pass it through the semaphore
    async with semaphore:
        try:
            async with session.get(url, headers=HEADERS, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    for item in data[1:]:  # Skip the first metadata item
                        title = item.get("position", "")
                        company = item.get("company", "")
                        link = item.get("url", "")
                        job_loc = item.get("location", "")

                        if match_role_and_location(title, job_loc, role, location):
                            jobs.append(
                                {"title": title, "company": company, "link": link}
                            )
        except Exception as e:
            logger.error(f"RemoteOK API Error: {e}")
    return jobs


async def fetch_wwr(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    role: str,
    location: str,
) -> list:
    url = "https://weworkremotely.com/categories/remote-programming-jobs.rss"
    jobs = []
    html = await fetch_html(session, semaphore, url)
    if not html:
        return jobs

    try:
        soup = BeautifulSoup(html, "html.parser")
        for item in soup.find_all("item"):
            title_full = item.find("title").text if item.find("title") else ""
            link = item.find("link").text if item.find("link") else ""
            category = item.find("category").text if item.find("category") else ""

            parts = title_full.split(":", 1)
            company = parts[0].strip() if len(parts) == 2 else "Unknown Company"
            title = parts[1].strip() if len(parts) == 2 else title_full.strip()

            if match_role_and_location(title, category, role, location):
                jobs.append({"title": title, "company": company, "link": link.strip()})
    except Exception as e:
        logger.error(f"WWR RSS Error: {e}")
    return jobs


async def sweep_jobs(role: str, location: str) -> list:
    """Main execution function utilizing a shared aiohttp session and concurrency limits."""
    # 4. Clear cache at the start of a new sweep
    _url_cache.clear()

    # Fetch Dork Links (Blocking I/O moved to thread)
    links = await asyncio.to_thread(execute_dork_search, role, location)
    valid_ats = [
        "greenhouse.io",
        "lever.co",
        "ashbyhq.com",
        "workable.com",
        "breezy.hr",
    ]
    filtered_links = [l for l in links if any(ats in l for ats in valid_ats)]

    final_jobs = []
    seen_links = set()  # 5. Deduplication set

    # 6. Shared Session & Semaphore implementation
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async with aiohttp.ClientSession() as session:
        # Create asynchronous tasks for all ATS links
        ats_tasks = [
            extract_ats_data(session, semaphore, link) for link in filtered_links
        ]

        # Add API/RSS tasks
        rok_task = fetch_remoteok(session, semaphore, role, location)
        wwr_task = fetch_wwr(session, semaphore, role, location)

        # Execute all HTTP requests concurrently (throttled by semaphore)
        results = await asyncio.gather(
            *ats_tasks, rok_task, wwr_task, return_exceptions=True
        )

        # Process ATS results
        ats_results = results[:-2]
        for job_data in ats_results:
            if isinstance(job_data, dict) and job_data["title"] != "Unknown Role":
                if job_data["link"] not in seen_links and is_entry_level(
                    job_data["title"]
                ):
                    seen_links.add(job_data["link"])
                    final_jobs.append(job_data)

        # Process API/RSS results (they return lists of dicts)
        for api_job_list in results[-2:]:
            if isinstance(api_job_list, list):
                for job_data in api_job_list:
                    if job_data["link"] not in seen_links and is_entry_level(
                        job_data["title"]
                    ):
                        seen_links.add(job_data["link"])
                        final_jobs.append(job_data)

    logger.info(
        f"Sweep complete. Extracted {len(final_jobs)} deduplicated, entry-level jobs."
    )
    return final_jobs
