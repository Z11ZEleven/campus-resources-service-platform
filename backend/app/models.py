# Register application models for Alembic. D services do not import private
# business models; each business module owns its own model and migrations.
from app.modules.audit.models import OperationLog
from app.modules.auth.models import RoleScope, User, UserIdentity, UserRole
from app.modules.m2.models import Application, ApplicationEvent, ApprovalStep, ResourceJob
from app.modules.m3.models import WorkOrder, WorkOrderEscalation, WorkOrderRating
from app.modules.notifications.models import Notification
from app.modules.resources.commands import ResourceCommand
from app.modules.resources.models import ResourceBlock, Room, Seat, SeatStatusSummary

__all__ = [
    "RoleScope", "Application", "ApprovalStep", "ApplicationEvent", "ResourceJob", "ResourceCommand",
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
