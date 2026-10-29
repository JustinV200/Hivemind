/**
 * `ErrorNotice`: a refusal shown the way the design says: a sentence, then what to do.
 *
 * Any view that shows an error uses this one component, so the sentence always comes from the
 * client's fixed table and the next step always follows, as a link when the table says where to
 * go (back to the Fleet, to the Devices). The raw code is not shown to the human;
 * it rides on the alert as `data-code` (and the thrown error's message), so devtools and logs
 * still have it. An error the table does not know shows the Entrance's own
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
  const { code, sentence, action, href } = describeError(props.error);
  return (
    <div className="notice notice--alarm" role="alert" data-code={code}>
      <p>{sentence}</p>
      {/* An action that names a place to go is the link there, not a sentence about it. */}
      {action ? <p>{href ? <a href={href}>{action}</a> : action}</p> : null}
    </div>
  );
}
