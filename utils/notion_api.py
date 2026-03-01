import os
import logging
from datetime import datetime, timezone
from notion_client import AsyncClient
from notion_client.errors import APIResponseError

logger = logging.getLogger("ghost_tracker")

NOTION_API_KEY = os.getenv("NOTION_API_KEY")
DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

notion = AsyncClient(auth=NOTION_API_KEY)

# Memory cache for the new Notion API 2025-09-03 Architecture
_cached_data_source_id = None


async def get_data_source_id():
    """Fetches the new hidden data_source_id from the parent Database."""
    global _cached_data_source_id
    if _cached_data_source_id:
        return _cached_data_source_id

    try:
        db = await notion.databases.retrieve(database_id=DATABASE_ID)
        _cached_data_source_id = db["data_sources"][0]["id"]
        return _cached_data_source_id
    except Exception as e:
        logger.error(f"Failed to fetch data_source_id: {e}")
        return None


async def add_task(day_title: str, topic: str) -> bool:
    ds_id = await get_data_source_id()
    if not ds_id:
        return False

    try:
        await notion.pages.create(
            parent={"type": "data_source_id", "data_source_id": ds_id},
            properties={
                "Day": {"title": [{"text": {"content": day_title}}]},
                "Topic": {"rich_text": [{"text": {"content": topic}}]},
                "Status": {"select": {"name": "Pending"}},
            },
        )
        return True
    except APIResponseError as e:
        logger.error(f"Notion API Error (add_task): {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected Error (add_task): {e}")
        return False


async def get_next_pending_task() -> dict | None:
    ds_id = await get_data_source_id()
    if not ds_id:
        return None

    try:
        response = await notion.data_sources.query(
            data_source_id=ds_id,
            filter={
                "or": [
                    {"property": "Status", "select": {"equals": "Pending"}},
                    {"property": "Status", "select": {"equals": "In-Progress"}},
                ]
            },
            sorts=[{"property": "Day", "direction": "ascending"}],
            page_size=1,
        )
        if not response["results"]:
            return None

        page = response["results"][0]

        day_title = "Unknown Day"
        if page["properties"]["Day"]["title"]:
            day_title = page["properties"]["Day"]["title"][0]["text"]["content"]

        topic = "No description provided."
        if page["properties"]["Topic"]["rich_text"]:
            topic = page["properties"]["Topic"]["rich_text"][0]["text"]["content"]

        return {
            "page_id": page["id"],
            "day": day_title,
            "topic": topic,
            "status": page["properties"]["Status"]["select"]["name"],
        }
    except APIResponseError as e:
        logger.error(f"Notion API Error (get_next_pending_task): {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected Error (get_next_pending_task): {e}")
        return None


async def update_task_status(page_id: str, status: str) -> bool:
    try:
        await notion.pages.update(
            page_id=page_id, properties={"Status": {"select": {"name": status}}}
        )
        return True
    except APIResponseError as e:
        logger.error(f"Notion API Error (update_task_status): {e}")
        return False


async def update_task_completion(page_id: str, time_spent_mins: int) -> bool:
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        await notion.pages.update(
            page_id=page_id,
            properties={
                "Status": {"select": {"name": "Completed"}},
                "Time Spent": {
                    "rich_text": [{"text": {"content": f"{time_spent_mins} mins"}}]
                },
                "Date Completed": {"date": {"start": now_iso}},
            },
        )
        return True
    except APIResponseError as e:
        logger.error(f"Notion API Error (update_task_completion): {e}")
        return False


async def get_progress_stats() -> dict:
    ds_id = await get_data_source_id()
    if not ds_id:
        return {"total": 0, "completed": 0, "in_progress": 0, "percentage": 0}

    try:
        response = await notion.data_sources.query(data_source_id=ds_id)
        results = response.get("results", [])

        total = len(results)
        completed = 0
        in_progress = 0

        for page in results:
            status = page["properties"]["Status"]["select"]["name"]
            if status == "Completed":
                completed += 1
            elif status == "In-Progress":
                in_progress += 1

        return {
            "total": total,
            "completed": completed,
            "in_progress": in_progress,
            "percentage": round((completed / total * 100)) if total > 0 else 0,
        }
    except APIResponseError as e:
        logger.error(f"Notion API Error (get_progress_stats): {e}")
        return {"total": 0, "completed": 0, "in_progress": 0, "percentage": 0}
