from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models.reminder import Reminder
from models.user import User
from schemas.reminder import ReminderResponse
from services.auth import get_current_user

router = APIRouter(prefix="/api/reminders", tags=["reminders"])


@router.get("/", response_model=list[ReminderResponse])
def list_reminders(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the caller's own reminders, ordered by due_at ascending."""
    return (
        db.query(Reminder)
        .filter(Reminder.user_id == current_user.id)
        .order_by(Reminder.due_at)
        .all()
    )


@router.delete("/{reminder_id}", response_model=dict)
def delete_reminder(
    reminder_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete one of the caller's reminders. Another user's reminder is a 404,
    same as a nonexistent one — no existence leak, no admin exemption."""
    reminder = (
        db.query(Reminder)
        .filter(Reminder.id == reminder_id, Reminder.user_id == current_user.id)
        .first()
    )
    if not reminder:
        raise HTTPException(status_code=404, detail="Reminder not found")
    db.delete(reminder)
    db.commit()
    return {"deleted": reminder_id}
