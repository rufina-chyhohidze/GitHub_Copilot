export interface Order {
  id: string;
  total: number;
}

export type OrderId = string;
export enum State { Pending, Saved }

export default class Store {
  private orders = new Map<OrderId, Order>();

  save(order: Order): Order {
    if (order.total <= 0) throw new Error("positive total required");
    this.orders.set(order.id, order);
    return order;
  }

  find = (id: OrderId): Order | undefined => this.orders.get(id);
}
