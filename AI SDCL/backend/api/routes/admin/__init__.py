"""
backend/api/routes/admin/__init__.py

Re-exports the admin router so the import in main.py stays as:
    from backend.api.routes.admin import router as admin_router
"""
from backend.api.routes.admin.router import router

__all__ = ["router"]
