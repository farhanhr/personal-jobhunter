/**
 * Date and time display helpers.
 *
 * Two different kinds of value arrive from the API and they must not be
 * treated alike:
 *
 * - **Instants** (`*_at_utc`) are absolute points in time. They are rendered in
 *   `Asia/Jakarta`, the app's operational timezone.
 * - **Calendar dates** (`as_of_date_jkt`, window boundaries, `operational_day`)
 *   are already Jakarta-local dates. They are parsed as UTC midnight and
 *   formatted as UTC so the calendar date is never shifted by the browser's
 *   own timezone.
 *
 * Nothing here computes "today", a window boundary, or a quota. The backend
 * supplies every one of those values.
 */

const JAKARTA = 'Asia/Jakarta'

const instantFormatter = new Intl.DateTimeFormat('en-GB', {
  timeZone: JAKARTA,
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  timeZoneName: 'short',
})

const dateFormatter = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'UTC',
  day: 'numeric',
  month: 'short',
  year: 'numeric',
})

const dayMonthFormatter = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'UTC',
  day: 'numeric',
  month: 'short',
})

/** An absolute instant, rendered in Jakarta time. */
export function formatJakartaInstant(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return value
  }

  return instantFormatter.format(parsed)
}

/** A Jakarta calendar date (`YYYY-MM-DD`), rendered without shifting it. */
export function formatJakartaDate(value: string): string {
  const parsed = calendarDate(value)
  if (parsed === null) {
    return value
  }

  return dateFormatter.format(parsed)
}

/**
 * An inclusive calendar window, collapsed when both ends fall in the same
 * month and year: "12–18 Nov 2026", otherwise "28 Oct – 3 Nov 2026".
 */
export function formatDateWindow(start: string, end: string): string {
  const startDate = calendarDate(start)
  const endDate = calendarDate(end)

  if (startDate === null || endDate === null) {
    return `${start} – ${end}`
  }

  const sameMonth =
    startDate.getUTCFullYear() === endDate.getUTCFullYear() &&
    startDate.getUTCMonth() === endDate.getUTCMonth()

  if (sameMonth) {
    return `${dayMonthFormatter.format(startDate)} – ${dateFormatter.format(endDate)}`
  }

  return `${dateFormatter.format(startDate)} – ${dateFormatter.format(endDate)}`
}

function calendarDate(value: string): Date | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return null
  }

  const parsed = new Date(`${value}T00:00:00Z`)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}
