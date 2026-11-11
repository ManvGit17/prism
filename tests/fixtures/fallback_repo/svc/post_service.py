def get_draft_details(user_email, draft_id):
    return {"id": draft_id, "user": user_email}


def delete_draft(user_email, draft_id):
    return True, ""
