from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from supabase import Client, create_client

from app.core.config import settings

# Does not auto-raise on a missing header so we can return our own 401 with a
# clear message instead of FastAPI's generic "Not authenticated".
_bearer_scheme = HTTPBearer(auto_error=False)

_admin_client: Client | None = None


@dataclass
class CurrentUser:
    id: str
    email: str | None
    role: str
    company_name: str | None


def _get_admin_client() -> Client:
    global _admin_client
    if not settings.has_supabase:
        raise HTTPException(
            status_code=500,
            detail="Supabase credentials are not configured on the backend.",
        )
    if _admin_client is None:
        _admin_client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    return _admin_client


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> CurrentUser:
    """Verifies the Supabase access token sent by the frontend and loads the
    caller's role/profile via the service-role client (bypassing RLS is safe
    here because we've already proven who the caller is)."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token.")

    admin = _get_admin_client()

    try:
        auth_response = admin.auth.get_user(credentials.credentials)
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token."
        ) from error

    user = getattr(auth_response, "user", None)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token.")

    role_row = (
        admin.table("user_roles").select("role").eq("user_id", user.id).limit(1).maybe_single().execute()
    )
    # .maybe_single().execute() returns None outright (not a response with
    # .data = None) when no row matches, so `role_row` itself may be None here.
    role = ((role_row.data if role_row else None) or {}).get("role", "user")

    profile_row = (
        admin.table("profiles").select("company_name").eq("id", user.id).maybe_single().execute()
    )
    company_name = ((profile_row.data if profile_row else None) or {}).get("company_name")

    return CurrentUser(id=user.id, email=user.email, role=role, company_name=company_name)


async def require_super_admin(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if current_user.role != "super_admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required.")
    return current_user


async def require_builder(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Builder workspace routes. The builder's own profile id is the tenant id."""
    if current_user.role not in ("admin", "super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Builder access required.")
    return current_user


def builder_has_feature(builder_id: str, feature_key: str) -> bool:
    """Effective per-builder feature flag (super admin → Feature Access).
    Fails open if the flags table/function isn't there yet, so a missing
    migration never takes a feature down for everyone."""
    try:
        result = _get_admin_client().rpc(
            "builder_has_feature", {"_builder_id": builder_id, "_feature_key": feature_key}
        ).execute()
    except HTTPException:
        raise
    except Exception as error:
        print(f"Feature flag lookup failed for {feature_key}: {error}")
        return True
    return result.data is not False


def require_feature(feature_key: str):
    """Dependency: builder route that is only available when the super admin
    has enabled `feature_key` for this builder. Super admins always pass."""

    async def dependency(current_user: CurrentUser = Depends(require_builder)) -> CurrentUser:
        if current_user.role != "super_admin" and not builder_has_feature(current_user.id, feature_key):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This feature is not enabled for your workspace.",
            )
        return current_user

    return dependency
