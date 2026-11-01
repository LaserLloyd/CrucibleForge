from dataclasses import dataclass
from datetime import datetime


@dataclass
class Nudge:
    chore_id: str
    title: str
    assignee: str
    due: datetime        # aware UTC
    send_at: datetime    # aware UTC
