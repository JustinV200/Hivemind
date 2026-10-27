/**
 * `TaskList`: a short list of task ids with their status, used for "recently finished" and
 * "up next".
 *
 * The Entrance sends only ids for these two lists (newest finished first; ready or assigned tasks,
 * oldest first). When the id is also a node of the graph, the row adds its status word and
 * placement, and links to the bee or Cell the way the graph node does; an id the graph does not
 * hold is shown as the bare id token.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/attendant/lists/`. Drawn twice by `attendant.tsx`.
 *
 * Key invariants:
 *   - The order is the Entrance's; the list never re-sorts.
 *   - Only the ids and the graph's own fields are shown: no title or outcome is guessed.
 *
 * See Also:
 *   - docs/observation/design.md section 5.5.
 */
import type { ReactElement } from 'react';
import { IdToken } from '../../../components';
import { hrefOfNode, placementOf, statusWord } from '../graph';
import type { TaskNode } from '../graph';

function Item(props: { id: string; node: TaskNode | undefined }): ReactElement {
  const { id, node } = props;
  const href = node ? hrefOfNode(node) : null;
  const token = <IdToken id={id} />;
  return (
    <li className="att-row">
      {href ? <a href={href}>{token}</a> : token}
      {node ? (
        <span className="att-muted">{`${statusWord(node.status)}, ${placementOf(node)}`}</span>
      ) : null}
    </li>
  );
}

export function TaskList(props: {
  title: string;
  empty: string;
  ids: readonly string[];
  nodes: ReadonlyMap<string, TaskNode>;
}): ReactElement {
  const heading = `att-${props.title.toLowerCase().replace(/\s+/g, '-')}-title`;
  return (
    <section className="att-panel comb-cell" aria-labelledby={heading}>
      <h2 id={heading} className="section-heading">
        {props.title}
      </h2>
      {props.ids.length === 0 ? (
        <p className="att-muted">{props.empty}</p>
      ) : (
        <ul className="att-list">
          {props.ids.map((id) => (
            <Item key={id} id={id} node={props.nodes.get(id)} />
          ))}
        </ul>
      )}
    </section>
  );
}
