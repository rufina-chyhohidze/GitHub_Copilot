def register_handlers(user_service):
    return {"user.created": user_service.create_user}


def dispatch(handlers, event, *args):
    return handlers[event](*args)
