import { describe, expect, it } from 'vitest'
import { fitBadgeClass, fitBadgeText, fitTier } from './fitLabels'

describe('fit labels', () => {
  it('marks 90 and above as Best Fit', () => {
    expect(fitBadgeText(90)).toBe('⭐ Best Fit (90%)')
    expect(fitBadgeText(97)).toBe('⭐ Best Fit (97%)')
    expect(fitBadgeClass(97)).toBe('fit-badge is-best')
  })

  it('uses a standard badge between 70 and 89', () => {
    expect(fitBadgeText(78)).toBe('Fit: 78%')
    expect(fitBadgeClass(78)).toBe('fit-badge is-strong')
  })

  it('uses a muted badge below 70', () => {
    expect(fitBadgeText(55)).toBe('Fit: 55%')
    expect(fitBadgeClass(55)).toBe('fit-badge is-moderate')
  })

  it('classifies the tiers consistently', () => {
    expect(fitTier(90)).toBe('best')
    expect(fitTier(89)).toBe('strong')
    expect(fitTier(70)).toBe('strong')
    expect(fitTier(69)).toBe('moderate')
    expect(fitTier(0)).toBe('moderate')
  })
})
