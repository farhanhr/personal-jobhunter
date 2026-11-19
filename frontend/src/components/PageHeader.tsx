type PageHeaderProps = {
  title: string
  description?: string
}

/**
 * The page title is the only display-sized element on a page. It sits above a
 * single rule; sections below carry their own top rule, so the header never
 * needs a box or a shadow to separate itself.
 */
export function PageHeader({ title, description }: PageHeaderProps) {
  return (
    <header className="page-header">
      <h1 className="page-title">{title}</h1>
      {description ? <p className="page-description">{description}</p> : null}
    </header>
  )
}
