def import_user(user_service, row):
    return user_service.create_user(row["id"], row["email"])
