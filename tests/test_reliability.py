import asyncio
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from telegram.error import RetryAfter, TimedOut
from automation.cards import Publisher
from automation.autonomous import SPXRadar
from automation.uw_client import UWClient, UWError
from automation.runtime import AutomationRuntime

class ReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_background_edits_rotate_and_deduplicate(self):
        bot=NS(edit_message_text=AsyncMock()); p=Publisher(bot,'-1',None)
        with patch('automation.cards.time.monotonic',return_value=100):
            self.assertTrue(await p.edit(1,'a',background=True))
            self.assertFalse(await p.edit(2,'b',background=True))
            self.assertFalse(await p.edit(3,'c',background=True))
        with patch('automation.cards.time.monotonic',return_value=105):
            self.assertFalse(await p.edit(1,'new',background=True))
            self.assertTrue(await p.edit(2,'b',background=True))
        with patch('automation.cards.time.monotonic',return_value=110):
            self.assertFalse(await p.edit(1,'new',background=True))
            self.assertTrue(await p.edit(3,'c',background=True))
            self.assertTrue(await p.edit(2,'b',background=True))
        self.assertEqual([c.kwargs['message_id'] for c in bot.edit_message_text.call_args_list],[1,2,3])

    async def test_retry_after_pauses_edits_and_does_not_consume_ledger(self):
        bot=NS(edit_message_text=AsyncMock(side_effect=[RetryAfter(30),None]))
        db=NS(claim_event=AsyncMock());p=Publisher(bot,'-1',db)
        with patch('automation.cards.time.monotonic',return_value=100):
            self.assertFalse(await p.edit(1,'a',background=True))
        with patch('automation.cards.time.monotonic',return_value=120):
            self.assertFalse(await p.edit(1,'a',background=True))
            self.assertIsNone(await p._deliver_unlocked('radar',1,'REPORT',0,{},AsyncMock(),3))
        db.claim_event.assert_not_awaited()
        with patch('automation.cards.time.monotonic',return_value=132):
            self.assertTrue(await p.edit(1,'a',background=True))
        self.assertEqual(bot.edit_message_text.await_count,2)

    async def test_priority_edit_bypasses_cosmetic_delay(self):
        bot=NS(edit_message_text=AsyncMock()); p=Publisher(bot,'-1',None)
        with patch('automation.cards.time.monotonic',return_value=100):
            await p.edit(1,'live',background=True)
            self.assertFalse(await p.edit(2,'live',background=True))
            self.assertTrue(await p.edit(2,'closed'))
        self.assertNotIn(2,p._edit_pending)
        self.assertEqual(bot.edit_message_text.await_count,2)

    async def test_timeout_preview_survives_failed_error_notification(self):
        now=datetime(2026,11,20,16,tzinfo=timezone.utc)
        r=NS(liquidity=NS(radar_snapshot=AsyncMock(side_effect=asyncio.TimeoutError)),settings=NS(market=NS(open_et=datetime.strptime('09:30','%H:%M').time())))
        u=NS(message=NS(reply_text=AsyncMock(side_effect=TimedOut())))
        with patch('automation.runtime.utcnow',return_value=now):
            await AutomationRuntime._radar_preview(r,u)
        u.message.reply_text.assert_awaited_once()

    async def test_network_error_defers_cosmetic_edits(self):
        bot=NS(edit_message_text=AsyncMock(side_effect=TimedOut()));p=Publisher(bot,'-1',None)
        with patch('automation.cards.time.monotonic',return_value=100):
            await p.edit(1,'a',background=True)
        with patch('automation.cards.time.monotonic',return_value=110):
            self.assertFalse(await p.edit(2,'b',background=True))
        self.assertEqual(bot.edit_message_text.await_count,1)

    async def test_spx_bad_contract_does_not_abort_good_contract(self):
        now=datetime(2026,11,20,16,tzinfo=timezone.utc)
        r=SPXRadar(NS(market=NS(open_et=datetime.strptime('09:30','%H:%M').time(),close_et=datetime.strptime('16:00','%H:%M').time())),None,None,None)
        r.candidates=AsyncMock(return_value=['bad','good'])
        r.check=AsyncMock(side_effect=[UWError('http','upstream',500),None])
        with patch('automation.autonomous.utcnow',return_value=now),patch('automation.autonomous.enabled',return_value=True):
            await r.tick()
        self.assertEqual(r.check.await_count,2)
        self.assertEqual(r.scanned,1)
        self.assertEqual(r.last_error,'SPX_UW_HTTP')
        r.check=AsyncMock(side_effect=UWError('rate_limited','limit',429))
        with patch('automation.autonomous.utcnow',return_value=now),patch('automation.autonomous.enabled',return_value=True):
            await r.tick()
        self.assertEqual(r.check.await_count,1)

    async def test_uw_limit_shared_and_recovers(self):
        c=UWClient('not-a-real-key',NS(max_retries=2))
        limited=NS(status_code=429,headers={'x-uw-req-per-minute-reset':'30'})
        good=NS(status_code=200,headers={},json=lambda:{'data':[]})
        c._client=NS(get=AsyncMock(side_effect=[limited,good]))
        with patch('automation.uw_client.time.monotonic',return_value=100):
            with self.assertRaises(UWError):await c.get('/first')
        with patch('automation.uw_client.time.monotonic',return_value=110):
            with self.assertRaises(UWError):await c.get('/second')
        self.assertEqual(c._client.get.await_count,1)
        with patch('automation.uw_client.time.monotonic',return_value=132):
            self.assertEqual((await c.get('/third')).status,200)

    async def test_radar_command_returns_before_scan_and_single_flight(self):
        now=datetime(2026,11,20,16,tzinfo=timezone.utc)
        blocker=asyncio.Event()
        async def preview(update):await blocker.wait()
        r=NS(_guard=AsyncMock(return_value=True),liquidity=object(),settings=NS(market=NS(open_et=datetime.strptime('09:30','%H:%M').time(),close_et=datetime.strptime('16:00','%H:%M').time())),_radar_preview=preview)
        r._spawn=lambda coro,name:asyncio.create_task(coro,name=name)
        u=NS(message=NS(reply_text=AsyncMock()))
        with patch('automation.runtime.utcnow',return_value=now):
            await asyncio.wait_for(AutomationRuntime.cmd_radar(r,u,None),.5)
            task=r._radar_preview_task
            await AutomationRuntime.cmd_radar(r,u,None)
        self.assertIs(task,r._radar_preview_task)
        self.assertFalse(task.done())
        blocker.set();await task

if __name__=='__main__':unittest.main()
