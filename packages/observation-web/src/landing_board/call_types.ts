/**
 * Type-level helpers that read a request's and response's shape out of the generated types.
 *
 * The Landing Board contract is the OpenAPI document, and `types.ts` is generated from it. This
 * file is the only bridge between that generated shape and the HTTP wrapper: given a method and a
 * path it works out the path parameters, the query, the JSON body and the success body. Keeping
 * the bridge as pure types means no Landing Board shape is ever written by hand, and a contract
 * change surfaces as a type error in whichever caller used the changed route.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/landing_board/`. Imported by `client/http.ts` and by callers
 *   that name a response type. Holds no runtime code.
 *
 * Key invariants:
 *   - Only `get`, `post` and `delete` are modelled: they are the only methods the Landing Board
 *     document uses.
 *   - A route that takes no path parameter, query or body makes that member `undefined`, so a call
 *     cannot pass one the document does not declare.
 *
 * See Also:
 *   - src/landing_board/types.ts, generated and never edited.
 *   - src/landing_board/client/http.ts, which implements `call` over these types.
 */
import type { paths } from './types';

export type Method = 'get' | 'post' | 'delete';

// A path is offered for a method only when the document declares that operation on it.
export type PathsWith<M extends Method> = {
  [P in keyof paths]: paths[P] extends Record<M, object> ? P : never;
}[keyof paths];

export type OperationOf<P extends keyof paths, M extends Method> =
  paths[P] extends Record<M, infer O> ? O : never;

type JsonOf<C> = C extends { 'application/json': infer B } ? B : never;
type Declared<K extends string, V> = [V] extends [never]
  ? { [P in K]?: undefined }
  : { [P in K]: V };

export type PathParamsOf<O> = O extends { parameters: { path: infer X } } ? X : never;
export type QueryOf<O> = O extends { parameters: { query?: infer Q } } ? NonNullable<Q> : never;
export type BodyOf<O> = O extends { requestBody: { content: infer C } } ? JsonOf<C> : never;

// What a caller may pass: only the members the operation declares, and required ones required.
export type CallInit<O> = Declared<'params', PathParamsOf<O>> &
  Declared<'body', BodyOf<O>> &
  ([QueryOf<O>] extends [never] ? { query?: undefined } : { query?: QueryOf<O> });

// The init argument is optional exactly when nothing in it is required.
export type InitArgs<O> = object extends CallInit<O> ? [init?: CallInit<O>] : [init: CallInit<O>];

type SuccessStatus = 200 | 201 | 202 | 204;

// The JSON a successful answer carries, or undefined for an answer with no body (204).
export type ResponseOf<O> = O extends { responses: infer R }
  ? {
      [S in keyof R & SuccessStatus]: R[S] extends { content: infer C } ? JsonOf<C> : undefined;
    }[keyof R & SuccessStatus]
  : never;
