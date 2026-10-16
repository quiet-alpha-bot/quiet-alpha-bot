"""Existing card renderers (injected from main.py) + ledgered Telegram output.

Delivery guarantee: an event recorded as SENT is never republished
automatically. Exactly-once is NOT guaranteed across the crash window
between Telegram accepting a message and the ledger recording SENT; such
interrupted deliveries end up in NEEDS_RECONCILIATION instead of being resent.

The automation layer never re-implements card drawing. main.py passes the
same functions that power /c /p /win /max into CardRenderers, and every
image is produced by them, unchanged.

All output goes to SIGNAL_CHAT_ID only. Every message that must appear
once (entry card, +10% notification, success card, highest card, proposal)
goes through the `events` ledger:

    claim -> send -> SENT
    definite failure (BadRequest/Forbidden/RetryAfter) -> FAILED (retryable)
    ambiguous failure (timeout/network)               -> UNCERTAIN (never resent)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
from typing import Any, Callable, Optional

from telegram import Bot, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut

from .db import Database
from .models import money

logger = logging.getLogger("quiet-alpha.cards")

PHOTO_TIMEOUTS = dict(
    connect_timeout=30, read_timeout=90, write_timeout=90, pool_timeout=30
)


@dataclass(frozen=True)
class CardRenderers:
    create_signal_card: Callable[..., BytesIO]
    create_success_card: Callable[..., BytesIO]
    create_highest_price_card: Callable[..., BytesIO]
    create_stop_loss_card: Callable[..., BytesIO]
    calculate_highest_result: Callable[..., tuple]
    format_strike: Callable[[str], str]
    format_premium: Callable[[str], str]

    # Thin adapters: same inputs the manual commands produce.
    def signal_card(self, direction: str, strike: Decimal, entry: Decimal) -> BytesIO:
        return self.create_signal_card(
            signal_type=direction,
            strike=self.format_strike(str(strike)),
            premium=self.format_premium(str(entry)),
        )

    def success_card(self, entry: Decimal, exit_price: Decimal) -> BytesIO:
        return self.create_success_card(
            entry_price=self.format_premium(str(entry)),
            exit_price=self.format_premium(str(exit_price)),
        )

    def highest_card(self, entry: Decimal, highest: Decimal) -> BytesIO:
        return self.create_highest_price_card(
            entry_price=self.format_premium(str(entry)),
            highest_price=self.format_premium(str(highest)),
        )

    def stop_loss_card(self, entry: Decimal) -> BytesIO:
        return self.create_stop_loss_card(
            entry_price=self.format_premium(str(entry)),
        )


class Publisher:
    def __init__(self, bot: Bot, chat_id: str, db: Database) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.db = db

    async def _deliver(
        self,
        entity_type: str,
        entity_id: int,
        event_type: str,
        level: int,
        payload: Optional[dict],
        send: Callable[[], Any],
        max_attempts: int = 3,
    ) -> Optional[int]:
        event_id = await self.db.claim_event(
            entity_type, entity_id, event_type, level, payload, max_attempts
        )
        if event_id is None:
            return None  # already sent / uncertain / in progress
        try:
            message = await send()
        except RetryAfter as error:
            await self.db.mark_event(event_id, "FAILED", None, f"RetryAfter {error.retry_after}")
            logger.warning("%s/%s %s: rate limited by Telegram", entity_type, entity_id, event_type)
            return None
        except (BadRequest, Forbidden) as error:
            await self.db.mark_event(event_id, "FAILED", None, type(error).__name__)
            logger.error("%s/%s %s rejected by Telegram: %s",
                         entity_type, entity_id, event_type, error)
            return None
        except (TimedOut, NetworkError) as error:
            await self.db.mark_event(event_id, "UNCERTAIN", None, type(error).__name__)
            logger.error("%s/%s %s delivery uncertain (%s); will not resend",
                         entity_type, entity_id, event_type, type(error).__name__)
            await self.alert_uncertain(entity_type, entity_id, event_type, level)
            return None
        except Exception as error:  # rendering or unexpected errors
            await self.db.mark_event(event_id, "FAILED", None, type(error).__name__)
            logger.exception("%s/%s %s failed", entity_type, entity_id, event_type)
            return None
        message_id = getattr(message, "message_id", None)
        await self.db.mark_event(event_id, "SENT", message_id, None)
        return message_id

    async def text_once(
        self,
        entity_type: str,
        entity_id: int,
        event_type: str,
        text: str,
        level: int = 0,
        reply_markup: Optional[InlineKeyboardMarkup] = None,
        payload: Optional[dict] = None,
        max_attempts: int = 3,
    ) -> Optional[int]:
        return await self._deliver(
            entity_type,
            entity_id,
            event_type,
            level,
            payload,
            lambda: self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            ),
            max_attempts,
        )

    async def photo_once(
        self,
        entity_type: str,
        entity_id: int,
        event_type: str,
        render: Callable[[], BytesIO],
        level: int = 0,
        payload: Optional[dict] = None,
        max_attempts: int = 3,
    ) -> Optional[int]:
        async def send():
            card = await asyncio.to_thread(render)
            try:
                return await self.bot.send_photo(
                    chat_id=self.chat_id, photo=card, **PHOTO_TIMEOUTS
                )
            finally:
                card.close()

        return await self._deliver(
            entity_type, entity_id, event_type, level, payload, send, max_attempts
        )

    async def text(self, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None) -> Optional[int]:
        """Non-ledgered informational message (status/errors). Best effort."""
        try:
            message = await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
            return message.message_id
        except Exception as error:
            logger.warning("Status message failed: %s", type(error).__name__)
            return None

    async def edit(
        self,
        message_id: int,
        text: str,
        reply_markup: Optional[InlineKeyboardMarkup] = None,
    ) -> bool:
        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=message_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
            return True
        except BadRequest as error:
            if "not modified" in str(error).lower():
                return True
            logger.warning("Edit failed for message %s: %s", message_id, error)
            return False
        except Exception as error:
            logger.warning("Edit failed for message %s: %s", message_id, type(error).__name__)
            return False

    async def alert_uncertain(
        self, entity_type: str, entity_id: int, event_type: str, level: int
    ) -> None:
        await self.text(
            "⚠️ <b>QUIET ALPHA</b>\n"
            f"تعذر التأكد من وصول رسالة ({event_type}"
            f"{'' if not level else f' {level}'}) لـ {entity_type} #{entity_id}.\n"
            "لن تُعاد تلقائيًا لتجنب التكرار."
        )


def price(value: Optional[Decimal]) -> str:
    return f"${money(value)}" if value is not None else "—"
