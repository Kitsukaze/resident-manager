from __future__ import annotations

import itertools
import re
from datetime import datetime
from typing import Any, List, Optional

import pyodbc  # type: ignore

from .auth import HashedAuthorization
from .info import PublicInfo
from .residents import Resident
from ..config import DB_PAGINATION_QUERY
from ..database import Database
from ..utils import generate_id, hash_password


__all__ = ("RegisterRequest",)


class RegisterRequest(PublicInfo, HashedAuthorization):
    """Data model for objects holding information about a registration request.

    Each object of this class corresponds to a database row."""

    async def accept(self) -> Resident:
        """This function is a coroutine.

        Accept the registration request, create a new resident record in the database
        and remove this request from the database.

        Returns
        -----
        `Resident`
            The newly registered resident.
        """
        async with Database.instance.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    DELETE FROM register_queue
                    OUTPUT ?, DELETED.name, DELETED.room, DELETED.birthday, DELETED.phone, DELETED.email, DELETED.username, DELETED.hashed_password
                    INTO residents
                    WHERE request_id = ?
                    """,
                    generate_id(),
                    self.id,
                )

                row = await cursor.fetchone()
                resident = Resident.from_row(row)

        return resident

    async def decline(self) -> None:
        async with Database.instance.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM register_queue WHERE request_id = ?", self.id)

    @classmethod
    async def accept_many(cls, ids: List[int]) -> None:
        async with Database.instance.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                mapping = [(generate_id(), id) for id in ids]
                temp_fmt = ", ".join("(?, ?)" for _ in mapping)
                temp_decl = f"(VALUES {temp_fmt}) temp(resident_id, request_id)"

                await cursor.execute(
                    f"""
                    DELETE FROM register_queue
                    OUTPUT temp.resident_id, DELETED.name, DELETED.room, DELETED.birthday, DELETED.phone, DELETED.email, DELETED.username, DELETED.hashed_password
                    INTO residents
                    FROM register_queue
                    INNER JOIN {temp_decl}
                    ON register_queue.request_id = temp.request_id
                    """,
                    *itertools.chain(mapping),
                )

    @classmethod
    def from_row(cls, row: Any) -> RegisterRequest:
        return cls(
            id=row[0],
            name=row[1],
            room=row[2],
            birthday=row[3],
            phone=row[4],
            email=row[5],
            username=row[6],
            hashed_password=row[7],
        )

    @classmethod
    async def create(
        cls,
        name: str,
        room: int,
        birthday: Optional[datetime],
        phone: Optional[str],
        email: Optional[str],
        username: str,
        password: str,
    ) -> Optional[RegisterRequest]:
        # Validate data
        if phone is not None and len(phone) == 0:
            phone = None

        if email is not None and len(email) == 0:
            email = None

        if (
            len(name) == 0
            or len(name) > 255
            or room < 0
            or room > 32767
            or (phone is not None and (len(phone) > 15 or not phone.isdigit()))
            or (email is not None and len(email) > 255)
            or len(username) == 0
            or len(username) > 255
            or len(password) == 0
        ):
            return None

        if email is not None and re.fullmatch(r"[\w\.-]+@[\w\.-]+\.[\w\.]+[\w\.]?", email) is None:
            return None

        hashed_password = hash_password(password)
        async with Database.instance.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                request_id = generate_id()
                try:
                    await cursor.execute(
                        """
                        IF NOT EXISTS (SELECT username FROM residents WHERE username = ?)
                        INSERT INTO register_queue VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ELSE
                        RAISERROR(15600, -1, -1)
                        """,
                        username,
                        request_id,
                        name,
                        room,
                        birthday,
                        phone,
                        email,
                        username,
                        hashed_password,
                    )
                except pyodbc.DatabaseError:
                    return None

        return cls(
            id=request_id,
            name=name,
            room=room,
            birthday=birthday,
            phone=phone,
            email=email,
            username=username,
            hashed_password=hashed_password,
        )

    @classmethod
    async def query(cls, *, offset: int) -> List[RegisterRequest]:
        async with Database.instance.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT * FROM register_queue
                    ORDER BY request_id DESC
                    OFFSET ? ROWS
                    FETCH NEXT ? ROWS ONLY
                    """,
                    offset,
                    DB_PAGINATION_QUERY,
                )

                rows = await cursor.fetchall()
                return [cls.from_row(row) for row in rows]
