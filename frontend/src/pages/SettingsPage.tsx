import { CompanySources } from '../components/CompanySources'
import { PageHeader } from '../components/PageHeader'
import { TargetRolesSettings } from '../components/TargetRolesSettings'

export function SettingsPage() {
  return (
    <>
      <PageHeader
        title="Settings"
        description="Target roles decide what a search looks for. Company sources decide where it looks. Both are stored locally."
      />
      <TargetRolesSettings />
      <CompanySources />
    </>
  )
}
