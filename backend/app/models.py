# Register application models for Alembic. D services do not import private
# business models; each business module owns its own model and migrations.
from app.modules.audit.models import OperationLog
from app.modules.auth.models import User, UserIdentity, UserRole
from app.modules.m3.models import WorkOrder
from app.modules.resources.models import ResourceBlock, Room, Seat, SeatStatusSummary

__all__ = [
    "OperationLog",
    "User",
    "UserIdentity",
    "UserRole",
    "WorkOrder",
    "ResourceBlock",
    "Room",
    "Seat",
    "SeatStatusSummary",
]
