"""Current-user profile endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Body, status

from app.api.deps import CurrentUser, DbSession
from app.api.errors import COMMON_RESPONSES
from app.core.exceptions import BadRequestError
from app.core.security import verify_password
from app.schemas.auth import AccountDeleteRequest, PasswordChangeRequest
from app.schemas.common import MessageResponse
from app.schemas.user import UserRead, UserUpdate
from app.services import auth as auth_service
from app.services import users as user_service

router = APIRouter(prefix="/users", tags=["Users"], responses=COMMON_RESPONSES)


@router.get(
    "/me",
    response_model=UserRead,
    summary="Get the current user's profile",
)
async def read_current_user(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)


@router.patch(
    "/me",
    response_model=UserRead,
    summary="Update the current user's profile",
    description="Partial update. Email changes must remain unique across the platform.",
)
async def update_current_user(
    payload: UserUpdate,
    db: DbSession,
    user: CurrentUser,
) -> UserRead:
    updated = await user_service.update_profile(db, user, payload)
    return UserRead.model_validate(updated)


@router.put(
    "/me/password",
    response_model=MessageResponse,
    summary="Change the current user's password",
    description=(
        "Changing the password revokes every refresh token and invalidates all "
        "access tokens; clients must sign in again."
    ),
)
async def change_password(
    payload: PasswordChangeRequest,
    db: DbSession,
    user: CurrentUser,
) -> MessageResponse:
    await auth_service.change_password(db, user, payload.current_password, payload.new_password)
    return MessageResponse(
        message="Password updated. All sessions have been revoked; please sign in again."
    )


@router.delete(
    "/me",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete the current account",
    description=(
        "Permanently deletes the account and cascades to owned projects, tasks, and "
        "memberships. Requires the account password in the request body."
    ),
)
async def delete_current_user(
    db: DbSession,
    user: CurrentUser,
    payload: AccountDeleteRequest = Body(...),
) -> MessageResponse:
    if not payload.confirm:
        raise BadRequestError("Set 'confirm' to true to delete the account", code="not_confirmed")
    if not verify_password(payload.password, user.hashed_password):
        raise BadRequestError("Password is incorrect", code="invalid_password")
    await user_service.delete_account(db, user)
    return MessageResponse(message="Account deleted")
