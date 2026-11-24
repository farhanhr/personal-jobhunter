import { useState } from 'react'
import {
  analyzeFit,
  type OpportunityDetail,
  type OpportunityFitResult,
} from '../api/opportunities'
import { formatJakartaInstant } from '../lib/datetime'
import { fitBadgeClass, fitBadgeText, fitTier } from '../lib/fitLabels'
import { TailoredCVModal } from './TailoredCVModal'

type AnalyzeState =
  | { status: 'idle' }
  | { status: 'analyzing' }
  | { status: 'error'; message: string }

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Unknown fit analysis error'
}

/**
 * The candidate-fit card on the opportunity detail page.
 *
 * Nothing is evaluated automatically: the button is the only thing that spends
 * the user's tokens, and the backend caches the answer so a revisit is free.
 */
export function FitAnalysisSection({ detail }: { detail: OpportunityDetail }) {
  const [result, setResult] = useState<OpportunityFitResult | null>(null)
  const [state, setState] = useState<AnalyzeState>({ status: 'idle' })
  const [isTailorOpen, setIsTailorOpen] = useState(false)

  const score = result?.candidate_fit_score ?? detail.candidate_fit_score
  const summary = result?.summary ?? detail.candidate_fit_summary
  const evaluatedAt = result?.evaluated_at_utc ?? detail.candidate_fit_evaluated_at
  const isAnalyzing = state.status === 'analyzing'

  async function runAnalysis() {
    setState({ status: 'analyzing' })
    try {
      const analysis = await analyzeFit(detail.opportunity_id)
      setResult(analysis)
      setState({ status: 'idle' })
    } catch (error: unknown) {
      setState({ status: 'error', message: errorMessage(error) })
    }
  }

  return (
    <section className="settings-panel" aria-labelledby="fit-heading">
      <div className="settings-heading">
        <h2 id="fit-heading">Candidate fit</h2>
        <p>
          An AI comparison between this job and your master CV. It is separate
          from the rule-based role match, and it runs only when you ask.
        </p>
      </div>

      {score === null && (
        <p className="settings-empty">
          Not analyzed yet. Run an analysis to see how well this job matches your
          saved CV.
        </p>
      )}

      {score !== null && (
        <div className="fit-result">
          <p className="fit-result-head">
            <span className={fitBadgeClass(score)}>{fitBadgeText(score)}</span>
            {summary !== null && (
              <span className="fit-verdict">{summary.fit_verdict}</span>
            )}
          </p>

          <div
            className={`fit-gauge is-${fitTier(score)}`}
            role="img"
            aria-label={`Candidate fit ${score} out of 100`}
          >
            <span className="fit-gauge-fill" style={{ width: `${score}%` }} />
          </div>

          {summary !== null && (
            <>
              <div className="fit-skills">
                <div>
                  <h3>Matching skills</h3>
                  {summary.matching_skills.length === 0 ? (
                    <p className="field-hint">None identified.</p>
                  ) : (
                    <ul className="fit-chips">
                      {summary.matching_skills.map((skill) => (
                        <li className="chip is-match" key={`match-${skill}`}>
                          {skill}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <div>
                  <h3>Missing skills</h3>
                  {summary.missing_skills.length === 0 ? (
                    <p className="field-hint">None identified.</p>
                  ) : (
                    <ul className="fit-chips">
                      {summary.missing_skills.map((skill) => (
                        <li className="chip is-gap" key={`gap-${skill}`}>
                          {skill}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>

              <p className="fit-rationale">{summary.rationale}</p>
            </>
          )}

          {evaluatedAt !== null && (
            <p className="field-hint">
              Evaluated {formatJakartaInstant(evaluatedAt)}.
            </p>
          )}
        </div>
      )}

      <div className="settings-actions">
        <button
          type="button"
          className="button-primary"
          onClick={() => void runAnalysis()}
          disabled={isAnalyzing}
        >
          {isAnalyzing
            ? 'Analyzing…'
            : score === null
              ? 'Analyze Fit'
              : 'Re-analyze Fit'}
        </button>
        <button
          type="button"
          className="button-secondary"
          onClick={() => setIsTailorOpen(true)}
        >
          Tailor CV
        </button>
      </div>

      {isTailorOpen && (
        <TailoredCVModal
          opportunityId={detail.opportunity_id}
          onClose={() => setIsTailorOpen(false)}
        />
      )}

      {state.status === 'error' && (
        <div className="settings-alert settings-alert-error" role="alert">
          <h3>Could not analyze the fit</h3>
          <p>{state.message}</p>
        </div>
      )}
    </section>
  )
}
