"""Pydantic schemas grouped by resource."""

from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    LogoutRequest,
    PasswordChangeRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    TokenResponse,
    UserSummary,
)
from app.schemas.common import (
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    LivenessResponse,
    MessageResponse,
    UTCDatetime,
)
from app.schemas.project import (
    ProjectCreate,
    ProjectMemberCreate,
    ProjectMemberRead,
    ProjectMemberUpdate,
    ProjectRead,
    ProjectStats,
    ProjectUpdate,
)
from app.schemas.task import (
    TaskBulkResult,
    TaskBulkStatusUpdate,
    TaskCreate,
    TaskRead,
    TaskUpdate,
)
from app.schemas.user import AdminStats, UserAdminUpdate, UserRead, UserUpdate

__all__ = [
    "AdminStats",
    "AuthResponse",
    "ErrorDetail",
    "ErrorResponse",
    "HealthResponse",
    "LivenessResponse",
    "LoginRequest",
    "LogoutRequest",
    "MessageResponse",
    "PasswordChangeRequest",
    "ProjectCreate",
    "ProjectMemberCreate",
    "ProjectMemberRead",
    "ProjectMemberUpdate",
    "ProjectRead",
    "ProjectStats",
    "ProjectUpdate",
    "RefreshRequest",
    "RegisterRequest",
    "TaskBulkResult",
    "TaskBulkStatusUpdate",
    "TaskCreate",
    "TaskRead",
    "TaskUpdate",
    "TokenPair",
    "TokenResponse",
    "UTCDatetime",
    "UserAdminUpdate",
    "UserRead",
    "UserSummary",
    "UserUpdate",
]
