import { describe, expect, it } from 'vitest'
import { remoteScopeLabel, workModeLabel } from './locationLabels'

describe('location labels', () => {
  it('renders the work mode as prose', () => {
    expect(workModeLabel('hybrid')).toBe('Hybrid')
    expect(workModeLabel('unknown')).toBe('Unknown')
  })

  it('renders the remote scope as prose, including not applicable', () => {
    expect(remoteScopeLabel('not_applicable')).toBe('Not applicable')
    expect(remoteScopeLabel('indonesia_only')).toBe('Indonesia only')
  })

  it('keeps an unrecognised value visible without underscores', () => {
    expect(remoteScopeLabel('asean_region')).toBe('asean region')
  })
})
