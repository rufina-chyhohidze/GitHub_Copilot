import type { Order } from './store';

export const OrderCard = ({ order }: { order: Order }) => (
  <article data-id={order.id}>{order.total}</article>
);

export default OrderCard;
