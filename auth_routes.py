from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

import jwt
from flask import Blueprint, jsonify, request
from sqlalchemy import select

from db import Subscription, User, UserPreference, password_hash, session_scope
from profile_store import normalize_profile_items, preference_profile_payload


auth_bp = Blueprint("auth", __name__)
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-only-change-me")
JWT_TTL_HOURS = int(os.environ.get("JWT_TTL_HOURS", "168"))
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")


def ensure_subscription(session, user_id):
    sub = session.scalar(select(Subscription).where(Subscription.user_id == user_id))
    if not sub:
        sub = Subscription(user_id=user_id, plan="free", status="active")
        session.add(sub)
        session.flush()
    return sub


def get_subscription_payload(user_id):
    with session_scope() as session:
        return subscription_payload(ensure_subscription(session, user_id))


def subscription_payload(sub):
    return {
        "plan": sub.plan,
        "status": sub.status,
        "startedAt": sub.started_at.isoformat() if sub.started_at else "",
        "expiresAt": sub.expires_at.isoformat() if sub.expires_at else "",
    }


def user_payload(user, subscription=None):
    subscription = subscription or (
        get_subscription_payload(user.id) if user and getattr(user, "id", None) else {"plan": "free", "status": "active"}
    )
    return {
        "id": user.id,
        "email": user.email,
        "displayName": user.display_name,
        "role": user.role,
        "status": user.status,
        "subscription": subscription,
    }


def issue_token(user):
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=JWT_TTL_HOURS)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def current_user_from_request():
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    token = header.removeprefix("Bearer ").strip()
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        user_id = int(payload.get("sub", 0))
    except Exception:
        return None
    with session_scope() as session:
        return session.get(User, user_id)


def current_plan():
    user = current_user_from_request()
    if not user:
        return "free"
    sub = get_subscription_payload(user.id)
    return sub.get("plan", "free") if sub.get("status") == "active" else "free"


def is_admin_request():
    user = current_user_from_request()
    if user and user.role == "admin":
        return True
    token = request.headers.get("X-Admin-Token", "")
    return bool(ADMIN_TOKEN and token == ADMIN_TOKEN)


@auth_bp.route("/api/auth/register", methods=["POST"])
def auth_register():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    display_name = str(data.get("displayName") or data.get("display_name") or "").strip()
    if not email or "@" not in email:
        return jsonify({"error": "Invalid email"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400

    with session_scope() as session:
        existing = session.scalar(select(User).where(User.email == email))
        if existing:
            return jsonify({"error": "Email already registered"}), 409
        user = User(email=email, password_hash=password_hash(password), display_name=display_name or email.split("@")[0])
        session.add(user)
        session.flush()
        session.add(UserPreference(user_id=user.id))
        sub = Subscription(user_id=user.id, plan="free", status="active")
        session.add(sub)
        token = issue_token(user)
        return jsonify({"token": token, "user": user_payload(user, subscription_payload(sub))}), 201


@auth_bp.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    with session_scope() as session:
        user = session.scalar(select(User).where(User.email == email))
        if not user or not user.verify_password(password):
            return jsonify({"error": "Invalid email or password"}), 401
        if user.status != "active":
            return jsonify({"error": "User is not active"}), 403
        user.last_login_at = datetime.now(timezone.utc)
        sub = ensure_subscription(session, user.id)
        token = issue_token(user)
        return jsonify({"token": token, "user": user_payload(user, subscription_payload(sub))})


@auth_bp.route("/api/auth/me")
def auth_me():
    user = current_user_from_request()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify({"user": user_payload(user)})


@auth_bp.route("/api/account/subscription")
def account_subscription():
    user = current_user_from_request()
    if not user:
        return jsonify({"subscription": {"plan": "free", "status": "active"}})
    return jsonify({"subscription": get_subscription_payload(user.id)})


@auth_bp.route("/api/account/profile-data", methods=["GET", "POST"])
def account_profile_data():
    user = current_user_from_request()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    with session_scope() as session:
        pref = session.scalar(select(UserPreference).where(UserPreference.user_id == user.id))
        if not pref:
            pref = UserPreference(user_id=user.id)
            session.add(pref)
            session.flush()
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            current = preference_profile_payload(pref)
            pref.watchlist = {
                "favorites": normalize_profile_items(data.get("favorites", current["favorites"]), limit=120),
                "history": normalize_profile_items(data.get("history", current["history"]), limit=120),
            }
            pref.updated_at = datetime.now(timezone.utc)
            session.flush()
        return jsonify({"profile": preference_profile_payload(pref)})


@auth_bp.route("/api/admin/users/<int:user_id>/subscription", methods=["POST"])
def admin_set_subscription(user_id):
    if not is_admin_request():
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json(silent=True) or {}
    plan = str(data.get("plan") or "free").lower()
    status = str(data.get("status") or "active").lower()
    if plan not in {"free", "pro"}:
        return jsonify({"error": "plan must be free or pro"}), 400
    if status not in {"active", "paused", "cancelled"}:
        return jsonify({"error": "invalid status"}), 400
    with session_scope() as session:
        user = session.get(User, user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404
        sub = session.scalar(select(Subscription).where(Subscription.user_id == user_id))
        if not sub:
            sub = Subscription(user_id=user_id)
            session.add(sub)
        sub.plan = plan
        sub.status = status
        sub.updated_at = datetime.now(timezone.utc)
        session.flush()
        return jsonify({"userId": user_id, "subscription": subscription_payload(sub)})
