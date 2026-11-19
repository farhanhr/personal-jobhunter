import {
  TRACKER_FIELDS,
  type OpportunityDetail,
  type TrackerField,
  type TrackerUpdate,
} from '../api/opportunities'

/** The text currently in the form, one entry per tracker field. */
export type TrackerDraft = Record<TrackerField, string>

export function draftFromDetail(detail: OpportunityDetail): TrackerDraft {
  return Object.fromEntries(
    TRACKER_FIELDS.map((field) => [field, detail[field] ?? '']),
  ) as TrackerDraft
}

/**
 * Compute the patch from the draft.
 *
 * A field is included only when its text differs from what the detail response
 * currently reports. That single rule covers every case:
 *
 * - an untouched unknown field compares `''` to `''` and is never sent;
 * - a cleared known field compares `''` to its value and is sent as `null`;
 * - a newly typed value is sent as text, exactly as typed.
 */
export function trackerUpdate(
  draft: TrackerDraft,
  detail: OpportunityDetail,
): TrackerUpdate {
  const update: TrackerUpdate = {}

  for (const field of TRACKER_FIELDS) {
    const current = draft[field]
    const original = detail[field] ?? ''
    if (current === original) {
      continue
    }
    update[field] = current === '' ? null : current
  }

  return update
}
