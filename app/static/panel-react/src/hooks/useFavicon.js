import { useEffect } from 'react'

export function useFavicon(theme) {
  useEffect(() => {
    // Get all favicon link elements
    const favicon96Link = document.querySelector('link[sizes="96x96"]')
    
    if (favicon96Link) {
      // Update the favicon based on theme
      if (theme === 'dark') {
        favicon96Link.href = '/panel/favicon-96x96-dark.png'
      } else {
        favicon96Link.href = '/panel/favicon-96x96.png'
      }
    }
  }, [theme])
}
