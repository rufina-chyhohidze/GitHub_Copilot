from shop.auth import authenticate


class Routes:
    def __init__(self, users, orders, verify_token):
        self.users = users
        self.orders = orders
        self.verify_token = verify_token

    def post_users(self, payload):
        return self.users.create_user(payload["id"], payload["email"])

    def post_orders(self, token, payload):
        user_id = authenticate(token, self.verify_token)
        return self.orders.create(user_id, payload["total"])
