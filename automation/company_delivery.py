"""All company experiment delivery stays in its dedicated channel."""
import logging
from .cards import Publisher
from . import queries as q
from .company_policy import CHAT

log=logging.getLogger('quiet-alpha.company-delivery')

class CompanyPublisher(Publisher):
    def __init__(self,bot,db):super().__init__(bot,CHAT,db)

    async def alert_uncertain(self,entity_type,entity_id,event_type,level):
        # Keep uncertain events durable, never resend or leak to the SPX admin channel.
        log.warning('Company delivery uncertain: %s/%s/%s/%s',entity_type,entity_id,event_type,level)

    async def entry_reply(self,entity_type,entity_id,event_type,target,entry_event='ENTRY'):
        if entity_type!='company_position' or event_type=='ENTRY':return {}
        row=await self.db.fetchrow(q.EVENT_STATUS,entity_type,entity_id,'ENTRY',0)
        if row and row['status']=='SENT' and row.get('message_id'):
            return dict(reply_to_message_id=row['message_id'],allow_sending_without_reply=True)
        return {}

    async def publication_ready(self,entity_type,entity_id,event_type,target,entry_event='ENTRY'):
        if entity_type!='company_position' or event_type=='ENTRY':return True
        row=await self.db.fetchrow(q.EVENT_STATUS,entity_type,entity_id,'ENTRY',0)
        return bool(row and row['status']=='SENT')
