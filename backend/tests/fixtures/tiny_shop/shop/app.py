from shop.repository import OrderRepository, UserRepository
from shop.routes import Routes
from shop.services import OrderService, UserService


def build_routes(verify_token):
    users = UserService(UserRepository())
    orders = OrderService(OrderRepository())
    return Routes(users, orders, verify_token)


ROUTES = {"POST /users": "post_users", "POST /orders": "post_orders"}
