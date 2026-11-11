import functools

import svc.post_service as post_service

get_draft_details = getattr(post_service, "get_draft_details", lambda *a, **k: None)


def login_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        return f(*args, **kwargs)

    return decorated_function


@login_required
def content_details(item_id):
    return get_draft_details("me", item_id)
