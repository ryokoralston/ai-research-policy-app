from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models.reminder import Reminder
from schemas.reminder import ReminderResponse

router = APIRouter(prefix="/api/reminders", tags=["reminders"])


@router.get("/", response_model=list[ReminderResponse])
def list_reminders(db: Session = Depends(get_db)):
    """Return all reminders ordered by due_at ascending."""
    return db.query(Reminder).order_by(Reminder.due_at).all()


@router.delete("/{reminder_id}", response_model=dict)
def delete_reminder(reminder_id: str, db: Session = Depends(get_db)):
    """Delete a reminder by ID. Returns 404 if not found."""
    reminder = db.query(Reminder).filter(Reminder.id == reminder_id).first()
    if not reminder:
        raise HTTPException(status_code=404, detail="Reminder not found")
    db.delete(reminder)
    db.commit()
    return {"deleted": reminder_id}
