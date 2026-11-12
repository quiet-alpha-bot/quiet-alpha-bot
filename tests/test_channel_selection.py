import os
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from automation import channel_selection as cs, channel_routing as routing
from automation.telegram_ui import proposal_keyboard
from automation.manual_tracking import manual_keyboard
from decimal import Decimal


class SelectionTests(unittest.IsolatedAsyncioTestCase):
    def test_all_seven_combinations(self):
        for mask, expected in {1:routing.PUBLIC_CHAT,2:routing.PRIVATE_CHAT,3:'both',
                               4:cs.EXECUTION_CHAT,5:routing.PUBLIC_CHAT,
                               6:routing.PRIVATE_CHAT,7:'both'}.items():
            self.assertEqual(cs.route(mask),expected)
        for value in (0,8,-1,True,'1'):
            with self.assertRaises(ValueError):cs.route(value)

    def test_keyboard_selection_and_flag_default(self):
        markup=cs.keyboard(3,6)
        self.assertTrue(markup.inline_keyboard[0][0].text.startswith('⬜'))
        self.assertTrue(markup.inline_keyboard[0][1].text.startswith('✅'))
        self.assertTrue(markup.inline_keyboard[0][2].text.startswith('✅'))
        with patch.dict(os.environ,{'CHANNEL_ROUTING_ENABLED':'true','CHANNEL_SELECTION_ENABLED':'false'}):
            self.assertFalse(cs.enabled())
        with patch.dict(os.environ,{'CHANNEL_ROUTING_ENABLED':'true','CHANNEL_SELECTION_ENABLED':'true'}):
            self.assertTrue(proposal_keyboard(3).inline_keyboard[1][0].callback_data.startswith('qs:send:'))
            self.assertEqual(manual_keyboard(3,Decimal('2')).inline_keyboard[1][0].callback_data,'qs:send:3:2')

    def fixtures(self, action, mask=6):
        row={'id':3,'classification':'BOTH_CONFIRMED','status':'PENDING_APPROVAL',
             'channel_selection_mask':mask,'approval_quote':{},'details':{}}
        query=NS(data=f'qs:{action}:3:a',from_user=NS(id=1),
                 message=NS(chat=NS(id=-10,type='channel')),answer=AsyncMock(),
                 edit_message_reply_markup=AsyncMock())
        rt=NS(ready=True,settings=NS(owner_user_id=1,signal_chat_id=-10),
              db=NS(fetchrow=AsyncMock(return_value=row),execute=AsyncMock()),
              app=NS(bot=NS()),approval=NS(_approve=AsyncMock()))
        return rt,NS(callback_query=query),row

    async def test_toggle_never_approves(self):
        rt,update,row=self.fixtures('2')
        with patch.object(cs,'enabled',return_value=True):
            await cs.callback(rt,update,None)
        rt.approval._approve.assert_not_awaited()
        update.callback_query.edit_message_reply_markup.assert_awaited_once()

    async def test_send_checks_both_destinations_before_approval(self):
        rt,update,row=self.fixtures('send')
        with patch.object(cs,'enabled',return_value=True), patch.object(routing,'check_permission',new_callable=AsyncMock) as check:
            await cs.callback(rt,update,None)
        self.assertEqual(check.await_count,2)
        self.assertEqual(rt.approval._approve.await_args.args[-1],routing.PRIVATE_CHAT)

    async def test_unauthorized_and_empty_and_hero(self):
        for case in ('unauthorized','empty','hero'):
            rt,update,row=self.fixtures('send',0 if case=='empty' else 4)
            if case=='unauthorized':update.callback_query.from_user.id=2
            if case=='hero':row['details']={'trade_mode':'HERO_OR_ZERO'}
            with patch.object(cs,'enabled',return_value=True):
                await cs.callback(rt,update,None)
            rt.approval._approve.assert_not_awaited()

    async def test_permission_failure_does_not_freeze_or_approve(self):
        rt,update,row=self.fixtures('send')
        with patch.object(cs,'enabled',return_value=True),patch.object(routing,'check_permission',new_callable=AsyncMock,side_effect=ValueError('denied')):
            await cs.callback(rt,update,None)
        self.assertEqual(rt.db.fetchrow.await_count,1)
        rt.approval._approve.assert_not_awaited()

    async def test_manual_dispatch_keeps_price_and_simulation_destination(self):
        rt,update,row=self.fixtures('send',4)
        row['classification']='MANUAL'
        update.callback_query.data='qs:send:3:2.50'
        update.callback_query.message.chat.type='private'
        rt.manual=NS(callback=AsyncMock())
        with patch.object(cs,'enabled',return_value=True),patch.object(routing,'check_permission',new_callable=AsyncMock):
            await cs.callback(rt,update,None)
        forwarded=rt.manual.callback.await_args.args[0].callback_query
        self.assertEqual(forwarded.data,'qam:execution:3:2.50')
        self.assertTrue(forwarded.selection_dispatch)

    async def test_cancel_delegates_without_permission_checks_or_approval(self):
        rt,update,row=self.fixtures('cancel')
        rt.approval.on_callback=AsyncMock()
        with patch.object(cs,'enabled',return_value=True):await cs.callback(rt,update,None)
        self.assertEqual(rt.approval.on_callback.await_args.args[0].callback_query.data,'qa:rj:3')
        rt.approval._approve.assert_not_awaited()


if __name__=='__main__':unittest.main()
