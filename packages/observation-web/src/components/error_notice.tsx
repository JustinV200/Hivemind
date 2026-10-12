/**
 * `ErrorNotice`: a refusal shown the way the design says: the code, a sentence, what to do.
 *
 * Any view that shows an error uses this one component, so the code always appears in monospace
 * (so it can be quoted to the operator), the sentence always comes from the client's fixed table
 * and the next step always follows. An error the table does not know shows the Entrance's own
 * one-sentence detail and no invented advice.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/`. Used by the login screen now and by any view
 *   that reports a refusal later.
 *
 * Key invariants:
 *   - The notice is a live `role="alert"` region, so a screen reader hears it when it appears.
 *   - No credential can appear in it: the table's words are fixed and the Entrance's `detail`
 *     never carries one.
 *
 * See Also:
 *   - docs/observation/design.md section 8 for the wording rules.
 *   - src/landing_board/client/errors.ts for the table.
 */
import type { ReactElement } from 'react';
import { describeError } from '../landing_board/client';

export function ErrorNotice(props: { error: unknown }): ReactElement {
  const { code, sentence, action } = describeError(props.error);
  return (
    <div className="notice notice--alarm" role="alert">
      <code>{code}</code>
      <p>{sentence}</p>
      {action ? <p>{action}</p> : null}
    </div>
  );
}
