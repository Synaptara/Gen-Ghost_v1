import os
import logging
from datetime import datetime, timezone
from notion_client import AsyncClient

logger = logging.getLogger("ghost_tracker")
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_JOB_DB_ID = os.getenv("NOTION_JOB_DB_ID")

notion = AsyncClient(auth=NOTION_API_KEY)

# Cache for the new Notion API architecture
_cached_job_ds_id = None


async def get_job_data_source_id():
    """Fetches the new hidden data_source_id from the parent Database."""
    global _cached_job_ds_id
    if _cached_job_ds_id:
        return _cached_job_ds_id

    try:
        db = await notion.databases.retrieve(database_id=NOTION_JOB_DB_ID)
        _cached_job_ds_id = db["data_sources"][0]["id"]
        return _cached_job_ds_id
    except Exception as e:
        logger.error(f"Failed to fetch job data_source_id: {e}")
        return None


async def check_job_exists(url: str) -> bool:
    """Checks if a job URL already exists in the database to prevent duplicates."""
    ds_id = await get_job_data_source_id()
    if not ds_id:
        return False

    try:
        response = await notion.data_sources.query(
            data_source_id=ds_id,
            filter={"property": "Application Link", "url": {"equals": url}},
        )
        return len(response.get("results", [])) > 0
    except Exception as e:
        logger.error(f"Notion DB Query Error: {e}")
        return False


async def add_job_to_notion(title: str, company: str, url: str) -> str | None:
    """Adds a newly discovered job to the Notion matrix."""
    ds_id = await get_job_data_source_id()
    if not ds_id:
        return None

    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        response = await notion.pages.create(
            parent={"type": "data_source_id", "data_source_id": ds_id},
            properties={
                "Job Title": {"title": [{"text": {"content": title}}]},
                "Company": {"rich_text": [{"text": {"content": company}}]},
                "Application Link": {"url": url},
                "Date Found": {"date": {"start": now_iso}},
                "Status": {"select": {"name": "Not Applied"}},
            },
        )
        return response["id"]
    except Exception as e:
        logger.error(f"Failed to add job to Notion: {e}")
        return None


async def update_job_status(page_id: str, status: str) -> bool:
    """Updates the job status (e.g., Applied, Rejected)."""
    try:
        await notion.pages.update(
            page_id=page_id, properties={"Status": {"select": {"name": status}}}
        )
        return True
    except Exception as e:
        logger.error(f"Failed to update job status: {e}")
        return False
