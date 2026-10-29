"""Existing card renderers (injected from main.py) + ledgered Telegram output.

Delivery guarantee: an event recorded as SENT is never republished
automatically. Exactly-once is NOT guaranteed across the crash window
between Telegram accepting a message and the ledger recording SENT; such
interrupted deliveries end up in NEEDS_RECONCILIATION instead of being resent.

The automation layer never re-implements card drawing. main.py passes the
same functions that power /c /p /win /max into CardRenderers, and every
image is produced by them, unchanged.

Operational output stays in SIGNAL_CHAT_ID. Approved trade publications use
the immutable destination on the trade, without duplicate admin copies.
Every message that must appear
once (entry card, +10% notification, success card, highest card, proposal)
goes through the `events` ledger:

    claim -> send -> SENT
    definite failure (BadRequest/Forbidden/RetryAfter) -> FAILED (retryable)
    ambiguous failure (timeout/network)               -> UNCERTAIN (never resent)
"""

from __future__ import annotations

import asyncio
import logging
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from typing import Any, Callable, Optional

from telegram import Bot, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut

from .db import Database
from .models import money
from .channel_routing import trade_destination
from . import queries as q

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

    def success_card(self, entry: Decimal, exit_price: Decimal, option_symbol: str = "") -> BytesIO:
        return self.create_success_card(
            entry_price=self.format_premium(str(entry)),
            exit_price=self.format_premium(str(exit_price)),
            option_symbol=option_symbol,
        )

    def highest_card(self, entry: Decimal, highest: Decimal, option_symbol: str = "") -> BytesIO:
        return self.create_highest_price_card(
            entry_price=self.format_premium(str(entry)),
            highest_price=self.format_premium(str(highest)),
            option_symbol=option_symbol,
        )

    def stop_loss_card(self, entry: Decimal, exit_price: Optional[Decimal] = None, option_symbol: str = "") -> BytesIO:
        return self.create_stop_loss_card(
            exit_price=None if exit_price is None else self.format_premium(str(exit_price)),
            option_symbol=option_symbol,
            entry_price=self.format_premium(str(entry)),
        )


class Publisher:
    def __init__(self, bot: Bot, chat_id: str, db: Database) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.db = db

    async def publication_ready(self, entity_type, entity_id, event_type, target):
        if entity_type != 'trade' or str(target) == str(self.chat_id) or event_type == 'ENTRY_CARD':
            return True
        entry = await self.db.fetchrow(q.EVENT_STATUS,'trade',entity_id,'ENTRY_CARD',0)
        return entry is not None and entry['status'] == 'SENT'

    async def entry_reply(self, entity_type, entity_id, event_type, target):
        """Resolve this trade's original card from the durable delivery ledger."""
        if entity_type != 'trade' or event_type == 'ENTRY_CARD':
            return {}
        entry = await self.db.fetchrow(q.EVENT_STATUS, 'trade', entity_id, 'ENTRY_CARD', 0)
        if not entry or entry['status'] != 'SENT':
            return {}
        message_id = entry.get('message_id')
        if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id <= 0:
            return {}
        payload = entry.get('payload') or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (ValueError, TypeError):
                return {}
        if not isinstance(payload, dict):
            return {}
        entry_chat = payload.get('publication_chat_id')
        if entry_chat is None:
            # Older ledger rows predate destination metadata.
            trade = await self.db.fetchrow(q.GET_TRADE, entity_id)
            if not trade:
                return {}
            entry_chat = trade.get('publication_chat_id') or self.chat_id
        if str(entry_chat) != str(target):
            return {}  # Never attach an admin update to another channel's ID.
        return dict(reply_to_message_id=message_id, allow_sending_without_reply=True)

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
        sent_at = getattr(message, "date", None)
        if not isinstance(sent_at, datetime) or sent_at.tzinfo is None:
            sent_at = None
        await self.db.mark_event(event_id, "SENT", message_id, None, sent_at=sent_at)
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
        chat_id: Optional[str] = None,
    ) -> Optional[int]:
        target = chat_id or await trade_destination(self.db,entity_type,entity_id,event_type,self.chat_id)
        if not await self.publication_ready(entity_type,entity_id,event_type,target):
            return None
        reply = await self.entry_reply(entity_type,entity_id,event_type,target)
        async def send_to(chat):
            return await self.bot.send_message(chat_id=chat,text=text,parse_mode=ParseMode.HTML,
                                               reply_markup=reply_markup, **reply)
        result = await self._deliver(
            entity_type,entity_id,event_type,level,
            dict(payload or {},publication_chat_id=str(target)),lambda:send_to(target),max_attempts)
        return result

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
        target = await trade_destination(self.db,entity_type,entity_id,event_type,self.chat_id)
        if not await self.publication_ready(entity_type,entity_id,event_type,target):
            return None
        reply = await self.entry_reply(entity_type,entity_id,event_type,target)
        async def send(chat):
            card = await asyncio.to_thread(render)
            try:
                return await self.bot.send_photo(
                    chat_id=chat, photo=card, **PHOTO_TIMEOUTS, **reply
                )
            finally:
                card.close()

        result = await self._deliver(
            entity_type, entity_id, event_type, level,
            dict(payload or {},publication_chat_id=str(target)),lambda:send(target),max_attempts
        )
        return result

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
