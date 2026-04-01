import asyncio
import json
import logging

import aiohttp

from config import (
    GLPI_APP_TOKEN,
    GLPI_URL,
    GLPI_USER_TOKEN,
    GLPI_FIELD_CONTENT,
    GLPI_FIELD_STATUS,
    GLPI_FIELD_NAME,
    GLPI_FIELD_DATE,
    TICKETS_PER_PAGE,
)

log = logging.getLogger(__name__)


class GLPIClient:
    def __init__(self) -> None:
        self._session_token: str | None = None
        self._http: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()

    @property
    def _base_headers(self) -> dict:
        return {
            "App-Token": GLPI_APP_TOKEN,
            "Content-Type": "application/json",
        }

    @property
    def _auth_headers(self) -> dict:
        return {**self._base_headers, "Session-Token": self._session_token or ""}

    async def _get_http(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession()
        return self._http

    async def init_session(self) -> None:
        async with self._session_lock:
            http = await self._get_http()
            headers = {**self._base_headers, "Authorization": f"user_token {GLPI_USER_TOKEN}"}
            async with http.get(f"{GLPI_URL}/apirest.php/initSession", headers=headers) as resp:
                resp.raise_for_status()
                data = await resp.json()
                self._session_token = data["session_token"]
                log.info("GLPI session ініціалізовано")

    async def kill_session(self) -> None:
        if not self._session_token:
            return
        http = await self._get_http()
        try:
            async with http.get(
                f"{GLPI_URL}/apirest.php/killSession", headers=self._auth_headers
            ):
                pass
        except Exception:
            pass
        self._session_token = None

    async def _ensure_session(self) -> None:
        if not self._session_token:
            await self.init_session()

    async def _request(self, method: str, url: str, **kwargs) -> aiohttp.ClientResponse:
        """Виконує HTTP-запит з автоматичним retry при 401.

        Увага: не використовувати для multipart/form-data — тіло запиту вже
        буде прочитано і не може бути надіслане повторно.
        """
        await self._ensure_session()
        http = await self._get_http()
        headers = kwargs.pop("headers", self._auth_headers)
        resp = await http.request(method, url, headers=headers, **kwargs)
        if resp.status == 401:
            resp.release()  # звільняємо з'єднання перед повторним запитом
            await self.init_session()
            headers = {**headers, "Session-Token": self._session_token or ""}
            resp = await http.request(method, url, headers=headers, **kwargs)
        return resp

    async def create_ticket(
        self,
        name: str,
        content: str,
        category_id: int,
        telegram_user_id: int,
        phone: str | None = None,
        priority: int = 3,
    ) -> dict:
        extra = f"\nТелефон: {phone}" if phone else ""
        tagged_content = f"{content}{extra}\n\n[tg:{telegram_user_id}]"
        payload = {
            "input": {
                "name": name,
                "content": tagged_content,
                "itilcategories_id": category_id,
                "type": 1,           # 1 = Incident
                "urgency": priority,
                "impact": priority,
                "priority": priority,
                "requesttypes_id": 7,  # Telegram Bot
            }
        }
        async with await self._request("POST", f"{GLPI_URL}/apirest.php/Ticket", json=payload) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_user_tickets(
        self, user_id: int, offset: int = 0, limit: int = TICKETS_PER_PAGE
    ) -> tuple[list[dict], int]:
        """Повертає (заявки, totalcount) за міткою [tg:user_id] в content."""
        params = {
            "criteria[0][field]": GLPI_FIELD_CONTENT,
            "criteria[0][searchtype]": "contains",
            "criteria[0][value]": f"[tg:{user_id}]",
            "forcedisplay[0]": GLPI_FIELD_NAME,
            "forcedisplay[1]": GLPI_FIELD_STATUS,
            "forcedisplay[2]": GLPI_FIELD_DATE,
            "sort": GLPI_FIELD_DATE,
            "order": "DESC",
            "range": f"{offset}-{offset + limit - 1}",
        }
        async with await self._request(
            "GET", f"{GLPI_URL}/apirest.php/search/Ticket", params=params
        ) as resp:
            if resp.status in (200, 206):
                data = await resp.json()
                return data.get("data", []), data.get("totalcount", 0)
            return [], 0

    async def get_categories(self) -> dict[str, int]:
        """Повертає словник {назва: id} категорій з GLPI."""
        async with await self._request(
            "GET", f"{GLPI_URL}/apirest.php/ITILCategory", params={"range": "0-200"}
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return {
                item["completename"]: item["id"]
                for item in data
                if isinstance(item, dict)
                and item.get("is_active") != 0
                and item.get("is_helpdeskvisible") == 1
            }

    async def get_ticket(self, ticket_id: int) -> dict:
        """Повний об'єкт заявки."""
        async with await self._request("GET", f"{GLPI_URL}/apirest.php/Ticket/{ticket_id}") as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_ticket_followups(self, ticket_id: int) -> list[dict]:
        """Follow-up коментарі до заявки."""
        async with await self._request(
            "GET", f"{GLPI_URL}/apirest.php/Ticket/{ticket_id}/ITILFollowup"
        ) as resp:
            if resp.status in (200, 206):
                data = await resp.json()
                return data if isinstance(data, list) else []
            return []

    async def add_followup(self, ticket_id: int, content: str) -> None:
        """Додає публічний follow-up до заявки."""
        payload = {"input": {
            "itemtype": "Ticket",
            "items_id": ticket_id,
            "content": content,
            "is_private": 0,
        }}
        async with await self._request(
            "POST", f"{GLPI_URL}/apirest.php/Ticket/{ticket_id}/ITILFollowup", json=payload
        ) as resp:
            resp.raise_for_status()

    async def upload_document(self, file_bytes: bytes, filename: str) -> int:
        """Завантажує файл у GLPI і повертає document id.

        Не використовує _request — FormData не можна надіслати двічі після 401.
        """
        await self._ensure_session()
        http = await self._get_http()
        manifest = json.dumps({"input": {"name": filename, "_filename": [filename]}})
        form = aiohttp.FormData()
        form.add_field("uploadManifest", manifest, content_type="application/json")
        form.add_field("filename[0]", file_bytes, filename=filename, content_type="image/jpeg")
        # Для multipart не передаємо Content-Type — aiohttp встановить boundary сам
        headers = {k: v for k, v in self._auth_headers.items() if k != "Content-Type"}
        async with http.post(
            f"{GLPI_URL}/apirest.php/Document",
            data=form,
            headers=headers,
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["id"]

    async def link_document_to_ticket(self, doc_id: int, ticket_id: int) -> None:
        """Прив'язує документ до тікету."""
        payload = {"input": {"documents_id": doc_id, "items_id": ticket_id, "itemtype": "Ticket"}}
        async with await self._request(
            "POST", f"{GLPI_URL}/apirest.php/Document_Item", json=payload
        ) as resp:
            resp.raise_for_status()

    async def cancel_ticket(self, ticket_id: int) -> None:
        """Закриває заявку (status=TICKET_STATUS_CLOSED)."""
        from config import TICKET_STATUS_CLOSED
        async with await self._request(
            "PUT",
            f"{GLPI_URL}/apirest.php/Ticket/{ticket_id}",
            json={"input": {"status": TICKET_STATUS_CLOSED}},
        ) as resp:
            resp.raise_for_status()

    async def close(self) -> None:
        await self.kill_session()
        if self._http and not self._http.closed:
            await self._http.close()
