import Store from './store';
import { publish as notify } from './events';

const store = new Store();

export const create = (order) => {
  const saved = store.save(order);
  notify(saved);
  return saved;
};

export { create as submit };
export * as events from './events';
