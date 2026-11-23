import unittest
from datetime import datetime, timezone, time, date
from decimal import Decimal as D
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from automation import recovery, radar_session as rs
from automation.radar import report_slot
from automation.tracker import Tracker

class PolicyTests(unittest.TestCase):
    def test_recovery_needs_rise_then_return_and_override(self):
        t={'approved_entry':D('4'),'approval_quote':{'recovery_profit_at':'now'}}
        self.assertEqual(recovery.profit_level(t),D('4.10'))
        self.assertFalse(recovery.profit_exit(t,D('4.10')))
        t['approval_quote']['recovery_profit_above']=True
        self.assertFalse(recovery.profit_exit(t,D('4.20')))
        self.assertTrue(recovery.profit_exit(t,D('4.10')))
        self.assertTrue(recovery.profit_exit(t,D('3.90')))
        t['approval_quote']['recovery_continue']=True
        self.assertFalse(recovery.profit_exit(t,D('3.90')))

    def test_copy_uses_real_price_not_guaranteed_profit(self):
        t={'approved_entry':D('4'),'current_bid':D('3.90'),'option_symbol':'SPXW',
           'approval_quote':{'recovery_profit_bid':'4.10'}}
        text=recovery.recovered_text(t)
        self.assertIn('$4.10',text)
        self.assertIn('تابعوا حركة العقد عن قرب',text)
        self.assertNotIn('/C',text)
        self.assertIn('-10.00$',recovery.close_text(t))

    def test_hourly_slots_exclude_half_hours(self):
        market=NS(open_et=time(9,30),close_et=time(16))
        for hour, minute, slot in [(14,30,0),(15,0,100),(15,30,1),(16,0,101),(16,30,2)]:
            now=datetime(2026,11,23,hour,minute,tzinfo=timezone.utc)
            self.assertEqual(report_slot(now,market)[1],slot)
        self.assertIsNone(report_slot(datetime(2026,11,23,21,tzinfo=timezone.utc),market))

class CommandTests(unittest.IsolatedAsyncioTestCase):
    def runtime(self):
        return NS(settings=NS(owner_user_id=7,signal_chat_id=-10,market=NS(open_et=time(9,30),close_et=time(16))),
                  db=NS(fetchrow=AsyncMock(),execute=AsyncMock()),_guard=AsyncMock(return_value=True),ready=True)

    async def test_continue_only_owner_private(self):
        rt=self.runtime();rt.db.fetchrow.return_value={'id':636}
        u=NS(effective_user=NS(id=7),effective_chat=NS(id=-10),message=NS(reply_text=AsyncMock()))
        await recovery.continue_command(rt,u,NS(args=['636']))
        rt.db.fetchrow.assert_not_awaited();u.message.reply_text.assert_not_awaited()
        u.effective_chat.id=7
        await recovery.continue_command(rt,u,NS(args=['636']))
        u.message.reply_text.assert_awaited_once_with('✅ تم تطبيق الأمر على الصفقة #636.')
        u.effective_user.id=8
        await recovery.continue_command(rt,u,NS(args=['636']))
        self.assertEqual(rt.db.fetchrow.await_count,1)

    async def test_close_checks_override_in_atomic_update(self):
        rt=NS(db=NS(fetchrow=AsyncMock(side_effect=[{'approval_quote':{}},None])))
        self.assertIsNone(await Tracker.close(rt,636,'RECOVERY_STOP'))
        sql=rt.db.fetchrow.call_args.args[0]
        self.assertIn('recovery_continue',sql)
        self.assertIn("status = 'TRACKING'",sql)

    async def test_publication_requires_daily_consent_and_hour_slot(self):
        now=datetime(2026,11,23,15,30,tzinfo=timezone.utc);day=now.date()
        rt=self.runtime(); r=NS(s=rt.settings,db=rt.db,publisher=NS(text_once=AsyncMock()))
        with patch.object(rs,'utcnow',return_value=now),patch.object(rs.routing,'enabled',return_value=True):
            rt.db.fetchrow.return_value={'enabled':True,'stopped':False}
            await rs.publish(r,day,100,'half hour')
            r.publisher.text_once.assert_not_awaited()
            await rs.publish(r,day,1,'hour')
            self.assertEqual(r.publisher.text_once.call_args.kwargs['chat_id'],rs.routing.PUBLIC_CHAT)
            self.assertEqual(r.publisher.text_once.call_args.args[2],'RADAR_COPY_'+rs.routing.PUBLIC_CHAT)
            rt.db.fetchrow.return_value={'enabled':False,'stopped':True}
            await rs.publish(r,day,2,'stopped')
            await rs.publish(r,date(2026,11,20),1,'old')
            self.assertEqual(r.publisher.text_once.await_count,1)

    async def test_consent_offered_once_via_daily_ledger(self):
        rt=self.runtime();r=NS(db=rt.db,publisher=NS(text_once=AsyncMock()))
        rt.db.fetchrow.return_value={'enabled':False,'stopped':False}
        with patch.object(rs.routing,'enabled',return_value=True):
            await rs.offer(r,date(2026,11,23));await rs.offer(r,date(2026,11,23))
            args=[c.args[:3] for c in r.publisher.text_once.call_args_list]
            self.assertEqual(args[0],args[1])
            rt.db.fetchrow.return_value={'enabled':False,'stopped':True}
            await rs.offer(r,date(2026,11,23))
            self.assertEqual(r.publisher.text_once.await_count,2)

    async def test_stop_authorized_only_and_scoped_to_day(self):
        rt=self.runtime();u=NS(effective_user=NS(id=8),effective_chat=NS(id=7),message=NS(reply_text=AsyncMock()))
        await rs.stop_command(rt,u,None);rt.db.execute.assert_not_awaited()
        u.effective_user.id=7
        with patch.object(rs,'utcnow',return_value=datetime(2026,11,23,16,tzinfo=timezone.utc)):
            await rs.stop_command(rt,u,None)
        rt.db.execute.assert_awaited_once_with(rs.STOP,date(2026,11,23))
