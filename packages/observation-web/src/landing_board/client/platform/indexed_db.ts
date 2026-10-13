/**
 * A `KeyValueStore` over IndexedDB, with an in-memory fallback.
 *
 * The binding key is a non-extractable `CryptoKey`, which only IndexedDB (through the structured
 * clone algorithm) can store without exposing its bytes; `localStorage` cannot hold one at all.
 * Private windows and some embedded webviews refuse IndexedDB, so the store falls back to a Map
 * and the session then lasts only until the page closes, which is the safe way to fail.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/client/platform/`. Built by `real_browser.ts`
 *   and handed to `keys.ts` through `Browser.store`.
 *
 * Key invariants:
 *   - One database, one object store; values are stored as given (no JSON round trip).
 *   - Any IndexedDB failure at open time selects the in-memory fallback for the page's life.
 *
 * See Also:
 *   - docs/adr/0049-observation-hive-dashboard-stack.md (the key lives in IndexedDB).
 */
import type { KeyValueStore } from '../browser';

const DATABASE = 'hivemind-observation';
const STORE = 'kv';

// Wraps one IndexedDB request in a promise that settles on its success or error event.
function settle<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function openDatabase(factory: IDBFactory): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = factory.open(DATABASE, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export function memoryStore(): KeyValueStore {
  const values = new Map<string, unknown>();
  return {
    get: (key) => Promise.resolve(values.get(key)),
    put: (key, value) => {
      values.set(key, value);
      return Promise.resolve();
    },
    remove: (key) => {
      values.delete(key);
      return Promise.resolve();
    },
  };
}

export function indexedDbStore(factory: IDBFactory | undefined): KeyValueStore {
  if (!factory) return memoryStore();
  const fallback = memoryStore();
  // Opened lazily and once; a failed open resolves to null and the fallback serves from then on.
  const opened = openDatabase(factory).catch(() => null);
  const run = async <T>(
    mode: IDBTransactionMode,
    action: (store: IDBObjectStore) => IDBRequest<T>,
  ): Promise<T | null> => {
    const database = await opened;
    if (!database) return null;
    return settle(action(database.transaction(STORE, mode).objectStore(STORE)));
  };
  return {
    async get(key) {
      const database = await opened;
      return database ? run('readonly', (store) => store.get(key)) : fallback.get(key);
    },
    async put(key, value) {
      const database = await opened;
      if (!database) return fallback.put(key, value);
      await run('readwrite', (store) => store.put(value, key));
    },
    async remove(key) {
      const database = await opened;
      if (!database) return fallback.remove(key);
      await run('readwrite', (store) => store.delete(key));
    },
  };
}
