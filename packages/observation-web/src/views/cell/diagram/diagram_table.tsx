/**
 * `DiagramTable`: the diagram's numbers as a table only a screen reader sees.
 *
 * The SVG carries its meaning in shapes and short cut labels, so a reader who cannot see it gets
 * the same facts here in full: each bee's role, id, status, task, context and spend, then the four
 * fact boxes. It follows the diagram in the page and is visually hidden by `.sr-only`.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cell/diagram/`. Rendered by `cell_diagram.tsx` right
 *   after the SVG (design.md 4.8).
 *
 * Key invariants:
 *   - Holds exactly what the model holds; nothing is added or rounded here.
 *   - Not hidden from assistive technology: `.sr-only` clips it, it does not remove it.
 *
 * See Also:
 *   - docs/observation/design.md section 4.8.
 */
import type { ReactElement } from 'react';
import type { DiagramModel } from './model';

export function DiagramTable(props: { model: DiagramModel }): ReactElement {
  const { model } = props;
  const bees = model.warden ? [model.warden, ...model.bees] : model.bees;
  return (
    <div className="sr-only">
      <table>
        <caption>{`Bees and facts of Cell ${model.cellId}`}</caption>
        <thead>
          <tr>
            <th scope="col">Bee</th>
            <th scope="col">Detail</th>
          </tr>
        </thead>
        <tbody>
          {bees.map((bee) => (
            <tr key={`bee:${bee.id}`}>
              <th scope="row">{bee.id}</th>
              <td>{bee.title}</td>
            </tr>
          ))}
          {model.boxes.map((box) => (
            <tr key={`box:${box.key}`}>
              <th scope="row">{box.label}</th>
              <td>{box.title}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
