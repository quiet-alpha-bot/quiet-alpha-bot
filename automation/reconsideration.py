"""Persist a fixed 15-minute reconsideration window from each accepted rejection."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

REJECT = """UPDATE proposals SET status='REJECTED',decided_at=now(),decided_by=$3,
 decision_note='reconsiderable rejection',expires_at=now()+interval '15 minutes',updated_at=now()
WHERE id=$1 AND status=$2 AND (expires_at IS NULL OR expires_at>now()) RETURNING *"""
RESTORE = """UPDATE proposals SET status='PENDING_APPROVAL',updated_at=now(),
 details=COALESCE(details,'{}'::jsonb)||jsonb_build_object('reconsidered',true),
 approving_from=NULL,approving_since=NULL
WHERE id=$1 AND status='REJECTED' AND decided_by=$2
 AND decision_note='reconsiderable rejection' AND expires_at>now()
 AND decided_at>now()-interval '15 minutes' RETURNING *"""

def keyboard(proposal_id, manual=False):
    prefix='qam' if manual else 'qa'
    return InlineKeyboardMarkup([[InlineKeyboardButton('↩️ إعادة النظر — خلال ١٥ دقيقة',
        callback_data=f'{prefix}:reopen:{proposal_id}')]])

async def reject(db,proposal_id,expected,user_id):
    return await db.fetchrow(REJECT,proposal_id,expected,user_id)

async def restore(db,proposal_id,user_id):
    # Never extend the rejection deadline on restore or repeated button presses.
    return await db.fetchrow(RESTORE,proposal_id,user_id)
