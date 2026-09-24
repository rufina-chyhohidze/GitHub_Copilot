from shop.models import Order, User


class UserService:
    def __init__(self, repository):
        self.repository = repository

    def create_user(self, user_id, email):
        user = User(id=user_id, email=email)
        return self.repository.save(user)

    def change_language(self, user_id, language):
        user = self.repository.users[user_id]
        user.preferred_language = language
        return self.repository.save(user)


class OrderService:
    def __init__(self, repository):
        self.repository = repository

    def create(self, user_id, total):
        if total <= 0:
            raise ValueError("Total must be positive")
        return self.repository.save(Order(user_id=user_id, total=total))
