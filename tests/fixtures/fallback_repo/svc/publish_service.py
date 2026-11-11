# Shaped like a real service that guards an import with a stub fallback.
try:
    from .post_service import get_draft_details, delete_draft
except ImportError:
    def get_draft_details(*args): return None
    def delete_draft(*args): return True, ""


def publish_draft_now(user_email, draft_id):
    draft = get_draft_details(user_email, draft_id)
    if draft:
        delete_draft(user_email, draft_id)
    return draft
