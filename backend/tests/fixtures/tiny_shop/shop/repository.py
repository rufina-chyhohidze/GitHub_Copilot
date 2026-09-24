from shop.models import Order, User


class UserRepository:
    def __init__(self):
        self.users = {}

    def save(self, user: User):
        self.users[user.id] = user
        return user


class OrderRepository:
    def __init__(self):
        self.orders = []

    def save(self, order: Order):
        self.orders.append(order)
        return order
