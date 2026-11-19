import { NavLink, Outlet } from 'react-router'

const APP_NAME = 'Personal Job Finder'
const APP_VERSION = __APP_VERSION__

const NAV_ITEMS = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/opportunities', label: 'Opportunities', end: false },
  { to: '/settings', label: 'Settings', end: false },
]

/**
 * The rail is the ledger margin: navigation, wordmark, version. It holds no
 * status and no counts, so nothing here can drift out of date.
 */
export function AppShell() {
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>

      <aside className="app-rail">
        <p className="rail-wordmark">{APP_NAME}</p>

        <nav className="rail-nav" aria-label="Main">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                isActive ? 'rail-link rail-link-active' : 'rail-link'
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        <p className="rail-foot">Version {APP_VERSION}</p>
      </aside>

      <main id="main-content" className="app-main">
        <Outlet />
      </main>
    </div>
  )
}
