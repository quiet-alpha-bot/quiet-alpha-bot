"""Explicit local-only integration check; never reads DATABASE_URL."""
import asyncio
import json
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
import asyncpg
from automation import channel_selection as cs
from automation.cards import Publisher
from automation.db import Database
from telegram.error import BadRequest


async def main():
    conn=await asyncpg.connect(host='127.0.0.1',port=55439,user='qa_test',database='postgres')
    await conn.set_type_codec('jsonb',encoder=json.dumps,decoder=json.loads,schema='pg_catalog')
    try:
        await conn.execute('CREATE SCHEMA qa_full; SET search_path TO qa_full')
        migrations=sorted((Path(__file__).resolve().parents[1]/'automation/migrations').glob('*.sql'))
        for _ in range(2):
            for path in migrations:await conn.execute(path.read_text())
        await conn.prepare(cs.PENDING)
        print(f'{len(migrations)} migrations applied twice; selection query prepared: PASS')
        db=Database('');db.pool=conn
        message=NS(message_id=700,date=datetime.now(timezone.utc))
        bot=NS(send_message=AsyncMock(return_value=message),send_photo=AsyncMock(return_value=message))
        publisher=Publisher(bot,'-10',db)
        for _ in range(2):
            await publisher.text_once('simulation_trade',101,'SIMULATION_ENTRY',
                'محاكاة فقط',chat_id=cs.EXECUTION_CHAT,payload={'mode':'SIMULATION_ONLY'})
        assert bot.send_message.await_count==1
        ledger=await conn.fetchrow("SELECT * FROM events WHERE entity_type='simulation_trade' AND entity_id=101")
        assert ledger['status']=='SENT'
        assert ledger['payload']['publication_chat_id']==cs.EXECUTION_CHAT
        assert ledger['payload']['mode']=='SIMULATION_ONLY'
        assert 'telegram_sent_at' in ledger['payload']
        bot.send_message.side_effect=[BadRequest('test rejection'),message]
        for _ in range(2):
            await publisher.text_once('simulation_trade',102,'SIMULATION_ENTRY','محاكاة فقط',chat_id=cs.EXECUTION_CHAT)
        assert await conn.fetchval("SELECT status FROM events WHERE entity_type='simulation_trade' AND entity_id=102")=='SENT'
        await publisher.photo_once('simulation_preview',103,'ENTRY_CARD',lambda:BytesIO(b'test'),chat_id=cs.EXECUTION_CHAT)
        assert 'ليست صفقة منفذة' in bot.send_photo.await_args.kwargs['caption']
        print('Publisher + real ledger: duplicate suppressed, failed delivery retry, simulation label: PASS')
        await conn.execute('CREATE SCHEMA qa_selection; SET search_path TO qa_selection')
        await conn.execute("""CREATE TABLE proposals(id bigint PRIMARY KEY,
            status text DEFAULT 'PENDING_APPROVAL',classification text DEFAULT 'BOTH_CONFIRMED',
            channel_selection_mask integer DEFAULT 0,channel_selection_locked boolean DEFAULT false,
            details jsonb DEFAULT '{}',approval_quote jsonb DEFAULT '{}')""")
        await conn.execute('INSERT INTO proposals(id) VALUES(1),(2)')
        # Exercise actual PostgreSQL XOR and conditional compare-and-set.
        for bit in (2,4):
            await conn.execute('UPDATE proposals SET channel_selection_mask=channel_selection_mask # $1 WHERE id=1',bit)
        assert await conn.fetchval('SELECT channel_selection_mask FROM proposals WHERE id=1')==6
        class DB:
            async def fetchrow(self,sql,*args):return await conn.fetchrow(sql,*args)
            async def execute(self,sql,*args):return await conn.execute(sql,*args)
        query=NS(data='qs:send:1:a',from_user=NS(id=1),message=NS(chat=NS(id=-10,type='channel')),
                 answer=AsyncMock(),edit_message_reply_markup=AsyncMock())
        entered=asyncio.Event();release=asyncio.Event();calls=[]
        async def approve(*args):
            calls.append(args);entered.set();await release.wait()
            await conn.execute("UPDATE proposals SET status='APPROVED' WHERE id=1 AND status='PENDING_APPROVAL'")
        rt=NS(ready=True,settings=NS(owner_user_id=1,signal_chat_id=-10),db=DB(),
              app=NS(bot=NS()),approval=NS(_approve=approve))
        update=NS(callback_query=query)
        with patch.object(cs,'enabled',return_value=True),patch.object(cs.routing,'check_permission',new_callable=AsyncMock):
            first=asyncio.create_task(cs.callback(rt,update,None))
            await asyncio.wait_for(entered.wait(),2)
            await cs.callback(rt,update,None)
            release.set();await first
        assert len(calls)==1, 'Concurrent send dispatched twice'
        assert await conn.fetchval('SELECT channel_selection_locked FROM proposals WHERE id=1')
        # A failed price/session check must release the selection for retry.
        query.data='qs:send:2:a'
        await conn.execute('UPDATE proposals SET channel_selection_mask=4 WHERE id=2')
        rt.approval._approve=AsyncMock()
        with patch.object(cs,'enabled',return_value=True),patch.object(cs.routing,'check_permission',new_callable=AsyncMock):
            await cs.callback(rt,update,None)
        assert not await conn.fetchval('SELECT channel_selection_locked FROM proposals WHERE id=2')
        print('Persistent toggles, concurrent send, immutable approved selection, declined retry: PASS')
    finally:
        await conn.execute('SET search_path TO public; DROP SCHEMA IF EXISTS qa_full CASCADE; DROP SCHEMA IF EXISTS qa_selection CASCADE')
        await conn.close()


if __name__=='__main__':asyncio.run(main())
