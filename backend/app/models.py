# Import all D models once for Alembic metadata. Business modules extend this list
# in their own migrations; D never imports another module's private models.
from app.modules.audit.models import OperationLog
from app.modules.auth.models import User, UserIdentity, UserRole
from app.modules.resources.models import ResourceBlock, Room, Seat, SeatStatusSummary

__all__ = [
    "OperationLog",
    "User",
    "UserIdentity",
    "UserRole",
    "ResourceBlock",
    "Room",
    "Seat",
    "SeatStatusSummary",
]
