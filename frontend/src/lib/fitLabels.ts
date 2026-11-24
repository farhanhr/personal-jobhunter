/**
 * Presentation rules for the AI candidate-fit score.
 *
 * The threshold lives here as well as in the backend because the UI must not
 * depend on the backend having set `is_best_fit` for a locally updated score.
 */
export const BEST_FIT_THRESHOLD = 90

export type FitTier = 'best' | 'strong' | 'moderate'

export function fitTier(score: number): FitTier {
  if (score >= BEST_FIT_THRESHOLD) return 'best'
  if (score >= 70) return 'strong'
  return 'moderate'
}

export function fitBadgeText(score: number): string {
  return score >= BEST_FIT_THRESHOLD ? `⭐ Best Fit (${score}%)` : `Fit: ${score}%`
}

export function fitBadgeClass(score: number): string {
  return `fit-badge is-${fitTier(score)}`
}
