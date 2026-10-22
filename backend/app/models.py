# Register application models for Alembic. D services do not import private
# business models; each business module owns its own model and migrations.
from app.modules.audit.models import OperationLog
from app.modules.auth.models import (
    AuthSession,
    RolePermission,
    ServicePrincipal,
    ServiceRoomScope,
    User,
    UserIdentity,
    UserRole,
)
from app.modules.m1.models import (
    CheckinAttempt,
    Reservation,
    ReservationEvent,
    RoomCheckinCode,
    Violation,
)
from app.modules.m3.models import WorkOrder, WorkOrderEscalation, WorkOrderRating
from app.modules.notifications.models import Notification
from app.modules.resources.allocation_models import SeatAllocation
from app.modules.resources.models import (
    Building,
    OpeningException,
    OpeningRule,
    ResourceBlock,
    Room,
    Seat,
    SeatStatusSummary,
)

__all__ = [
    "AuthSession",
    "RolePermission",
    "ServicePrincipal",
    "ServiceRoomScope",
    "Reservation",
    "ReservationEvent",
    "Violation",
    "RoomCheckinCode",
    "CheckinAttempt",
    "Building",
    "OpeningRule",
    "OpeningException",
    "SeatAllocation",
    "OperationLog",
    "User",
    "UserIdentity",
    "UserRole",
    "WorkOrder",
    "WorkOrderEscalation",
    "WorkOrderRating",
    "Notification",
    "ResourceBlock",
    "Room",
    "Seat",
    "SeatStatusSummary",
]
