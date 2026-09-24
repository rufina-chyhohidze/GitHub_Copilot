"""Illustrative source for retrieval; the evaluation loader never executes it."""

from shop.repository import OrderRepository
from shop.services import OrderService


def test_positive_order_is_saved():
    repository = OrderRepository()
    order = OrderService(repository).create(7, 12.5)
    assert repository.orders == [order]
